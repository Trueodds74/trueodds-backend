import os
import requests
from datetime import datetime
from typing import Generator
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from analytics import PoissonEngine

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

# Neon Tech Connection Pool Optimization
if "postgresql" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800
    )
else:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})

Base = declarative_base()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 3. Database Model
class CachedFixture(Base):
    __tablename__ = "cached_fixtures_v2"
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
def get_edges(db: Session = Depends(get_db)):
    fixtures = db.query(CachedFixture).all()
    matches = []
    
    for f in fixtures:
        # Calculate EV for all three outcomes
        home_ev = ((f.bookmaker_home_odds / f.true_home_odds) - 1) * 100
        draw_ev = ((f.bookmaker_draw_odds / f.true_draw_odds) - 1) * 100
        away_ev = ((f.bookmaker_away_odds / f.true_away_odds) - 1) * 100
        
        edges = {"Home": home_ev, "Draw": draw_ev, "Away": away_ev}
        best_outcome = max(edges, key=edges.get)
        best_ev = edges[best_outcome]
        
        # Only show positive Expected Value
        if best_ev > 0:
            if best_outcome == "Home":
                fair_odd = f.true_home_odds
                bookie_odd = f.bookmaker_home_odds
                model_prob = round(100 / fair_odd, 1)
                reason = f"Model implies {model_prob}% chance (fair odds {fair_odd}), but bookies offer {bookie_odd}. This creates a {round(best_ev, 1)}% Expected Value (EV)."
            elif best_outcome == "Draw":
                fair_odd = f.true_draw_odds
                bookie_odd = f.bookmaker_draw_odds
                model_prob = round(100 / fair_odd, 1)
                reason = f"Model implies {model_prob}% chance of a draw (fair odds {fair_odd}), but bookies are paying {bookie_odd}. This creates a {round(best_ev, 1)}% Expected Value (EV)."
            else:
                fair_odd = f.true_away_odds
                bookie_odd = f.bookmaker_away_odds
                model_prob = round(100 / fair_odd, 1)
                reason = f"Model implies {model_prob}% chance (fair odds {fair_odd}), but bookies offer {bookie_odd}. This creates a {round(best_ev, 1)}% Expected Value (EV)."

            matches.append({
                "fixture_id": f.id,
                "match": f"{f.home_team} vs {f.away_team}",
                "best_bet": f"{best_outcome} Win",
                "bookmaker_odd": bookie_odd,
                "expected_value_ev": f"{round(best_ev, 1)}%", # Renamed for clarity
                "reason": reason
            })

    # Sort by highest Expected Value
    matches.sort(key=lambda x: float(x["expected_value_ev"].replace('%', '')), reverse=True)

    return {
        "status": "success",
        "total_value_opportunities": len(matches),
        "matches": matches[:10]
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
    
    fixtures_url = "https://v3.football.api-sports.io/fixtures"
    params = {
        "league": league,
        "season": season,
        "next": 10
    }

    f_response = requests.get(
        fixtures_url,
        headers={"x-apisports-key": API_FOOTBALL_KEY},
        params=params,
        timeout=10
    )
    
    if f_response.status_code != 200:
        return {"status": "error", "message": "Failed to pull fixtures"}

    fixtures_data = f_response.json().get('response', [])
    
    return {
        "status": "success",
        "matches_synced": len(fixtures_data),
        "message": "Data pulled successfully from API-Football"
    }

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

@app.get("/sync/mock-data")
def create_mock_sa_matches(db: Session = Depends(get_db)):
    db.query(CachedFixture).delete()
    db.commit()
    
    # 1. Add mock historic form data arrays to feed the Poisson calculation
    mock_matches = [
        {
            "home": "Kaizer Chiefs", "away": "Orlando Pirates", "h_edge": 0.15,
            "home_stats": {"avg_goals_scored_home": 1.70, "avg_goals_conceded_home": 1.10},
            "away_stats": {"avg_goals_scored_away": 1.40, "avg_goals_conceded_away": 0.95}
        },
        {
            "home": "Mamelodi Sundowns", "away": "Stellenbosch FC", "h_edge": 0.12,
            "home_stats": {"avg_goals_scored_home": 2.30, "avg_goals_conceded_home": 0.50},
            "away_stats": {"avg_goals_scored_away": 1.50, "avg_goals_conceded_away": 1.10}
        },
        {
            "home": "Cape Town City", "away": "SuperSport United", "h_edge": 0.08,
            "home_stats": {"avg_goals_scored_home": 1.45, "avg_goals_conceded_home": 1.20},
            "away_stats": {"avg_goals_scored_away": 1.20, "avg_goals_conceded_away": 1.30}
        },
        {
            "home": "AmaZulu", "away": "Golden Arrows", "h_edge": 0.18,
            "home_stats": {"avg_goals_scored_home": 1.10, "avg_goals_conceded_home": 1.40},
            "away_stats": {"avg_goals_scored_away": 1.35, "avg_goals_conceded_away": 1.50}
        },
        {
            "home": "Sekhukhune United", "away": "Polokwane City", "h_edge": 0.10,
            "home_stats": {"avg_goals_scored_home": 1.30, "avg_goals_conceded_home": 1.00},
            "away_stats": {"avg_goals_scored_away": 1.05, "avg_goals_conceded_away": 1.25}
        }
    ]
    
    # Static league benchmark defaults (Reflective of a standard competitive domestic season)
    psl_averages = {"league_avg_home_scored": 1.35, "league_avg_away_scored": 1.05}
    
    for i, m in enumerate(mock_matches):
        # 2. RUN THE REAL MATHEMATICAL CALCULATION LIVE!
        t_home, t_draw, t_away = PoissonEngine.calculate_match_odds(
            m["home_stats"], m["away_stats"], psl_averages
        )
        
        # Factor in your requested edge discrepancies on top of calculated home wins
        bookie_home_odd = round(t_home * (1 + m["h_edge"]), 2)
        
        fixture = CachedFixture(
            id=1000 + i,
            home_team=m["home"],
            away_team=m["away"],
            league_id=1,
            true_home_odds=t_home,
            true_draw_odds=t_draw,
            true_away_odds=t_away,
            bookmaker_home_odds=bookie_home_odd,
            bookmaker_draw_odds=round(t_draw * 0.95, 2), # Simulated margin deductions
            bookmaker_away_odds=round(t_away * 0.95, 2),
            max_value_edge=m["h_edge"]
        )
        db.add(fixture)
        
    db.commit()
    return {"status": "Dynamic Mathematical Mock Data Created", "matches_added": 5}
@app.get("/debug-db")
def debug_database():
    try:
        db = SessionLocal()
        count = db.query(CachedFixture).count()
        return {"status": "SUCCESS", "rows": count}
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()
        

    
