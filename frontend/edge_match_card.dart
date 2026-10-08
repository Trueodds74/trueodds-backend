import 'package:flutter/material.dart';

class EdgeMatchCard extends StatelessWidget {
  final Map<String, dynamic> matchData;

  const EdgeMatchCard({Key? key, required this.matchData}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    // Safely extract names and metrics from your FastAPI JSON map format
    final String matchName = matchData['match'] ?? 'Unknown Fixture';
    final String edgePercentage = matchData['punter_edge_percentage'] ?? '0.0%';
    
    final Map<String, dynamic> trueOdds = matchData['true_odds'] ?? {};
    final Map<String, dynamic> bookieOdds = matchData['bookmaker_odds'] ?? {};

    return Card(
      color: const Color(0xFF1E1E1E), // Dark Charcoal surface background color
      elevation: 3,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Row 1: Header / Category Tags
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Row(
                  children: [
                    Icon(Icons.sports_soccer, color: Colors.grey, size: 16),
                    SizedBox(width: 6),
                    Text(
                      "Data Analytics",
                      style: TextStyle(color: Colors.grey, fontSize: 12, fontWeight: FontWeight.w500),
                    ),
                  ],
                ),
                // Premium Green Highlight Value Edge Badge
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: const Color(0xFF00C853).withOpacity(0.15),
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: const Color(0xFF00C853), width: 1),
                  ),
                  child: Text(
                    "$edgePercentage Edge",
                    style: const TextStyle(
                      color: Color(0xFF00C853),
                      fontSize: 12,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),

            // Row 2: Large Scannable Match Headline
            Text(
              matchName,
              style: const TextStyle(
                color: Colors.white,
                fontSize: 18,
                fontWeight: FontWeight.bold,
                letterSpacing: 0.3,
              ),
            ),
            const SizedBox(height: 16),
            const Divider(color: Colors.white12, height: 1),
            const SizedBox(height: 14),

            // Row 3: Data Analytics Odds Layout Blocks
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                // Column Data Left: Calculated True Odds
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text("True Mathematical Odds", style: TextStyle(color: Colors.grey, fontSize: 11)),
                    const SizedBox(height: 6),
                    Text(
                      "H: ${trueOdds['home'] ?? '-'}  |  D: ${trueOdds['draw'] ?? '-'}  |  A: ${trueOdds['away'] ?? '-'}",
                      style: const TextStyle(color: Colors.white90, fontSize: 13, fontWeight: FontWeight.bold, fontFamily: 'monospace'),
                    ),
                  ],
                ),
                // Column Data Right: Real Bookie Market Odds
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    const Text("Active Market Odds", style: TextStyle(color: Colors.grey, fontSize: 11)),
                    const SizedBox(height: 6),
                    Text(
                      "H: ${bookieOdds['home'] ?? '-'}  |  D: ${bookieOdds['draw'] ?? '-'}  |  A: ${bookieOdds['away'] ?? '-'}",
                      style: const TextStyle(color: Colors.white60, fontSize: 13, fontWeight: FontWeight.w600, fontFamily: 'monospace'),
                    ),
                  ],
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
