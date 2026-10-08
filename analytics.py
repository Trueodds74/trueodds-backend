import math
from typing import Dict, Tuple

class PoissonEngine:
    @staticmethod
    def calculate_match_odds(
        home_team_stats: Dict[str, float], 
        away_team_stats: Dict[str, float], 
        league_averages: Dict[str, float]
    ) -> Tuple[float, float, float]:
        """
        Calculates pure mathematical True Odds using Poisson Distribution.
        Formula: P(x; λ) = (λ^x * e^-λ) / x!
        """
        # 1. Calculate Home Team Attack and Defense Strength
        home_attack = home_team_stats["avg_goals_scored_home"] / league_averages["league_avg_home_scored"]
        home_defense = home_team_stats["avg_goals_conceded_home"] / league_averages["league_avg_away_scored"]
        
        # 2. Calculate Away Team Attack and Defense Strength
        away_attack = away_team_stats["avg_goals_scored_away"] / league_averages["league_avg_away_scored"]
        away_defense = away_team_stats["avg_goals_conceded_away"] / league_averages["league_avg_home_scored"]
        
        # 3. Calculate Expected Goals (xG / Lambda) for this match
        home_lambda = home_attack * away_defense * league_averages["league_avg_home_scored"]
        away_lambda = away_attack * home_defense * league_averages["league_avg_away_scored"]
        
        # Helper function to compute basic Poisson probability points
        def poisson_probability(lmbda: float, goals: int) -> float:
            return (math.pow(lmbda, goals) * math.exp(-lmbda)) / math.factorial(goals)
            
        prob_home_win = 0.0
        prob_draw = 0.0
        prob_away_win = 0.0
        
        # 4. Generate a 6x6 matrix of possible goal lines (0 to 5 goals per team)
        for home_goals in range(6):
            for away_goals in range(6):
                p_home = poisson_probability(home_lambda, home_goals)
                p_away = poisson_probability(away_lambda, away_goals)
                combined_prob = p_home * p_away
                
                if home_goals > away_goals:
                    prob_home_win += combined_prob
                elif home_goals == away_goals:
                    prob_draw += combined_prob
                else:
                    prob_away_win += combined_prob
                    
        # 5. Convert probabilities directly into clean Decimal Odds
        true_home_odds = round(1.0 / prob_home_win, 2) if prob_home_win > 0 else 99.0
        true_draw_odds = round(1.0 / prob_draw, 2) if prob_draw > 0 else 99.0
        true_away_odds = round(1.0 / prob_away_win, 2) if prob_away_win > 0 else 99.0
        
        return true_home_odds, true_draw_odds, true_away_odds
