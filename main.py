import os
import requests
from fastapi import FastAPI
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 1. Setup the App
app = FastAPI(title="TrueOdds Backend")

# 2. Get your secret keys from Render
DATABASE_URL = os.environ.get("DATABASE_URL")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

# 3. Connect to your Database
engine = create_engine(DATABASE_URL)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)

# A simple table to test the connection
class TestTable(Base):
    __tablename__ = "tests"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)

Base.metadata.create_all(bind=engine)

# 4. The Endpoints (The doors to your app)
@app.get("/")
def read_root():
    return {"message": "TrueOdds Backend is Live! 🚀"}

@app.get("/test-db")
def test_database():
    db = SessionLocal()
    try:
        db.add(TestTable(name="connection_success"))
        db.commit()
        return {"status": "Database connected successfully!"}
    except Exception as e:
        return {"status": "Database error", "error": str(e)}
    finally:
        db.close()

@app.get("/test-keys")
def test_keys():
    return {
        "football_api": "Ready" if API_FOOTBALL_KEY else "Missing",
        "odds_api": "Ready" if ODDS_API_KEY else "Missing"
    }

@app.get("/fixtures")
def get_fixtures():
    url = "https://v3.football.api-sports.io/fixtures?league=39&last=5&season=2024"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    
    response = requests.get(url, headers=headers)
    data = response.json()
    
    fixtures = []
    if data.get('response'):
        for match in data['response']:
            fixtures.append({
                "home": match['teams']['home']['name'],
                "away": match['teams']['away']['name'],
                "date": match['fixture']['date']
            })
            
    return {"upcoming_matches":fixture]
