import 'dart:convert';
import 'package:http/http.dart' as http;

class TrueOddsApiService {
  // Your live production Render web address
  static const String baseUrl = "https://trueodds-backend.onrender.com";

  // 1. Fetches the clean, ordered mathematical dashboard edges feed
  Future<Map<String, dynamic>> fetchDashboardEdges() async {
    final url = Uri.parse("$baseUrl/dashboard/edges");
    
    try {
      final response = await http.get(url, headers: {"Accept": "application/json"});
      
      if (response.statusCode == 200) {
        return jsonDecode(response.body);
      } else {
        throw Exception("Failed to sync with backend feed. Server Code: ${response.statusCode}");
      }
    } catch (e) {
      throw Exception("Network connection timeout tracking database endpoint: $e");
    }
  }

  // 2. Fetches the smart calculated multi-bet ticket slip layout
  Future<Map<String, dynamic>> fetchSmartAccumulatorSlip({int legs = 3}) async {
    final url = Uri.parse("$baseUrl/accumulator/smart-slip?legs=$legs");
    
    try {
      final response = await http.get(url, headers: {"Accept": "application/json"});
      
      if (response.statusCode == 200) {
        return jsonDecode(response.body);
      } else {
        throw Exception("Failed to compile accumulator ticket. Server Code: ${response.statusCode}");
      }
    } catch (e) {
      throw Exception("Network connection timeout tracking accumulator endpoint: $e");
    }
  }
}
