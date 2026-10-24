"""TrueValue Odds - Poisson match engine.

What changed from the first version
-----------------------------------
1. The score grid is now 0..MAX_GOALS (25) per team instead of 0..5, and the
   result is normalised so Home + Draw + Away = 1 exactly. The old 6x6 grid
   dropped 0.2%-1.3% of the probability, which understated every outcome and
   therefore understated every edge by about the same amount.
2. Probabilities are returned (calculate_match_probabilities). Odds are
   1 / probability and are NOT rounded here, because rounding odds to 2
   decimals before computing an edge shifts the edge by up to ~0.3 points.
   Round only when you display.
3. Inputs are validated. A bad input raises ValueError instead of quietly
   producing a believable-looking number.

What this is NOT
----------------
It is still plain independent Poisson. It has no Dixon-Coles low-score
correction, no recency weighting and no small-sample shrinkage. Plain
independent Poisson usually understates draws, so treat Draw edges with the
most suspicion.
"""
import math
from typing import Dict, List, Tuple

MAX_GOALS = 25   # grid covers 0..MAX_GOALS goals for each team
MAX_LAMBDA = 6.0  # a bigger expected-goals figure means bad input data


def _validate_number(name: str, value: float, *, allow_zero: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{name} must be {'>= 0' if allow_zero else '> 0'}, got {value!r}")
    return float(value)


def _poisson_pmf(lmbda: float, max_goals: int = MAX_GOALS) -> List[float]:
    """P(X = k) for k = 0..max_goals, built with the recurrence
    p(k) = p(k-1) * lambda / k, which avoids huge factorials."""
    probs = [math.exp(-lmbda)]
    for k in range(1, max_goals + 1):
        probs.append(probs[-1] * lmbda / k)
    return probs


def outcome_probabilities(home_lambda: float, away_lambda: float) -> Tuple[float, float, float]:
    """(P(home win), P(draw), P(away win)) for independent Poisson goals,
    normalised so the three sum to 1."""
    home_lambda = _validate_number("home_lambda", home_lambda, allow_zero=False)
    away_lambda = _validate_number("away_lambda", away_lambda, allow_zero=False)
    if home_lambda > MAX_LAMBDA or away_lambda > MAX_LAMBDA:
        raise ValueError(
            f"expected goals above {MAX_LAMBDA} look like bad input "
            f"(home={home_lambda}, away={away_lambda})"
        )

    home_pmf = _poisson_pmf(home_lambda)
    away_pmf = _poisson_pmf(away_lambda)

    home_win = draw = away_win = 0.0
    for home_goals, p_home in enumerate(home_pmf):
        for away_goals, p_away in enumerate(away_pmf):
            p = p_home * p_away
            if home_goals > away_goals:
                home_win += p
            elif home_goals == away_goals:
                draw += p
            else:
                away_win += p

    total = home_win + draw + away_win
    return home_win / total, draw / total, away_win / total


class PoissonEngine:
    @staticmethod
    def expected_goals(
        home_team_stats: Dict[str, float],
        away_team_stats: Dict[str, float],
        league_averages: Dict[str, float],
    ) -> Tuple[float, float]:
        """Expected goals (lambda) for the home and away team.

        Same formula as the first version:
          home xG = (home scored / league home avg) * (away conceded / league home avg) * league home avg
          away xG = (away scored / league away avg) * (home conceded / league away avg) * league away avg
        """
        league_home = _validate_number(
            "league_avg_home_scored", league_averages["league_avg_home_scored"], allow_zero=False)
        league_away = _validate_number(
            "league_avg_away_scored", league_averages["league_avg_away_scored"], allow_zero=False)

        home_scored = _validate_number(
            "avg_goals_scored_home", home_team_stats["avg_goals_scored_home"], allow_zero=True)
        home_conceded = _validate_number(
            "avg_goals_conceded_home", home_team_stats["avg_goals_conceded_home"], allow_zero=True)
        away_scored = _validate_number(
            "avg_goals_scored_away", away_team_stats["avg_goals_scored_away"], allow_zero=True)
        away_conceded = _validate_number(
            "avg_goals_conceded_away", away_team_stats["avg_goals_conceded_away"], allow_zero=True)

        home_attack = home_scored / league_home
        home_defense = home_conceded / league_away
        away_attack = away_scored / league_away
        away_defense = away_conceded / league_home

        home_lambda = home_attack * away_defense * league_home
        away_lambda = away_attack * home_defense * league_away
        return home_lambda, away_lambda

    @staticmethod
    def calculate_match_probabilities(
        home_team_stats: Dict[str, float],
        away_team_stats: Dict[str, float],
        league_averages: Dict[str, float],
    ) -> Tuple[float, float, float]:
        """(P(home), P(draw), P(away)), summing to 1."""
        home_lambda, away_lambda = PoissonEngine.expected_goals(
            home_team_stats, away_team_stats, league_averages)
        return outcome_probabilities(home_lambda, away_lambda)

    @staticmethod
    def calculate_match_odds(
        home_team_stats: Dict[str, float],
        away_team_stats: Dict[str, float],
        league_averages: Dict[str, float],
    ) -> Tuple[float, float, float]:
        """Fair decimal odds (home, draw, away) = 1 / probability.

        Same name and argument order as before, so main.py keeps working, but
        the odds are no longer rounded and the 99.0 cap is gone.
        """
        p_home, p_draw, p_away = PoissonEngine.calculate_match_probabilities(
            home_team_stats, away_team_stats, league_averages)
        return 1.0 / p_home, 1.0 / p_draw, 1.0 / p_away
