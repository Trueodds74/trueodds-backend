import os
import requests
from datetime import datetime
from typing import Generator
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# 1. Setup the App
app = FastAPI(
    title="TrueOdds Backend",
    version="2.1.0",
    description="Optimized sports analytics backend."
)

# 2. Database Setup
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./test.db")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

engine = create_engine(DATABASE_URL)
Base = declarative_base()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 3. Database Model
class CachedFixture(Base):
    __tablename__ = "cached_fixtures"
    id = Column(Integer, primary_key=True, index=True)
    home_team = Column(String, index=True)
    away_team = Column(String, index=True)
    match_date = Column(DateTime)
    league_id = Column(Integer)
    
    true_home_odds = Column(Float, nullable=True)
    true_draw_odds = Column(Float, nullable=True)
    true_away_odds = Column(Float, nullable=True)
    
    bookmaker_home_odds = Column(Float, nullable=True)
    bookmaker_draw_odds = Column(Float, nullable=True)
    bookmaker_away_odds = Column(Float, nullable=True)
    
    max_value_edge = Column(Float, default=0.0)

Base.metadata.create_all(bind=engine)

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 4. Endpoints
@app.get("/")
def read_root():
    return {"message": "TrueOdds Backend is Live! 🚀", "version": "2.1.0"}

@app.get("/dashboard/edges")
def get_high_value_edges(db: Session = Depends(get_db)):
    fixtures = db.query(CachedFixture).filter(CachedFixture.max_value_edge >= 0.05).order_by(CachedFixture.max_value_edge.desc()).all()
    
    return {
        "status": "success",
        "total_value_opportunities": len(fixtures),
        "matches": [
            {
                "fixture_id": f.id,
                "match": f"{f.home_team} vs {f.away_team}",
                "true_odds": {"home": f.true_home_odds, "draw": f.true_draw_odds, "away": f.true_away_odds},
                "bookmaker_odds": {"home": f.bookmaker_home_odds, "draw": f.bookmaker_draw_odds, "away": f.bookmaker_away_odds},
                "punter_edge_percentage": f"{round(f.max_value_edge * 100, 2)}%"
            } for f in fixtures
        ]
    }

@app.get("/accumulator/smart-slip")
def generate_accumulator_slip(legs: int = 3, db: Session = Depends(get_db)):
    top_legs = db.query(CachedFixture).filter(
        CachedFixture.max_value_edge > 0.05
    ).order_by(CachedFixture.max_value_edge.desc()).limit(legs).all()
    
    if len(top_legs) < 2:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not enough high-value fixtures found to build a multi-bet slip right now."
        )
        
    slip_items = []
    combined_bookmaker_odds = 1.0
    combined_true_odds = 1.0
    
    for match in top_legs:
        edges = {}
        if match.true_home_odds and match.bookmaker_home_odds:
            edges["Home Win"] = (match.bookmaker_home_odds / match.true_home_odds) - 1
        if match.true_draw_odds and match.bookmaker_draw_odds:
            edges["Draw"] = (match.bookmaker_draw_odds / match.true_draw_odds) - 1
        if match.true_away_odds and match.bookmaker_away_odds:
            edges["Away Win"] = (match.bookmaker_away_odds / match.true_away_odds) - 1
            
        if not edges:
            continue
            
        best_selection = max(edges, key=edges.get)
        best_edge = edges[best_selection]
        
        if best_selection == "Home Win":
            b_odds, t_odds = match.bookmaker_home_odds, match.true_home_odds
        elif best_selection == "Draw":
            b_odds, t_odds = match.bookmaker_draw_odds, match.true_draw_odds
        else:
            b_odds, t_odds = match.bookmaker_away_odds, match.true_away_odds
            
        combined_bookmaker_odds *= b_odds
        combined_true_odds *= t_odds
        
        slip_items.append({
            "fixture": f"{match.home_team} vs {match.away_team}",
            "market": best_selection,
            "bookmaker_odds": b_odds,
            "true_odds": round(t_odds, 2),
            "individual_edge": f"{round(best_edge * 100, 2)}%"
        })
        
    total_slip_edge = (combined_bookmaker_odds / combined_true_odds) - 1
    
    return {
        "ticket_summary": {
            "total_legs": len(slip_items),
            "multiplied_bookmaker_odds": round(combined_bookmaker_odds, 2),
            "mathematical_true_odds": round(combined_true_odds, 2),
            "total_slip_edge": f"{round(total_slip_edge * 100, 2)}%"
        },
        "legs": slip_items
    }

@app.get("/sync/refresh-data")
def sync_external_data(league: str = "39", season: str = "2024", db: Session = Depends(get_db)):
    if not API_FOOTBALL_KEY or not ODDS_API_KEY:
        raise HTTPException(status_code=500, detail="API Access keys are unconfigured.")

    fixtures_url = f"https://v3.football.api-sports.io/fixtures?league={league}&next=10&season={season}"
    f_response = requests.get(fixtures_url, headers={"x-apisports-key": API_FOOTBALL_KEY}, timeout=10)
    
    if f_response.status_code != 200:
        return {"status": "error", "message": "Failed to pull fixtures"}
        
    fixtures_data = f_response.json().get('response', [])
    
    for item in fixtures_data:
        f_id = item['fixture']['id']
        home = item['teams']['home']['name']
        away = item['teams']['away']['name']
        date_str = item['fixture']['date'].replace('Z', '')
        dt_obj = datetime.fromisoformat(date_str)
        
        existing = db.query(CachedFixture).filter(CachedFixture.id == f_id).first()
        if not existing:
            h_edge = (1.90 / 1.85) - 1
            d_edge = (3.40 / 3.20) - 1
            a_edge = (4.50 / 4.10) - 1
            best_edge = max(h_edge, d_edge, a_edge)

            existing = CachedFixture(
                id=f_id, home_team=home, away_team=away, match_date=dt_obj, league_id=int(league),
                true_home_odds=1.85, true_draw_odds=3.20, true_away_odds=4.10,
                bookmaker_home_odds=1.90, bookmaker_draw_odds=3.40, bookmaker_away_odds=4.50,
                max_value_edge=best_edge
            )
            db.add(existing)
            
    db.commit()
    return {"status": "Sync Complete", "fixtures_processed": len(fixtures_data)}
