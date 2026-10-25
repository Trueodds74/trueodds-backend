"""Tests for service.py (no database, no web server needed)."""
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

from service import (
    PRICE_MAX_AGE_HOURS, best_prices, build_candidates, pick_card,
    select_for_window, settle_profit, track_record, validate_odds,
)

NOW = datetime(2026, 10, 24, 10, 0, 0)


def price(book, h=None, d=None, a=None, age_h=1):
    return NS(bookmaker=book, home_odds=h, draw_odds=d, away_odds=a,
              updated_at=NOW - timedelta(hours=age_h))


def fixture(fid=1, p=(0.5, 0.25, 0.25), hours=5, home="A", away="B"):
    return NS(id=fid, home_team=home, away_team=away,
              kickoff=NOW + timedelta(hours=hours),
              p_home=p[0], p_draw=p[1], p_away=p[2])


def raises(exc, fn, *a):
    try:
        fn(*a)
    except exc:
        return True
    return False


def test_validate_odds_accepts_normal():
    assert validate_odds(2.1) == 2.1
    assert validate_odds(5) == 5.0


def test_validate_odds_rejects_bad():
    for bad in (1.0, 0.5, 0, -2, float("nan"), 1001, "2.0", None, True):
        assert raises(ValueError, validate_odds, bad), bad


def test_best_price_picks_highest_and_names_bookmaker():
    best = best_prices([price("Betway", h=2.0), price("Hollywoodbets", h=2.2)], NOW)
    assert best["Home Win"] == (2.2, "Hollywoodbets")


def test_tie_goes_to_first_alphabetical_bookmaker():
    best = best_prices([price("Zebet", h=2.0), price("Betway", h=2.0)], NOW)
    assert best["Home Win"][1] == "Betway"


def test_stale_price_is_ignored():
    best = best_prices([price("Betway", h=9.0, age_h=PRICE_MAX_AGE_HOURS + 1),
                        price("Supabets", h=2.0)], NOW)
    assert best["Home Win"] == (2.0, "Supabets")


def test_price_exactly_at_max_age_is_kept():
    best = best_prices([price("Betway", h=3.0, age_h=PRICE_MAX_AGE_HOURS)], NOW)
    assert best["Home Win"][0] == 3.0


def test_future_dated_price_is_ignored():
    p = price("Betway", h=3.0)
    p.updated_at = NOW + timedelta(hours=1)
    assert best_prices([p], NOW) == {}


def test_missing_or_invalid_outcomes_skipped():
    best = best_prices([price("Betway", h=None, d=1.0, a=3.0)], NOW)
    assert list(best) == ["Away Win"]


def test_candidate_uses_best_outcome_and_bookmaker():
    fx = fixture(p=(0.5, 0.25, 0.25))
    # home 0.5*2.2-1 = +10%; away 0.25*5.0-1 = +25%
    cands, bk = build_candidates([fx], {1: [price("Betway", h=2.2, a=4.0),
                                            price("Supabets", a=5.0)]}, NOW)
    assert len(cands) == 1
    c = cands[0]
    assert c.outcome == "Away Win" and c.bookmaker_odds == 5.0
    assert bk[(1, "Away Win")] == "Supabets"


def test_fixture_without_probabilities_or_prices_skipped():
    no_probs = fixture(1, p=(None, None, None))
    no_prices = fixture(2)
    cands, _bk = build_candidates([no_probs, no_prices], {1: [price("B", h=2.0)]}, NOW)
    assert cands == []


def test_window_excludes_started_and_far_future():
    fixtures = [fixture(1, hours=-1), fixture(2, hours=5), fixture(3, hours=30)]
    prices = {i: [price("B", h=2.2)] for i in (1, 2, 3)}
    result, _bk = select_for_window(fixtures, prices, NOW, hours=24)
    assert [c.fixture_id for c in result.published] == [2]


def test_no_value_gives_message():
    result, _bk = select_for_window([fixture(1)], {1: [price("B", h=1.9, d=3.5, a=3.5)]}, NOW)
    assert result.published == [] and result.message == "No value today."


def test_cap_of_four_and_review_hold_through_service():
    fixtures = [fixture(i, hours=2 + i) for i in range(1, 8)]
    prices = {i: [price("B", h=2.10 + 0.02 * i)] for i in range(1, 7)}  # edges 6%..12%
    prices[7] = [price("B", h=3.0)]  # 50% edge -> held for review, not published
    result, _bk = select_for_window(fixtures, prices, NOW)
    assert len(result.published) == 4
    assert [c.fixture_id for c in result.held_for_review] == [7]
    assert len(result.qualified_not_published) == 2


def test_pick_card_numbers_and_text():
    fx = fixture(p=(0.5, 0.25, 0.25))
    cands, bk = build_candidates([fx], {1: [price("Betway", h=2.2)]}, NOW)
    card = pick_card(cands[0], bk[(1, "Home Win")])
    assert card["edge_pct"] == 10.0 and card["fair_odds"] == 2.0
    assert card["bookmaker"] == "Betway" and "not a guarantee" in card["reason"]


def test_settle_profit():
    assert settle_profit("won", 2.5) == 1.5
    assert settle_profit("lost", 2.5) == -1.0
    assert settle_profit("void", 2.5) == 0.0
    assert settle_profit(None, 2.5) is None


def row(odds, edge, result, closing=None):
    return NS(odds=odds, edge=edge, result=result, closing_fair_prob=closing)


def test_track_record_basic():
    rec = track_record([row(2.0, 0.06, "won"), row(3.0, 0.08, "lost"),
                        row(2.0, 0.10, "void"), row(2.0, 0.05, None)])
    assert rec["picks_published"] == 4 and rec["picks_settled"] == 3
    assert (rec["wins"], rec["losses"], rec["voids"]) == (1, 1, 1)
    assert rec["profit_units"] == 0.0           # +1.0 - 1.0 + 0
    assert rec["roi_pct"] == 0.0 and rec["strike_rate_pct"] == 50.0


def test_track_record_empty():
    rec = track_record([])
    assert rec["picks_published"] == 0 and rec["roi_pct"] is None
    assert rec["average_edge_pct"] is None and rec["closing_line_value_pct"] is None


def test_track_record_clv():
    # published at 2.2, closing fair prob 0.5 -> CLV = 0.5*2.2-1 = +10%
    rec = track_record([row(2.2, 0.1, None, closing=0.5)])
    assert rec["closing_line_value_pct"] == 10.0 and rec["closing_line_sample"] == 1
