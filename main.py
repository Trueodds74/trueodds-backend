"""TrueValue Odds backend, version 3.

What this file does (and nothing more):
  * stores fixtures, hand-entered South African bookmaker prices, and a log of
    picks in the database
  * asks the TESTED modules (analytics.py, picks.py, service.py) for the maths
    and the rules
  * shows the public: today's PUBLISHED picks and the track record
  * protects every write with an admin key

The public never sees a live-recalculated list. Picks appear only after you
call POST /admin/publish, and from then on they are frozen (odds, probability
and edge are saved at that moment). That is what makes the track record honest.
"""
import hmac
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Generator, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Integer, String, UniqueConstraint,
    create_engine, text,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from picks import MAX_DAILY_PICKS, Candidate, devig_proportional
from service import (
    SETTLED_RESULTS, model_probabilities, pick_card, select_for_window,
    track_record, validate_odds,
)

logger = logging.getLogger("trueodds")

VERSION = "3.0.0"
SA_UTC_OFFSET_HOURS = 2  # South Africa has no daylight saving: always UTC+2

# Average goals per team per match in the league (home side, away side).
# Override per fixture in the request. APPROXIMATE Premier League figures:
# replace them with this season's real averages before trusting any pick.
LEAGUE_NAME = "Premier League"
DEFAULT_LEAGUE_HOME_AVG = 1.50
DEFAULT_LEAGUE_AWAY_AVG = 1.25

app = FastAPI(
    title="TrueValue Odds Backend",
    version=VERSION,
    description="Fair-odds model vs South African bookmaker prices. 18+. Gamble responsibly.",
)

# ---------------------------------------------------------------- database
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./test.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

ADMIN_KEY = os.environ.get("ADMIN_KEY")  # set this in Railway Variables

if "postgresql" in DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=10,
                           pool_timeout=30, pool_recycle=1800, pool_pre_ping=True)
else:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})

Base = declarative_base()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Fixture(Base):
    __tablename__ = "fixtures_v3"
    id = Column(Integer, primary_key=True, index=True)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    kickoff = Column(DateTime, nullable=False, index=True)  # naive UTC
    league = Column(String, default=LEAGUE_NAME)
    p_home = Column(Float)
    p_draw = Column(Float)
    p_away = Column(Float)
    is_mock = Column(Boolean, default=False, index=True)


class Price(Base):
    """The latest price one bookmaker offers on one fixture (typed by hand)."""
    __tablename__ = "prices_v3"
    __table_args__ = (UniqueConstraint("fixture_id", "bookmaker", name="one_price_per_book"),)
    id = Column(Integer, primary_key=True)
    fixture_id = Column(Integer, index=True, nullable=False)
    bookmaker = Column(String, nullable=False)
    home_odds = Column(Float)
    draw_odds = Column(Float)
    away_odds = Column(Float)
    updated_at = Column(DateTime, nullable=False)  # naive UTC


