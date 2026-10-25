"""API tests for main_v3.py. Run on your laptop (needs fastapi + httpx installed):

    pip install -r requirements-dev.txt
    pytest -q test_engine.py test_service.py test_api.py

They use a throwaway SQLite file, never your Neon database.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

# Must be set BEFORE main_v3 is imported.
_DB_FILE = os.path.join(tempfile.mkdtemp(), "api_test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE}"
os.environ["ADMIN_KEY"] = "test-admin-key"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main as main_v3  # noqa: E402
from service import model_probabilities  # noqa: E402

KEY = {"X-Admin-Key": "test-admin-key"}
STATS = {
    "home_stats": {"avg_goals_scored_home": 1.5, "avg_goals_conceded_home": 1.0},
    "away_stats": {"avg_goals_scored_away": 1.2, "avg_goals_conceded_away": 1.1},
}


@pytest.fixture(autouse=True)
def fresh_db():
    main_v3.Base.metadata.drop_all(bind=main_v3.engine)
    main_v3.Base.metadata.create_all(bind=main_v3.engine)
    yield


@pytest.fixture
def client():
    return TestClient(main_v3.app)


def kickoff(hours=5):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def fair_home_odds():
    p = model_probabilities(
        STATS["home_stats"], STATS["away_stats"],
        {"league_avg_home_scored": main_v3.DEFAULT_LEAGUE_HOME_AVG,
         "league_avg_away_scored": main_v3.DEFAULT_LEAGUE_AWAY_AVG})
    return 1 / p["Home Win"]


def add_fixture(client, hours=5):
    r = client.post("/admin/fixtures", headers=KEY,
                    json={"home_team": "Home FC", "away_team": "Away FC",
                          "kickoff_utc": kickoff(hours), **STATS})
    assert r.status_code == 200, r.text
    return r.json()["fixture_id"]


def set_price(client, fid, book="Betway", **odds):
    return client.post(f"/admin/fixtures/{fid}/prices", headers=KEY,
                       json={"bookmaker": book, **odds})


def test_admin_requires_key(client):
    assert client.post("/admin/mock-data").status_code == 401
    assert client.post("/admin/mock-data", headers={"X-Admin-Key": "wrong"}).status_code == 401


def test_admin_disabled_without_server_key(client, monkeypatch):
    monkeypatch.setattr(main_v3, "ADMIN_KEY", None)
    assert client.post("/admin/mock-data", headers=KEY).status_code == 503


def test_empty_day_says_no_value(client):
    data = client.get("/picks/today").json()
    assert data["count"] == 0 and data["message"] == "No value today."


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_full_flow_publish_and_public_view(client):
    fid = add_fixture(client)
    # price 10% above fair odds => edge ~10%
    assert set_price(client, fid, home_odds=round(fair_home_odds() * 1.10, 2)).status_code == 200
    preview = client.get("/admin/preview", headers=KEY).json()
    assert len(preview["would_publish"]) == 1
    # nothing is public until published
    assert client.get("/picks/today").json()["count"] == 0
    assert client.post("/admin/publish", headers=KEY).json()["published_now"] == 1
    today = client.get("/picks/today").json()
    assert today["count"] == 1
    pick = today["picks"][0]
    assert pick["pick"] == "Home Win" and pick["bookmaker"] == "Betway"
    assert 9.0 <= pick["edge_pct"] <= 11.0


def test_publish_twice_does_not_duplicate(client):
    fid = add_fixture(client)
    set_price(client, fid, home_odds=round(fair_home_odds() * 1.10, 2))
    client.post("/admin/publish", headers=KEY)
    second = client.post("/admin/publish", headers=KEY).json()
    assert second["published_now"] == 0 and second["published_today_total"] == 1
    assert client.get("/picks/today").json()["count"] == 1


def test_huge_edge_is_held_not_published(client):
    fid = add_fixture(client)
    set_price(client, fid, home_odds=round(fair_home_odds() * 1.60, 2))  # ~60% "edge"
    out = client.post("/admin/publish", headers=KEY).json()
    assert out["published_now"] == 0 and out["held_for_review"] == 1
    assert client.get("/picks/today").json()["message"] == "No value today."


def test_bad_odds_rejected(client):
    fid = add_fixture(client)
    assert set_price(client, fid, home_odds=1.0).status_code == 422
    assert set_price(client, fid, home_odds=5000).status_code == 422
    assert set_price(client, fid).status_code == 422  # nothing given
    assert set_price(client, 9999, home_odds=2.0).status_code == 404


def test_bad_stats_rejected(client):
    bad = {"home_team": "A", "away_team": "B", "kickoff_utc": kickoff(),
           "home_stats": {"avg_goals_scored_home": 99, "avg_goals_conceded_home": 1},
           "away_stats": STATS["away_stats"]}
    assert client.post("/admin/fixtures", headers=KEY, json=bad).status_code == 422


def test_started_match_not_published(client):
    fid = add_fixture(client, hours=-2)
    set_price(client, fid, home_odds=round(fair_home_odds() * 1.10, 2))
    assert client.post("/admin/publish", headers=KEY).json()["published_now"] == 0


def test_stale_price_not_used(client):
    fid = add_fixture(client)
    set_price(client, fid, home_odds=round(fair_home_odds() * 1.10, 2))
    with main_v3.SessionLocal() as db:
        row = db.query(main_v3.Price).first()
        row.updated_at = row.updated_at - timedelta(hours=13)
        db.commit()
    assert client.get("/admin/preview", headers=KEY).json()["would_publish"] == []


def test_mock_data_never_reaches_public(client):
    assert client.post("/admin/mock-data", headers=KEY).status_code == 200
    assert client.post("/admin/publish", headers=KEY).json()["published_now"] == 0
    client.post("/admin/publish?include_mock=true", headers=KEY)
    assert client.get("/picks/today").json()["count"] == 0
    assert client.get("/track-record").json()["summary"]["picks_published"] == 0


def test_settle_and_track_record(client):
    fid = add_fixture(client)
    set_price(client, fid, home_odds=round(fair_home_odds() * 1.10, 2))
    client.post("/admin/publish", headers=KEY)
    pick_id = client.get("/picks/today").json()["picks"][0]["pick_id"]
    r = client.post(f"/admin/picks/{pick_id}/result", headers=KEY,
                    json={"result": "won", "closing_home_odds": 2.0,
                          "closing_draw_odds": 3.5, "closing_away_odds": 4.0})
    assert r.status_code == 200 and r.json()["closing_fair_prob"] is not None
    summary = client.get("/track-record").json()["summary"]
    assert summary["wins"] == 1 and summary["profit_units"] > 0
    assert summary["closing_line_sample"] == 1


def test_bad_result_rejected(client):
    assert client.post("/admin/picks/1/result", headers=KEY, json={"result": "won"}).status_code == 404
    fid = add_fixture(client)
    set_price(client, fid, home_odds=round(fair_home_odds() * 1.10, 2))
    client.post("/admin/publish", headers=KEY)
    pid = client.get("/picks/today").json()["picks"][0]["pick_id"]
    assert client.post(f"/admin/picks/{pid}/result", headers=KEY,
                       json={"result": "maybe"}).status_code == 422
    assert client.post(f"/admin/picks/{pid}/result", headers=KEY,
                       json={"result": "won", "closing_home_odds": 2.0}).status_code == 422


def test_daily_cap_is_four(client):
    for i in range(6):
        fid = add_fixture(client, hours=2 + i)
        set_price(client, fid, home_odds=round(fair_home_odds() * (1.06 + 0.01 * i), 2))
    out = client.post("/admin/publish", headers=KEY).json()
    assert out["published_now"] == 4 and out["published_today_total"] == 4
    assert client.get("/picks/today").json()["count"] == 4


def test_old_dashboard_endpoint_still_works(client):
    data = client.get("/dashboard/edges").json()
    assert data["status"] == "success" and data["matches"] == []
