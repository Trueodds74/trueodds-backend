import os
import math
import requests
from datetime import datetime
from typing import Generator, List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from scipy.stats import poisson

# 1. Setup the App
app = FastAPI(
    title="TrueOdds Backend Engine",
    version="2.0.0",
    description="Optimized sports analytics backend with cached math validation and automated Accumulator engine."
)

# 2. Database URL Setup & Verification
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./test.db")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

engine = create_engine(DATABASE_URL)
Base = declarative_base()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ==========================================
# 3. Database Models (Caching Structure)
# ==========================================
class CachedFixture(Base):
    """
    Saves match information, calculated true odds, and live market odds locally.
    """
    __tablename__ = "cached_fixtures"
    id = Column(Integer, primary_key=True, index=True)  # Matches API-Football ID
    home_team = Column(String, index=True)
    away_team = Column(String, index=True)
    match_date = Column(DateTime)
    league_id = Column(Integer)
    
    # Calculated Poisson Probabilities / True Odds
    true_home_odds = Column(Float, nullable=True)
    true_draw_odds = Column(Float, nullable=True)
    true_away_odds = Column(Float, nullable=True)
    
    # Real-world Bookmaker Odds cached locally
    bookmaker_home_odds = Column(Float, nullable=True)
    bookmaker_draw_odds = Column(Float, nullable=True)
    bookmaker_away_odds = Column(Float, nullable=True)
    
    # Value edge percentage (+EV)
    max_value_edge = Column(Float, default=0.0)


Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==========================================
# 4. Helper Functions: Mathematical Engine
# ==========================================
def calculate_poisson_odds(home_xg: float = 1.50, away_xg: float = 1.20, max_goals: int = 10) -> Dict[str, float]:
    """
    Calculates True Odds using Poisson scoreline matrix distribution.
    """
    p_home, p_draw, p_away = 0.0, 0.0, 0.0

    for h in range(max_goals):
        for a in range(max_goals):
            prob = poisson.pmf(h, home_xg) * poisson.pmf(a, away_xg)
            if h > a:
                p_home += prob
            elif h == a:
                p_draw += prob
            else:
                p_away += prob

    return {
        "true_home": round(1.0 / p_home, 2) if p_home > 0 else 999.0,
        "true_draw": round(1.0 / p_draw, 2) if p_draw > 0 else 999.0,
        "true_away": round(1.0 / p_away, 2) if p_away > 0 else 999.0,
        "prob_home": p_home,
        "prob_draw": p_draw,
        "prob_away": p_away
    }


# ==========================================
# 5. API Endpoints
# ==========================================
@app.get("/")
def read_root():
    return {"message": "TrueOdds Backend is Live and Optimized! 🚀", "version": "2.0.0"}


@app.get("/dashboard/edges")
def get_high_value_edges(min_edge: float = 0.05, db: Session = Depends(get_db)):
    """
    Returns upcoming matches ordered strictly by the highest mathematical (+EV) edge.
    """
    fixtures = db.query(CachedFixture).filter(
        CachedFixture.max_value_edge >= min_edge
    ).order_by(CachedFixture.max_value_edge.desc()).all()
    
    return {
        "status": "success",
        "total_value_opportunities": len(fixtures),
        "matches": [
            {
                "fixture_id": f.id,
                "match": f"{f.home_team} vs {f.away_team}",
                "match_date": f.match_date.isoformat() if f.match_date else None,
                "true_odds": {"home": f.true_home_odds, "draw": f.true_draw_odds, "away": f.true_away_odds},
                "bookmaker_odds": {"home": f.bookmaker_home_odds, "draw": f.bookmaker_draw_odds, "away": f.bookmaker_away_odds},
                "punter_edge_percentage": f"{round(f.max_value_edge * 100, 2)}%"
            } for f in fixtures
        ]
    }


@app.get("/accumulator/smart-slip")
def generate_accumulator_slip(legs: int = 3, db: Session = Depends(get_db)):
    """
    Monetizable Pro Feature: Builds an optimal multi-leg ticket from high (+EV) value selections.
    """
    top_legs = db.query(CachedFixture).filter(
        CachedFixture.max_value_edge >= 0.05,
        CachedFixture.bookmaker_home_odds.isnot(None),
        CachedFixture.true_home_odds.isnot(None)
    ).order_by(CachedFixture.max_value_edge.desc()).limit(legs).all()
    
    if len(top_legs) < 2:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not enough high-value fixtures found in the database to build a multi-bet slip right now."
        )
        
    slip_items = []
    combined_bookmaker_odds = 1.0
    combined_true_odds = 1.0
    
    for match in top_legs:
        # Safe edge computation to avoid zero division
        home_edge = (match.bookmaker_home_odds / match.true_home_odds) - 1 if (match.bookmaker_home_odds and match.true_home_odds) else 0
        away_edge = (match.bookmaker_away_odds / match.true_away_odds) - 1 if (match.bookmaker_away_odds and match.true_away_odds) else 0
        
        if home_edge >= away_edge:
            selection = "Home Win"
            b_odds = match.bookmaker_home_odds
            t_odds = match.true_home_odds
            best_edge = home_edge
        else:
            selection = "Away Win"
            b_odds = match.bookmaker_away_odds
            t_odds = match.true_away_odds
            best_edge = away_edge
        
        combined_bookmaker_odds *= b_odds
        combined_true_odds *= t_odds
        
        slip_items.append({
            "fixture": f"{match.home_team} vs {match.away_team}",
            "selection": selection,
            "bookmaker_odds": b_odds,
            "true_odds": round(t_odds, 2),
            "individual_edge": f"{round(best_edge * 100, 2)}%"
        })
        
    total_slip_edge = (combined_bookmaker_odds / combined_true_odds) - 1 if combined_true_odds > 0 else 0
    
    return {
        "ticket_summary": {
            "total_legs": len(slip_items),
            "combined_bookmaker_odds": round(combined_bookmaker_odds, 2),
            "combined_true_odds": round(combined_true_odds, 2),
            "total_slip_edge": f"{round(total_slip_edge * 100, 2)}%"
        },
        "legs": slip_items
    }