class PickLog(Base):
    """Every pick the system decided on, frozen at that moment.

    status: published | qualified_not_published | held_for_review
    Only status == "published" (and not mock) counts in the public record.
    """
    __tablename__ = "pick_log_v3"
    id = Column(Integer, primary_key=True)
    pick_date = Column(Date, index=True, nullable=False)  # South African date
    fixture_id = Column(Integer, index=True, nullable=False)
    match = Column(String, nullable=False)
    kickoff = Column(DateTime)
    outcome = Column(String, nullable=False)
    bookmaker = Column(String)
    odds = Column(Float, nullable=False)
    model_prob = Column(Float, nullable=False)
    edge = Column(Float, nullable=False)
    status = Column(String, nullable=False, index=True)
    is_mock = Column(Boolean, default=False, index=True)
    result = Column(String)               # won | lost | void | NULL
    closing_fair_prob = Column(Float)     # de-vigged closing probability, optional
    created_at = Column(DateTime, nullable=False)


Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ------------------------------------------------------------------ helpers
def utcnow() -> datetime:
    """Naive UTC, the same convention the database uses."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sa_today(now: datetime) -> date:
    return (now + timedelta(hours=SA_UTC_OFFSET_HOURS)).date()


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt  # no timezone given: treated as UTC


def require_admin(x_admin_key: Optional[str] = Header(default=None)) -> None:
    if not ADMIN_KEY:
        raise HTTPException(status_code=503, detail="Admin is disabled: ADMIN_KEY is not set.")
    supplied = (x_admin_key or "").encode()
    if not hmac.compare_digest(supplied, ADMIN_KEY.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin key.")


def load_prices(db: Session, fixture_ids: List[int]) -> Dict[int, List[Price]]:
    grouped: Dict[int, List[Price]] = {}
    if not fixture_ids:
        return grouped
    for row in db.query(Price).filter(Price.fixture_id.in_(fixture_ids)).all():
        grouped.setdefault(row.fixture_id, []).append(row)
    return grouped


def live_selection(db: Session, now: datetime, include_mock: bool, hours: float = 24):
    q = db.query(Fixture).filter(Fixture.kickoff > now)
    # Real and mock fixtures are never mixed: one or the other.
    q = q.filter(Fixture.is_mock.is_(include_mock))
    fixtures = q.all()
    prices = load_prices(db, [f.id for f in fixtures])
    return select_for_window(fixtures, prices, now, hours=hours)


def log_to_candidate(row: PickLog) -> Candidate:
    return Candidate(row.fixture_id, row.match, row.outcome,
                     row.model_prob, row.odds, row.kickoff)


def card_from_log(row: PickLog) -> dict:
    card = pick_card(log_to_candidate(row), row.bookmaker)
    card["pick_id"] = row.id
    card["result"] = row.result
    return card


# -------------------------------------------------------------- request bodies
class HomeStats(BaseModel):
    avg_goals_scored_home: float = Field(..., ge=0, le=6)
    avg_goals_conceded_home: float = Field(..., ge=0, le=6)


class AwayStats(BaseModel):
    avg_goals_scored_away: float = Field(..., ge=0, le=6)
    avg_goals_conceded_away: float = Field(..., ge=0, le=6)


class FixtureIn(BaseModel):
    home_team: str = Field(..., min_length=1, max_length=80)
    away_team: str = Field(..., min_length=1, max_length=80)
    kickoff_utc: datetime
    league: str = LEAGUE_NAME
    home_stats: HomeStats
    away_stats: AwayStats
    league_avg_home_scored: float = Field(DEFAULT_LEAGUE_HOME_AVG, gt=0, le=6)
    league_avg_away_scored: float = Field(DEFAULT_LEAGUE_AWAY_AVG, gt=0, le=6)


class PriceIn(BaseModel):
    bookmaker: str = Field(..., min_length=1, max_length=40)
    home_odds: Optional[float] = None
    draw_odds: Optional[float] = None
    away_odds: Optional[float] = None


class ResultIn(BaseModel):
    result: str  # won | lost | void
    # Optional: the sharp (e.g. Pinnacle) closing 1X2 odds, used for CLV.
    closing_home_odds: Optional[float] = None
    closing_draw_odds: Optional[float] = None
    closing_away_odds: Optional[float] = None


def add_fixture(db: Session, body: FixtureIn, is_mock: bool = False) -> Fixture:
    probs = model_probabilities(
        body.home_stats.model_dump() if hasattr(body.home_stats, "model_dump") else body.home_stats.dict(),
        body.away_stats.model_dump() if hasattr(body.away_stats, "model_dump") else body.away_stats.dict(),
        {"league_avg_home_scored": body.league_avg_home_scored,
         "league_avg_away_scored": body.league_avg_away_scored},
    )
    fx = Fixture(
        home_team=body.home_team.strip(), away_team=body.away_team.strip(),
        kickoff=to_naive_utc(body.kickoff_utc), league=body.league, is_mock=is_mock,
        p_home=probs["Home Win"], p_draw=probs["Draw"], p_away=probs["Away Win"],
    )
    db.add(fx)
    db.flush()
    return fx


def upsert_price(db: Session, fixture_id: int, bookmaker: str,
                 home: Optional[float], draw: Optional[float], away: Optional[float],
                 now: datetime) -> Price:
    row = (db.query(Price)
           .filter(Price.fixture_id == fixture_id, Price.bookmaker == bookmaker).first())
    if row is None:
        row = Price(fixture_id=fixture_id, bookmaker=bookmaker)
        db.add(row)
    row.home_odds, row.draw_odds, row.away_odds = home, draw, away
    row.updated_at = now
    return row


# ------------------------------------------------------------ public endpoints
@app.get("/")
def read_root():
    return {"message": "TrueValue Odds backend is live", "version": VERSION,
            "notice": "18+ only. Gamble responsibly."}


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        logger.exception("Database check failed")
        raise HTTPException(status_code=503, detail="Database unavailable.")


@app.get("/picks/today")
def picks_today(db: Session = Depends(get_db)):
    """Today's (South African date) published picks. Zero is a valid answer."""
    today = sa_today(utcnow())
    rows = (db.query(PickLog)
            .filter(PickLog.pick_date == today, PickLog.status == "published",
                    PickLog.is_mock.is_(False))
            .order_by(PickLog.edge.desc(), PickLog.id).all())
    return {
        "date": today.isoformat(),
        "count": len(rows),
        "message": "" if rows else "No value today.",
        "picks": [card_from_log(r) for r in rows],
        "disclaimer": "Estimates, not guarantees. 18+ only. Gamble responsibly.",
    }


