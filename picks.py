"""TrueValue Odds - edge and daily pick selection.

Pure functions only (no database, no web framework), so every rule can be
tested without a server.

The rules, in one place:
  * edge = expected value per 1 staked = model probability x bookmaker odds - 1
  * a pick must have edge STRICTLY greater than MIN_EDGE (5%)
  * at most MAX_DAILY_PICKS (4) are published per day, and zero is fine
  * an edge above REVIEW_EDGE (15%) is NOT auto-published: a gap that big is
    far more likely to be a model or data bug than a bookmaker blunder, so it
    is held for a human to check
  * a fixture that has kicked off is never a candidate
  * one pick per fixture: the outcome with the best edge
  * qualified picks that did not make the cap are returned too, so you can log
    them as "qualified but not published"

Times: kickoff and `now` must both be naive UTC (what the database stores).
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

MIN_EDGE = 0.05
REVIEW_EDGE = 0.15
MAX_DAILY_PICKS = 4
OUTCOME_LABELS = ("Home Win", "Draw", "Away Win")
NO_VALUE_MESSAGE = "No value today."


def expected_value(model_prob: float, bookmaker_odds: float) -> float:
    """Expected profit per 1 staked: model_prob * odds - 1.

    This is already net of the bookmaker's margin, so you do NOT remove the
    margin from bookmaker_odds first. (It is identical to bookmaker_odds /
    fair_odds - 1, where fair_odds = 1 / model_prob.)
    """
    if not (0.0 < model_prob <= 1.0):
        raise ValueError(f"model_prob must be in (0, 1], got {model_prob!r}")
    if not bookmaker_odds > 1.0:
        raise ValueError(f"bookmaker_odds must be > 1.0, got {bookmaker_odds!r}")
    return model_prob * bookmaker_odds - 1.0


def devig_proportional(odds: Sequence[float]) -> Tuple[float, ...]:
    """Turn one bookmaker's odds for ALL outcomes of a market into
    probabilities that sum to 1 by scaling the implied probabilities.

    Use this on a sharp book's odds (e.g. Pinnacle) when you want a market
    probability, such as for the closing-line test. Simple proportional
    scaling is the basic method, not the most accurate one.
    """
    if len(odds) < 2 or any(not o > 1.0 for o in odds):
        raise ValueError(f"need two or more odds, each > 1.0, got {list(odds)!r}")
    implied = [1.0 / o for o in odds]
    total = sum(implied)
    return tuple(p / total for p in implied)


def closing_line_value(pick_odds: float, closing_fair_prob: float) -> float:
    """How much better the price you published was than the de-vigged
    closing price. Positive means you beat the closing line.

    CLV = pick_odds * closing_fair_prob - 1
    """
    return expected_value(closing_fair_prob, pick_odds)


@dataclass(frozen=True)
class Candidate:
    fixture_id: int
    match: str
    outcome: str
    model_prob: float
    bookmaker_odds: float
    kickoff: Optional[datetime] = None

    @property
    def edge(self) -> float:
        # Rounded to 6 places so float noise (0.05000000000000004) can never
        # push a borderline pick across the threshold.
        return round(expected_value(self.model_prob, self.bookmaker_odds), 6)


@dataclass
class SelectionResult:
    published: List[Candidate] = field(default_factory=list)
    qualified_not_published: List[Candidate] = field(default_factory=list)
    held_for_review: List[Candidate] = field(default_factory=list)

    @property
    def message(self) -> str:
        return NO_VALUE_MESSAGE if not self.published else ""


def best_candidate(
    fixture_id: int,
    match: str,
    probs: Dict[str, float],
    odds: Dict[str, Optional[float]],
    kickoff: Optional[datetime] = None,
) -> Optional[Candidate]:
    """The outcome with the best edge for one fixture, or None if no outcome
    has both a probability and usable odds. The edge may be negative; the
    threshold is applied later by select_daily_picks."""
    best: Optional[Candidate] = None
    for label in OUTCOME_LABELS:
        p = probs.get(label)
        o = odds.get(label)
        if p is None or o is None or not o > 1.0:
            continue
        cand = Candidate(fixture_id, match, label, p, o, kickoff)
        if best is None or cand.edge > best.edge:
            best = cand
    return best


def select_daily_picks(
    candidates: Sequence[Candidate],
    now: Optional[datetime] = None,
    min_edge: float = MIN_EDGE,
    review_edge: float = REVIEW_EDGE,
    max_picks: int = MAX_DAILY_PICKS,
) -> SelectionResult:
    if max_picks < 0:
        raise ValueError("max_picks cannot be negative")

    # one candidate per fixture: keep the best edge
    by_fixture: Dict[int, Candidate] = {}
    for c in candidates:
        if now is not None and c.kickoff is not None and c.kickoff <= now:
            continue  # already started
        current = by_fixture.get(c.fixture_id)
        if current is None or c.edge > current.edge:
            by_fixture[c.fixture_id] = c

    qualifying = [c for c in by_fixture.values() if c.edge > min_edge]
    held = [c for c in qualifying if c.edge > review_edge]
    eligible = [c for c in qualifying if c.edge <= review_edge]

    # best edge first; fixture_id breaks ties so the order is repeatable
    eligible.sort(key=lambda c: (-c.edge, c.fixture_id))
    held.sort(key=lambda c: (-c.edge, c.fixture_id))

    return SelectionResult(
        published=eligible[:max_picks],
        qualified_not_published=eligible[max_picks:],
        held_for_review=held,
    )