@app.post("/sync/refresh-data")
def sync_external_data(league: str = "39", season: str = "2026", db: Session = Depends(get_db)):
    """
    Cron endpoint: Syncs fixtures from API-Football, live odds from The Odds API,
    and updates True Odds calculations in the database.
    """
    if not API_FOOTBALL_KEY or not ODDS_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="API access keys are unconfigured."
        )

    # A. Fetch Fresh Fixtures
    fixtures_url = f"https://v3.football.api-sports.io/fixtures?league={league}&next=10&season={season}"
    f_res = requests.get(fixtures_url, headers={"x-apisports-key": API_FOOTBALL_KEY}, timeout=10)
    
    if f_res.status_code != 200:
        raise HTTPException(status_code=f_res.status_code, detail="Failed to pull fixtures from API-Football")
        
    fixtures_data = f_res.json().get('response', [])

    # B. Fetch Live Market Odds
    odds_url = f"https://api.the-odds-api.com/v4/sports/soccer_epl/odds/?apiKey={ODDS_API_KEY}&regions=uk,eu&markets=h2h&oddsFormat=decimal"
    o_res = requests.get(odds_url, timeout=10)
    odds_data = o_res.json() if o_res.status_code == 200 else []

    processed_count = 0

    for item in fixtures_data:
        f_id = item['fixture']['id']
        home = item['teams']['home']['name']
        away = item['teams']['away']['name']
        
        # Parse ISO date safely
        date_raw = item['fixture']['date'].replace('Z', '')
        try:
            dt_obj = datetime.fromisoformat(date_raw)
        except ValueError:
            dt_obj = datetime.utcnow()

        # Compute dynamic Poisson True Odds
        poisson_calc = calculate_poisson_odds()
        t_home = poisson_calc["true_home"]
        t_draw = poisson_calc["true_draw"]
        t_away = poisson_calc["true_away"]

        # Match with Live Bookmaker Odds from The Odds API
        b_home, b_draw, b_away = None, None, None
        for odds_item in odds_data:
            # Match home/away names loosely
            if home.lower() in odds_item.get('home_team', '').lower() or odds_item.get('home_team', '').lower() in home.lower():
                bookmakers = odds_item.get('bookmakers', [])
                if bookmakers:
                    outcomes = bookmakers[0]['markets'][0].get('outcomes', [])
                    for o in outcomes:
                        if o['name'] == odds_item['home_team']:
                            b_home = o['price']
                        elif o['name'] == odds_item['away_team']:
                            b_away = o['price']
                        elif o['name'].lower() == 'draw':
                            b_draw = o['price']
                break

        # Calculate maximum edge percentage
        home_edge = (b_home / t_home - 1) if (b_home and t_home) else 0
        away_edge = (b_away / t_away - 1) if (b_away and t_away) else 0
        max_edge = max(home_edge, away_edge, 0.0)

        # Update or Insert local database cache
        existing = db.query(CachedFixture).filter(CachedFixture.id == f_id).first()
        if not existing:
            existing = CachedFixture(
                id=f_id,
                home_team=home,
                away_team=away,
                match_date=dt_obj,
                league_id=int(league),
                true_home_odds=t_home,
                true_draw_odds=t_draw,
                true_away_odds=t_away,
                bookmaker_home_odds=b_home,
                bookmaker_draw_odds=b_draw,
                bookmaker_away_odds=b_away,
                max_value_edge=max_edge
            )
            db.add(existing)
        else:
            existing.true_home_odds = t_home
            existing.true_draw_odds = t_draw
            existing.true_away_odds = t_away
            existing.bookmaker_home_odds = b_home
            existing.bookmaker_draw_odds = b_draw
            existing.bookmaker_away_odds = b_away
            existing.max_value_edge = max_edge

        processed_count += 1

    db.commit()
    return {"status": "Sync Complete", "fixtures_processed": processed_count}
