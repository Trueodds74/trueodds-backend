"""TrueValue Odds - the "glue" rules between the database and the pick engine.

Pure Python (no database, no web framework) so every rule here is tested
without a server. main.py only moves data in and out of the database and
calls these functions.

Rules added here:
  * a bookmaker price older than PRICE_MAX_AGE_HOURS is ignored (stale prices
    are the most common way to publish a "value" bet that no longer exists)
  * for each outcome the BEST current price across bookmakers is used, and the
    bookmaker that offers it is named (that is the "click to bet" button)
  * every odds value must be a sane decimal price (> 1.0 and <= MAX_ODDS)
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from analytics import PoissonEngine
from picks import (
    OUTCOME_LABELS,
    Candidate,
    SelectionResult,
    best_candidate,
    closing_line_value,
    select_daily_picks,
)

PRICE_MAX_AGE_HOURS = 12
MAX_ODDS = 1000.0
PRICE_ATTRS = {"Home Win": "home_odds", "Draw": "draw_odds", "Away Win": "away_odds"}
SETTLED_RESULTS = ("won", "lost", "void")


def validate_odds(value: float, name: str = "odds") -> float:
    """Decimal odds must be a real number above 1.0 and not absurdly large."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if value != value or not (1.0 < value <= MAX_ODDS):
        raise ValueError(f"{name} must be above 1.0 and at most {MAX_ODDS}, got {value!r}")
    return float(value)


def model_probabilities(
    home_stats: Dict[str, float],
    away_stats: Dict[str, float],
    league_averages: Dict[str, float],
) -> Dict[str, float]:
    p_home, p_draw, p_away = PoissonEngine.calculate_match_probabilities(
        home_stats, away_stats, league_averages)
    return {"Home Win": p_home, "Draw": p_draw, "Away Win": p_away}


def best_prices(
    prices: Iterable[Any],
    now: datetime,
    max_age_hours: float = PRICE_MAX_AGE_HOURS,
) -> Dict[str, Tuple[float, str]]:
    """Best fresh price per outcome: {"Home Win": (2.10, "Hollywoodbets"), ...}.

    Each price row needs .bookmaker, .home_odds, .draw_odds, .away_odds and
    .updated_at (naive UTC). Rows older than max_age_hours, or dated in the
    future, are skipped. Ties go to the bookmaker name that sorts first so the
    answer is repeatable.
    """
    oldest = now - timedelta(hours=max_age_hours)
    best: Dict[str, Tuple[float, str]] = {}
    for row in sorted(prices, key=lambda r: r.bookmaker):
        if row.updated_at is None or row.updated_at < oldest or row.updated_at > now:
            continue
        for label, attr in PRICE_ATTRS.items():
            odds = getattr(row, attr)
            if odds is None or not odds > 1.0:
                continue
            if label not in best or odds > best[label][0]:
                best[label] = (float(odds), row.bookmaker)
    return best


def fixture_probs(fixture: Any) -> Optional[Dict[str, float]]:
    """The stored model probabilities, or None if the fixture has none yet."""
    probs = {
        "Home Win": fixture.p_home,
        "Draw": fixture.p_draw,
        "Away Win": fixture.p_away,
    }
    if any(p is None for p in probs.values()):
        return None
    return probs


def build_candidates(
    fixtures: Iterable[Any],
    prices_by_fixture: Dict[int, List[Any]],
    now: datetime,
) -> Tuple[List[Candidate], Dict[Tuple[int, str], str]]:
    """One candidate per fixture (its best-edge outcome at the best price).

    Returns (candidates, bookmaker_for) where bookmaker_for[(fixture_id,
    outcome)] names the bookmaker offering that price.
    """
    candidates: List[Candidate] = []
    bookmaker_for: Dict[Tuple[int, str], str] = {}
    for fx in fixtures:
        probs = fixture_probs(fx)
        if probs is None:
            continue
        best = best_prices(prices_by_fixture.get(fx.id, []), now)
        odds = {label: best[label][0] for label in best}
        cand = best_candidate(fx.id, f"{fx.home_team} vs {fx.away_team}",
                              probs, odds, fx.kickoff)
        if cand is None:
            continue
        candidates.append(cand)
        bookmaker_for[(fx.id, cand.outcome)] = best[cand.outcome][1]
    return candidates, bookmaker_for