@app.get("/dashboard/edges")
def dashboard_edges(db: Session = Depends(get_db)):
    """Old endpoint name kept so the existing app keeps working. It now shows
    the same published picks as /picks/today, in the old response shape."""
    data = picks_today(db)
    matches = [{
        "fixture_id": p["fixture_id"],
        "match": p["match"],
        "best_bet": p["pick"],
        "bookmaker_odd": p["bookmaker_odds"],
        "bookmaker": p["bookmaker"],
        "expected_value_ev": f"{p['edge_pct']}%",
        "reason": p["reason"],
    } for p in data["picks"]]
    return {"status": "success", "total_value_opportunities": len(matches),
            "message": data["message"], "matches": matches}


@app.get("/track-record")
def get_track_record(db: Session = Depends(get_db)):
    rows = (db.query(PickLog)
            .filter(PickLog.status == "published", PickLog.is_mock.is_(False))
            .order_by(PickLog.created_at.desc(), PickLog.id.desc()).all())
    recent = [{
        "date": r.pick_date.isoformat(), "match": r.match, "pick": r.outcome,
        "bookmaker": r.bookmaker, "odds": r.odds, "edge_pct": round(r.edge * 100, 1),
        "result": r.result or "pending",
    } for r in rows[:50]]
    return {"summary": track_record(rows), "recent": recent}


# ------------------------------------------------------------ admin endpoints
admin = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], tags=["admin"])


@admin.post("/fixtures")
def admin_add_fixture(body: FixtureIn, db: Session = Depends(get_db)):
    try:
        fx = add_fixture(db, body)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return {"fixture_id": fx.id, "match": f"{fx.home_team} vs {fx.away_team}",
            "model_probabilities_pct": {"home": round(fx.p_home * 100, 1),
                                        "draw": round(fx.p_draw * 100, 1),
                                        "away": round(fx.p_away * 100, 1)}}


