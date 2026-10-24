"""Tests for analytics.py and picks.py.

Run from this folder with:   pytest -q
Plain assert functions only, so there is nothing to configure.
"""
import math
from datetime import datetime, timedelta

import analytics
import picks
from analytics import MAX_GOALS, MAX_LAMBDA, PoissonEngine, outcome_probabilities
from picks import (
    MAX_DAILY_PICKS,
    MIN_EDGE,
    NO_VALUE_MESSAGE,
    Candidate,
    best_candidate,
    closing_line_value,
    devig_proportional,
    expected_value,
    select_daily_picks,
)

PSL = {"league_avg_home_scored": 1.35, "league_avg_away_scored": 1.05}

# (name, home stats, away stats) - the five mock fixtures from main.py
MOCKS = [
    ("Chiefs v Pirates",
     {"avg_goals_scored_home": 1.70, "avg_goals_conceded_home": 1.10},
     {"avg_goals_scored_away": 1.40, "avg_goals_conceded_away": 0.95}),
    ("Sundowns v Stellenbosch",
     {"avg_goals_scored_home": 2.30, "avg_goals_conceded_home": 0.50},
     {"avg_goals_scored_away": 1.50, "avg_goals_conceded_away": 1.10}),
    ("CT City v SuperSport",
     {"avg_goals_scored_home": 1.45, "avg_goals_conceded_home": 1.20},
     {"avg_goals_scored_away": 1.20, "avg_goals_conceded_away": 1.30}),
    ("AmaZulu v Arrows",
     {"avg_goals_scored_home": 1.10, "avg_goals_conceded_home": 1.40},
     {"avg_goals_scored_away": 1.35, "avg_goals_conceded_away": 1.50}),
    ("Sekhukhune v Polokwane",
     {"avg_goals_scored_home": 1.30, "avg_goals_conceded_home": 1.00},
     {"avg_goals_scored_away": 1.05, "avg_goals_conceded_away": 1.25}),
]

LAMBDA_PAIRS = [
    (0.5, 0.5), (1.0, 1.0), (1.196, 1.467), (1.874, 0.714), (2.5, 0.4),
    (0.3, 3.0), (3.5, 3.5), (MAX_LAMBDA, MAX_LAMBDA),
]