def select_for_window(
    fixtures: Iterable[Any],
    prices_by_fixture: Dict[int, List[Any]],
    now: datetime,
    hours: float = 24,
) -> Tuple[SelectionResult, Dict[Tuple[int, str], str]]:
    """Today's picks: fixtures that kick off after `now` and within `hours`."""
    end = now + timedelta(hours=hours)
    in_window = [f for f in fixtures if f.kickoff is not None and now < f.kickoff <= end]
    candidates, bookmaker_for = build_candidates(in_window, prices_by_fixture, now)
    return select_daily_picks(candidates, now=now), bookmaker_for


def pick_card(cand: Candidate, bookmaker: Optional[str]) -> Dict[str, Any]:
    """How one pick is shown to the public. Plain numbers plus a plain reason."""
    fair_odds = 1.0 / cand.model_prob
    edge_pct = round(cand.edge * 100, 1)
    chance = "chance of a draw" if cand.outcome == "Draw" else "chance"
    return {
        "fixture_id": cand.fixture_id,
        "match": cand.match,
        "kickoff_utc": cand.kickoff.isoformat() + "Z" if cand.kickoff else None,
        "pick": cand.outcome,
        "bookmaker": bookmaker,
        "bookmaker_odds": cand.bookmaker_odds,
        "model_probability_pct": round(cand.model_prob * 100, 1),
        "fair_odds": round(fair_odds, 2),
        "edge_pct": edge_pct,
        "reason": (
            f"Our model gives this a {round(cand.model_prob * 100, 1)}% {chance} "
            f"(fair odds {round(fair_odds, 2)}). {bookmaker or 'A bookmaker'} "
            f"pays {cand.bookmaker_odds}. That is an estimated {edge_pct}% edge. "
            f"It is an estimate, not a guarantee."
        ),
    }


def settle_profit(result: Optional[str], odds: float) -> Optional[float]:
    """Profit in units for a flat 1-unit stake. None while unsettled."""
    if result == "won":
        return odds - 1.0
    if result == "lost":
        return -1.0
    if result == "void":
        return 0.0
    return None


def track_record(rows: Iterable[Any]) -> Dict[str, Any]:
    """Summary of PUBLISHED picks only. Each row needs .odds, .edge, .result
    (won / lost / void / None) and .closing_fair_prob (or None).

    Held-for-review and qualified-not-published picks are logged for honesty
    but never counted in the public record.
    """
    rows = list(rows)
    settled = [r for r in rows if r.result in SETTLED_RESULTS]
    graded = [r for r in settled if r.result != "void"]
    wins = sum(1 for r in graded if r.result == "won")
    profit = sum(settle_profit(r.result, r.odds) for r in settled)
    staked = len(graded)  # void stakes are returned, so they are not at risk
    clv_values = [closing_line_value(r.odds, r.closing_fair_prob)
                  for r in rows if r.closing_fair_prob]
    return {
        "picks_published": len(rows),
        "picks_settled": len(settled),
        "wins": wins,
        "losses": len(graded) - wins,
        "voids": len(settled) - len(graded),
        "strike_rate_pct": round(100 * wins / len(graded), 1) if graded else None,
        "profit_units": round(profit, 2),
        "roi_pct": round(100 * profit / staked, 1) if staked else None,
        "average_edge_pct": round(100 * sum(r.edge for r in rows) / len(rows), 1) if rows else None,
        "closing_line_value_pct": (
            round(100 * sum(clv_values) / len(clv_values), 2) if clv_values else None),
        "closing_line_sample": len(clv_values),
        "note": ("Flat 1-unit stakes. A small sample proves nothing; "
                 "closing-line value is the better early signal."),
    }