@admin.post("/fixtures/{fixture_id}/prices")
def admin_set_prices(fixture_id: int, body: PriceIn, db: Session = Depends(get_db)):
    if db.get(Fixture, fixture_id) is None:
        raise HTTPException(status_code=404, detail="Fixture not found.")
    values = {}
    for name in ("home_odds", "draw_odds", "away_odds"):
        raw = getattr(body, name)
        try:
            values[name] = None if raw is None else validate_odds(raw, name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    if all(v is None for v in values.values()):
        raise HTTPException(status_code=422, detail="Give at least one odds value.")
    upsert_price(db, fixture_id, body.bookmaker.strip(),
                 values["home_odds"], values["draw_odds"], values["away_odds"], utcnow())
    db.commit()
    return {"status": "saved", "fixture_id": fixture_id, "bookmaker": body.bookmaker.strip()}


@admin.get("/status")
def admin_status(db: Session = Depends(get_db)):
    """What this running app can see right now. Use it to check which database
    it is reading and whether the practice matches are still upcoming."""
    now = utcnow()
    upcoming_mock = (db.query(Fixture)
                     .filter(Fixture.is_mock.is_(True), Fixture.kickoff > now)
                     .order_by(Fixture.kickoff).all())
    return {
        "server_time_utc": now.isoformat(),
        "database_type": engine.url.get_backend_name(),
        "fixtures_real": db.query(Fixture).filter(Fixture.is_mock.is_(False)).count(),
        "fixtures_mock": db.query(Fixture).filter(Fixture.is_mock.is_(True)).count(),
        "mock_fixtures_still_upcoming": len(upcoming_mock),
        "next_mock_kickoff_utc": upcoming_mock[0].kickoff.isoformat() if upcoming_mock else None,
        "prices_saved": db.query(Price).count(),
        "picks_logged": db.query(PickLog).count(),
    }


@admin.get("/preview")
def admin_preview(include_mock: bool = False, hours: float = Query(24, gt=0, le=72),
                  db: Session = Depends(get_db)):
    """What WOULD be published right now. Nothing is saved."""
    result, bookmaker_for = live_selection(db, utcnow(), include_mock, hours)

    def cards(cands):
        return [pick_card(c, bookmaker_for.get((c.fixture_id, c.outcome))) for c in cands]

    return {"would_publish": cards(result.published),
            "qualified_but_over_the_cap": cards(result.qualified_not_published),
            "held_for_review_edge_over_15pct": cards(result.held_for_review),
            "message": result.message}


@admin.post("/publish")
def admin_publish(include_mock: bool = False, db: Session = Depends(get_db)):
    """Freeze today's picks into the log. Safe to call more than once: a fixture
    is never published twice and the day's total never goes above the cap."""
    now = utcnow()
    today = sa_today(now)
    result, bookmaker_for = live_selection(db, now, include_mock)
    already_today = (db.query(PickLog)
                     .filter(PickLog.pick_date == today, PickLog.status == "published",
                             PickLog.is_mock.is_(include_mock)).count())
    slots = max(0, MAX_DAILY_PICKS - already_today)

    def seen(fixture_id: int) -> bool:
        return db.query(PickLog).filter(PickLog.fixture_id == fixture_id).first() is not None

    def record(c: Candidate, status: str) -> None:
        db.add(PickLog(
            pick_date=today, fixture_id=c.fixture_id, match=c.match, kickoff=c.kickoff,
            outcome=c.outcome, bookmaker=bookmaker_for.get((c.fixture_id, c.outcome)),
            odds=c.bookmaker_odds, model_prob=c.model_prob, edge=c.edge, status=status,
            is_mock=include_mock, created_at=now))

    published = 0
    for c in result.published:
        if seen(c.fixture_id):
            continue
        if published < slots:
            record(c, "published")
            published += 1
        else:
            record(c, "qualified_not_published")
    for c in result.qualified_not_published:
        if not seen(c.fixture_id):
            record(c, "qualified_not_published")
    for c in result.held_for_review:
        if not seen(c.fixture_id):
            record(c, "held_for_review")
    db.commit()
    return {"published_now": published, "published_today_total": already_today + published,
            "daily_cap": MAX_DAILY_PICKS,
            "held_for_review": len(result.held_for_review)}


@admin.post("/picks/{pick_id}/result")
def admin_set_result(pick_id: int, body: ResultIn, db: Session = Depends(get_db)):
    row = db.get(PickLog, pick_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pick not found.")
    if body.result not in SETTLED_RESULTS:
        raise HTTPException(status_code=422, detail="result must be won, lost or void.")
    row.result = body.result

    closing = [body.closing_home_odds, body.closing_draw_odds, body.closing_away_odds]
    if any(v is not None for v in closing):
        if any(v is None for v in closing):
            raise HTTPException(status_code=422, detail="Give all three closing odds, or none.")
        try:
            probs = devig_proportional(closing)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        index = {"Home Win": 0, "Draw": 1, "Away Win": 2}[row.outcome]
        row.closing_fair_prob = probs[index]
    db.commit()
    return {"status": "saved", "pick_id": row.id, "result": row.result,
            "closing_fair_prob": row.closing_fair_prob}


# Mock data: for testing the pipeline only. Marked is_mock so it can never
# reach the public pages or the track record.
MOCK_MATCHES = [
    ("Kaizer Chiefs", "Orlando Pirates", 0.15, (1.70, 1.10), (1.40, 0.95)),
    ("Mamelodi Sundowns", "Stellenbosch FC", 0.12, (2.30, 0.50), (1.50, 1.10)),
    ("Cape Town City", "SuperSport United", 0.08, (1.45, 1.20), (1.20, 1.30)),
    ("AmaZulu", "Golden Arrows", 0.18, (1.10, 1.40), (1.35, 1.50)),
    ("Sekhukhune United", "Polokwane City", 0.10, (1.30, 1.00), (1.05, 1.25)),
]


def clear_mock(db: Session) -> None:
    ids = [f.id for f in db.query(Fixture).filter(Fixture.is_mock.is_(True)).all()]
    if ids:
        db.query(Price).filter(Price.fixture_id.in_(ids)).delete(synchronize_session=False)
        db.query(Fixture).filter(Fixture.id.in_(ids)).delete(synchronize_session=False)
    db.query(PickLog).filter(PickLog.is_mock.is_(True)).delete(synchronize_session=False)


@admin.post("/mock-data")
def admin_create_mock(db: Session = Depends(get_db)):
    clear_mock(db)
    now = utcnow()
    for i, (home, away, h_edge, hs, aws) in enumerate(MOCK_MATCHES):
        body = FixtureIn(
            home_team=home, away_team=away, kickoff_utc=now + timedelta(hours=3 * (i + 1)),
            league="MOCK",
            home_stats=HomeStats(avg_goals_scored_home=hs[0], avg_goals_conceded_home=hs[1]),
            away_stats=AwayStats(avg_goals_scored_away=aws[0], avg_goals_conceded_away=aws[1]))
        fx = add_fixture(db, body, is_mock=True)
        fair_h, fair_d, fair_a = 1 / fx.p_home, 1 / fx.p_draw, 1 / fx.p_away
        upsert_price(db, fx.id, "Mock Bookie", round(fair_h * (1 + h_edge), 2),
                     round(fair_d * 0.95, 2), round(fair_a * 0.95, 2), now)
    db.commit()
    return {"status": "mock data created", "fixtures": len(MOCK_MATCHES)}


@admin.delete("/mock-data")
def admin_delete_mock(db: Session = Depends(get_db)):
    clear_mock(db)
    db.commit()
    return {"status": "mock data removed"}


app.include_router(admin)