def expect_error(error_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except error_type:
        return
    raise AssertionError(f"expected {error_type.__name__} from {fn.__name__}{args}")


def reference_probs(home_lambda, away_lambda, n=60):
    """Slow, obvious version using factorials and a 61x61 grid, used to
    cross-check the fast engine."""
    def pmf(lmbda, k):
        return math.exp(-lmbda) * lmbda ** k / math.factorial(k)

    home = draw = away = 0.0
    for i in range(n + 1):
        for j in range(n + 1):
            p = pmf(home_lambda, i) * pmf(away_lambda, j)
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    total = home + draw + away
    return home / total, draw / total, away / total


# ---------------------------------------------------------------- analytics

def test_probabilities_sum_to_one():
    for hl, al in LAMBDA_PAIRS:
        ph, pd, pa = outcome_probabilities(hl, al)
        assert abs(ph + pd + pa - 1.0) < 1e-12, (hl, al)
        assert ph > 0 and pd > 0 and pa > 0


def test_matches_slow_reference():
    for hl, al in LAMBDA_PAIRS:
        fast = outcome_probabilities(hl, al)
        slow = reference_probs(hl, al)
        for a, b in zip(fast, slow):
            assert abs(a - b) < 2e-4, (hl, al, fast, slow)


def test_grid_keeps_almost_all_probability_at_the_largest_allowed_lambda():
    kept = sum(analytics._poisson_pmf(MAX_LAMBDA, MAX_GOALS))
    assert kept > 0.999999, kept


def test_old_6x6_grid_lost_probability_new_one_does_not():
    # Sundowns v Stellenbosch: the old 0..5 grid captured only ~98.75%.
    hl, al = PoissonEngine.expected_goals(MOCKS[1][1], MOCKS[1][2], PSL)
    old_mass = sum(analytics._poisson_pmf(hl, 5)[i] * analytics._poisson_pmf(al, 5)[j]
                   for i in range(6) for j in range(6))
    assert old_mass < 0.99
    assert abs(sum(outcome_probabilities(hl, al)) - 1.0) < 1e-12


def test_league_average_teams_get_league_average_goals():
    home = {"avg_goals_scored_home": 1.35, "avg_goals_conceded_home": 1.05}
    away = {"avg_goals_scored_away": 1.05, "avg_goals_conceded_away": 1.35}
    hl, al = PoissonEngine.expected_goals(home, away, PSL)
    assert abs(hl - 1.35) < 1e-12
    assert abs(al - 1.05) < 1e-12


def test_expected_goals_matches_simplified_closed_form():
    # home xG = home_scored * away_conceded / league_home
    # away xG = away_scored * home_conceded / league_away
    for name, home, away in MOCKS:
        hl, al = PoissonEngine.expected_goals(home, away, PSL)
        want_h = home["avg_goals_scored_home"] * away["avg_goals_conceded_away"] / 1.35
        want_a = away["avg_goals_scored_away"] * home["avg_goals_conceded_home"] / 1.05
        assert abs(hl - want_h) < 1e-12, name
        assert abs(al - want_a) < 1e-12, name


def test_swapping_lambdas_swaps_home_and_away():
    ph, pd, pa = outcome_probabilities(1.8, 0.9)
    qh, qd, qa = outcome_probabilities(0.9, 1.8)
    assert abs(ph - qa) < 1e-12 and abs(pa - qh) < 1e-12 and abs(pd - qd) < 1e-12


def test_equal_lambdas_give_equal_home_and_away():
    ph, pd, pa = outcome_probabilities(1.3, 1.3)
    assert abs(ph - pa) < 1e-12


def test_stronger_home_attack_raises_home_win_probability():
    last = 0.0
    for scored in (0.8, 1.1, 1.4, 1.7, 2.0, 2.3):
        home = {"avg_goals_scored_home": scored, "avg_goals_conceded_home": 1.0}
        away = {"avg_goals_scored_away": 1.1, "avg_goals_conceded_away": 1.1}
        ph, _, _ = PoissonEngine.calculate_match_probabilities(home, away, PSL)
        assert ph > last
        last = ph


def test_odds_are_unrounded_inverse_probabilities():
    for name, home, away in MOCKS:
        probs = PoissonEngine.calculate_match_probabilities(home, away, PSL)
        odds = PoissonEngine.calculate_match_odds(home, away, PSL)
        for p, o in zip(probs, odds):
            assert abs(p * o - 1.0) < 1e-12, name
        # fair odds carry no margin: implied probabilities add to exactly 1
        assert abs(sum(1.0 / o for o in odds) - 1.0) < 1e-12, name


def test_bad_inputs_raise_value_error():
    home = {"avg_goals_scored_home": 1.5, "avg_goals_conceded_home": 1.0}
    away = {"avg_goals_scored_away": 1.2, "avg_goals_conceded_away": 1.1}

    expect_error(ValueError, PoissonEngine.expected_goals, home, away,
                 {"league_avg_home_scored": 0, "league_avg_away_scored": 1.05})
    expect_error(ValueError, PoissonEngine.expected_goals, home, away,
                 {"league_avg_home_scored": 1.35, "league_avg_away_scored": -1.0})
    expect_error(ValueError, PoissonEngine.expected_goals,
                 {"avg_goals_scored_home": -0.1, "avg_goals_conceded_home": 1.0}, away, PSL)
    expect_error(ValueError, PoissonEngine.expected_goals,
                 {"avg_goals_scored_home": float("nan"), "avg_goals_conceded_home": 1.0}, away, PSL)
    expect_error(ValueError, PoissonEngine.expected_goals,
                 {"avg_goals_scored_home": "1.5", "avg_goals_conceded_home": 1.0}, away, PSL)
    # a team that has never scored gives lambda = 0: refuse, do not publish 100%
    expect_error(ValueError, PoissonEngine.calculate_match_probabilities,
                 {"avg_goals_scored_home": 0.0, "avg_goals_conceded_home": 1.0}, away, PSL)
    expect_error(ValueError, outcome_probabilities, MAX_LAMBDA + 0.1, 1.0)
    expect_error(ValueError, outcome_probabilities, 1.0, 0.0)


def test_missing_stat_raises_key_error():
    expect_error(KeyError, PoissonEngine.expected_goals,
                 {"avg_goals_scored_home": 1.5}, MOCKS[0][2], PSL)


# -------------------------------------------------------------------- picks

def cand(fixture_id, edge_target, kickoff=None, outcome="Home Win"):
    """Build a Candidate whose edge is edge_target (probability 0.5)."""
    return Candidate(fixture_id, f"Match {fixture_id}", outcome,
                     0.5, 2.0 * (1.0 + edge_target), kickoff)


def test_expected_value_known_value():
    assert abs(expected_value(0.5, 2.2) - 0.10) < 1e-12
    assert abs(expected_value(0.25, 3.0) - (-0.25)) < 1e-12


def test_edge_equals_bookmaker_over_fair_minus_one():
    # main.py computes bookie / fair - 1 where fair = 1 / probability.
    # That is the SAME number as probability x bookie - 1.
    for p in (0.1, 0.33, 0.5, 0.62, 0.9):
        for bookie in (1.2, 2.0, 3.75, 9.0):
            fair = 1.0 / p
            assert abs(expected_value(p, bookie) - (bookie / fair - 1.0)) < 1e-12


def test_expected_value_rejects_bad_input():
    expect_error(ValueError, expected_value, 0.0, 2.0)
    expect_error(ValueError, expected_value, 1.2, 2.0)
    expect_error(ValueError, expected_value, 0.5, 1.0)
    expect_error(ValueError, expected_value, 0.5, 0.9)


def test_threshold_is_strictly_greater_than_five_percent():
    exactly_five = Candidate(1, "A v B", "Home Win", 0.5, 2.1)   # 0.5 x 2.1 - 1 = 0.05
    assert exactly_five.edge == MIN_EDGE
    just_over = Candidate(2, "C v D", "Home Win", 0.5, 2.11)     # 0.055
    result = select_daily_picks([exactly_five, just_over])
    assert [c.fixture_id for c in result.published] == [2]
    assert result.qualified_not_published == []


def test_no_qualifying_fixture_means_no_value_today():
    result = select_daily_picks([cand(1, 0.01), cand(2, -0.10), cand(3, 0.0)])
    assert result.published == []
    assert result.message == NO_VALUE_MESSAGE
    assert select_daily_picks([]).message == NO_VALUE_MESSAGE


def test_cap_keeps_the_four_best_and_records_the_rest():
    cands = [cand(i, e) for i, e in enumerate((0.06, 0.11, 0.08, 0.07, 0.10, 0.09), start=1)]
    result = select_daily_picks(cands)
    assert MAX_DAILY_PICKS == 4
    assert [c.fixture_id for c in result.published] == [2, 5, 6, 3]
    assert [c.fixture_id for c in result.qualified_not_published] == [4, 1]
    assert result.message == ""


def test_cap_is_a_ceiling_not_a_target():
    result = select_daily_picks([cand(1, 0.08), cand(2, 0.06)])
    assert len(result.published) == 2


def test_one_pick_per_fixture_keeps_the_best_outcome():
    home = Candidate(7, "A v B", "Home Win", 0.5, 2.12)    # edge 0.06
    draw = Candidate(7, "A v B", "Draw", 0.3, 4.0)         # edge 0.20 -> review
    away = Candidate(7, "A v B", "Away Win", 0.2, 5.5)     # edge 0.10
    result = select_daily_picks([home, draw, away])
    assert result.published == []
    assert [c.outcome for c in result.held_for_review] == ["Draw"]
    result2 = select_daily_picks([home, away])
    assert [c.outcome for c in result2.published] == ["Away Win"]


def test_huge_edge_is_held_for_review_not_published():
    big = cand(1, 0.50)
    ok = cand(2, 0.07)
    result = select_daily_picks([big, ok])
    assert [c.fixture_id for c in result.published] == [2]
    assert [c.fixture_id for c in result.held_for_review] == [1]
    assert big not in result.qualified_not_published


def test_started_matches_are_never_candidates():
    now = datetime(2026, 10, 24, 15, 0)
    started = cand(1, 0.09, kickoff=now - timedelta(minutes=1))
    kicking_off_now = cand(2, 0.09, kickoff=now)
    upcoming = cand(3, 0.09, kickoff=now + timedelta(hours=2))
    no_time = cand(4, 0.09)
    result = select_daily_picks([started, kicking_off_now, upcoming, no_time], now=now)
    assert sorted(c.fixture_id for c in result.published) == [3, 4]


def test_equal_edges_come_out_in_a_repeatable_order():
    cands = [cand(9, 0.08), cand(3, 0.08), cand(5, 0.08)]
    first = select_daily_picks(cands)
    second = select_daily_picks(list(reversed(cands)))
    assert [c.fixture_id for c in first.published] == [3, 5, 9]
    assert [c.fixture_id for c in second.published] == [3, 5, 9]


def test_best_candidate_chooses_highest_edge_outcome():
    probs = {"Home Win": 0.50, "Draw": 0.28, "Away Win": 0.22}
    odds = {"Home Win": 1.90, "Draw": 3.80, "Away Win": 4.20}
    c = best_candidate(11, "A v B", probs, odds)
    # edges: 0.50x1.90-1 = -0.05, 0.28x3.80-1 = 0.064, 0.22x4.20-1 = -0.076
    assert c is not None and c.outcome == "Draw" and abs(c.edge - 0.064) < 1e-9


def test_best_candidate_skips_missing_or_unusable_odds():
    probs = {"Home Win": 0.5, "Draw": 0.3, "Away Win": 0.2}
    assert best_candidate(1, "A v B", probs, {}) is None
    assert best_candidate(1, "A v B", probs,
                          {"Home Win": None, "Draw": 0.0, "Away Win": 1.0}) is None
    only_away = best_candidate(1, "A v B", probs,
                               {"Home Win": None, "Draw": None, "Away Win": 5.5})
    assert only_away is not None and only_away.outcome == "Away Win"


def test_devig_proportional():
    assert devig_proportional([2.0, 2.0]) == (0.5, 0.5)
    probs = devig_proportional([2.10, 3.40, 3.80])
    assert abs(sum(probs) - 1.0) < 1e-12
    assert probs[0] > probs[1] > probs[2]
    expect_error(ValueError, devig_proportional, [2.0])
    expect_error(ValueError, devig_proportional, [2.0, 1.0])


def test_closing_line_value():
    assert abs(closing_line_value(2.10, 0.50) - 0.05) < 1e-12     # beat the close
    assert closing_line_value(1.90, 0.50) < 0                      # did not


def test_fair_engine_against_a_bookmaker_with_margin_finds_nothing():
    # If the bookmaker's prices equal the model's fair prices less a 6%
    # margin, there is no edge anywhere, so the product must say "no value".
    cands = []
    for i, (name, home, away) in enumerate(MOCKS, start=1):
        ph, pd, pa = PoissonEngine.calculate_match_probabilities(home, away, PSL)
        probs = {"Home Win": ph, "Draw": pd, "Away Win": pa}
        odds = {k: (1.0 / v) / 1.06 for k, v in probs.items()}
        c = best_candidate(i, name, probs, odds)
        assert c is not None and c.edge < 0
        cands.append(c)
    result = select_daily_picks(cands)
    assert result.published == [] and result.message == NO_VALUE_MESSAGE


def test_fair_engine_finds_a_real_edge_when_one_exists():
    name, home, away = MOCKS[1]
    ph, pd, pa = PoissonEngine.calculate_match_probabilities(home, away, PSL)
    probs = {"Home Win": ph, "Draw": pd, "Away Win": pa}
    odds = {"Home Win": (1.0 / ph) * 1.08, "Draw": (1.0 / pd) / 1.06, "Away Win": (1.0 / pa) / 1.06}
    result = select_daily_picks([best_candidate(1, name, probs, odds)])
    assert len(result.published) == 1
    assert result.published[0].outcome == "Home Win"
    assert abs(result.published[0].edge - 0.08) < 1e-6
