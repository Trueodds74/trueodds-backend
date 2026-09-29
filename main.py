import os
import requests
from typing import Generator
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# 1. Setup the App
app = FastAPI(
    title="TrueOdds Backend",
    version="1.0.0",
    description="Backend service providing True Odds calculation and fixture data for TrueOdds Mobile App."
)

# 2. Get and Format Secret Keys / DB URL
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./test.db")

# Fix Render PostgreSQL URL compatibility if using Postgres
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

# 3. Connect to Database & Session Management
engine = create_engine(DATABASE_URL)
Base = declarative_base()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Database Model
class TestTable(Base):
    __tablename__ = "tests"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)


Base.metadata.create_all(bind=engine)


# Dependency to manage DB connections per request safely
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 4. Endpoints
@app.get("/")
def read_root():
    return {"message": "TrueOdds Backend is Live! 🚀", "version": "1.0.0"}


@app.get("/test-db")
def test_database(db: Session = Depends(get_db)):
    try:
        test_entry = TestTable(name="connection_success")
        db.add(test_entry)
        db.commit()
        db.refresh(test_entry)
        return {
            "status": "Database connected successfully!",
            "inserted_id": test_entry.id
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )


@app.get("/test-keys")
def test_keys():
    return {
        "football_api": "Ready" if API_FOOTBALL_KEY else "Missing",
        "odds_api": "Ready" if ODDS_API_KEY else "Missing"
    }


@app.get("/fixtures")
def get_fixtures(season: str = "2024", league: str = "39"):
    """
    Fetches recent or upcoming fixtures from API-Football.
    Note: Standard direct headers used below. If using RapidAPI, 
    pass 'x-rapidapi-key' and 'x-rapidapi-host'.
    """
    if not API_FOOTBALL_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API_FOOTBALL_KEY environment variable is not configured."
        )

    url = f"https://v3.football.api-sports.io/fixtures?league={league}&last=5&season={season}"
    headers = {
        "x-apisports-key": API_FOOTBALL_KEY,
        "x-rapidapi-key": API_FOOTBALL_KEY  # Included for RapidAPI compatibility
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code, 
                detail=f"API-Football error: {response.text}"
            )
            
        data = response.json()
        
        fixtures = []
        if data.get('response'):
            for match in data['response']:
                fixtures.append({
                    "id": match['fixture']['id'],
                    "home": match['teams']['home']['name'],
                    "away": match['teams']['away']['name'],
                    "date": match['fixture']['date'],
                    "status": match['fixture']['status']['short']
                })

        return {
            "total": len(fixtures),
            "upcoming_matches": fixtures  # Fixed syntax error (changed ] to })
        }

    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
            detail=f"External request failed: {str(e)}"
        )


@app.get("/odds")
def get_odds(sport: str = "soccer_epl"):
    """
    Fetches live market odds from The Odds API.
    """
    if not ODDS_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ODDS_API_KEY environment variable is not configured."
        )

    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/?apiKey={ODDS_API_KEY}&regions=uk&markets=h2h&oddsFormat=decimal"

    try:
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code, 
                detail=f"The Odds API error: {response.text}"
            )

        data = response.json()
        return {"total_matches": len(data), "betting_odds": data}

    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
            detail=f"External request failed: {str(e)}"
        )

@app.get("/value-bets")
def get_value_bets():
    # 1. Get the raw data from the Odds API
    url = "https://api.the-odds-api.com/v4/sports/soccer_epl/odds/?apiKey=" + ODDS_API_KEY + "&regions=uk&markets=h2h&oddsFormat=decimal"
    response = requests.get(url)
    matches = response.json()

    value_bets = []

    # 2. Loop through every match
    for match in matches:
        home_team = match['home_team']
        away_team = match['away_team']

        # We start with 0 odds and look for higher ones
        best_home_odd = 0
        best_home_bookie = ""
        best_away_odd = 0
        best_away_bookie = ""
        best_draw_odd = 0
        best_draw_bookie = ""

        # 3. Look at every bookmaker for this match
        for bookie in match.get('bookmakers', []):
            for market in bookie.get('markets', []):
                if market['key'] == 'h2h': 
                    for outcome in market['outcomes']:
                        name = outcome['name']
                        price = outcome['price']

                        # Check if this is the best price for Home Team
                        if name == home_team and price > best_home_odd:
                            best_home_odd = price
                            best_home_bookie = bookie['title']

                        # Check if this is the best price for Away Team
                        elif name == away_team and price > best_away_odd:
                            best_away_odd = price
                            best_away_bookie = bookie['title']

                        # Check if this is the best price for a Draw
                        elif name == 'Draw' and price > best_draw_odd:
                            best_draw_odd = price
                            best_draw_bookie = bookie['title']

        # 4. Save the best finds for this match
        value_bets.append({
            "match": f"{home_team} vs {away_team}",
            "best_home_win": {"odd": best_home_odd, "bookmaker": best_home_bookie},
            "best_away_win": {"odd": best_away_odd, "bookmaker": best_away_bookie},
            "best_draw": {"odd": best_draw_odd, "bookmaker": best_draw_bookie}
        })

    return {"total_value_bets": len(value_bets), "value_bets": value_bets}
