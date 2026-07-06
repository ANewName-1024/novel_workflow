import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../models/book.dart';
import '../models/chapter.dart';
import '../models/outline.dart';
import '../models/stats.dart';
import 'logger.dart';

class NovelApi {
  static const _defaultBase = 'http://8.137.116.121:9080';
  static const _keyBaseUrl = 'novel_base_url';

  String _baseUrl = _defaultBase;
  http.Client? _client;

  Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    _baseUrl = prefs.getString(_keyBaseUrl) ?? _defaultBase;
    _client = http.Client();
  }

  String get baseUrl => _baseUrl;

  Future<void> setBaseUrl(String url) async { /* fixed server URL */ }

  Future<dynamic> _get(String path) async {
    final url = '$_baseUrl$path';
    final r = await http.get(Uri.parse(url),
        headers: {'Accept': 'application/json', 'X-Client-Version': 'apk-1.0.0'});
    if (r.statusCode >= 400) {
      appLogger.logHttpError(url, r.statusCode, r.body);
      throw Exception('HTTP ${r.statusCode}: ${r.body}');
    }
    appLogger.debug('GET $url -> ${r.statusCode}');
    return jsonDecode(r.body);
  }

  Future<dynamic> _post(String path,
      {Map<String, dynamic>? body}) async {
    final url = '$_baseUrl$path';
    final r = await http.post(Uri.parse(url),
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-Client-Version': 'apk-1.0.0',
        },
        body: body != null ? jsonEncode(body) : null);
    if (r.statusCode >= 400) {
      appLogger.logHttpError(url, r.statusCode, r.body);
      throw Exception('HTTP ${r.statusCode}: ${r.body}');
    }
    appLogger.debug('POST $url -> ${r.statusCode}');
    return jsonDecode(r.body);
  }

  // === Projects / Books ===
  /// /api/projects returns a JSON array of book names
  Future<List<Book>> listBooks() async {
    final data = await _get('/api/projects');
    if (data is List) {
      // Old backend: simple array of names
      return data.map((name) => Book(name: name.toString(), displayName: name.toString())).toList();
    }
    if (data is Map) {
      // New backend with details
      final projects = data['projects'] as Map<String, dynamic>?;
      if (projects != null) {
        return projects.entries
            .map((e) => Book.fromJson(e.value as Map<String, dynamic>, e.key))
            .toList();
      }
      // Fallback: map of names
      return data.entries
          .map((e) => Book(name: e.key.toString(), displayName: e.key.toString()))
          .toList();
    }
    throw Exception('Unexpected /api/projects response type: ${data.runtimeType}');
  }

  // === Chapters ===
  Future<List<Chapter>> listChapters(String book) async {
    final data = await _get('/api/history/$book') as Map<String, dynamic>;
    final chapters = data['chapters'] as List<dynamic>?;
    if (chapters == null) return [];
    return chapters
        .map((e) => Chapter.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Chapter> getChapter(String book, String ch) async {
    final data = await _get('/api/chapter/$book/$ch') as Map<String, dynamic>;
    return Chapter.fromJson(data);
  }

  Future<ChapterDiff> getDiff(String book, String ch) async {
    final data = await _get('/api/diff/$book/$ch') as Map<String, dynamic>;
    return ChapterDiff.fromJson(data);
  }

  // === Review ===
  Future<Map<String, dynamic>> getReview(String book, String ch) async {
    return (await _get('/api/review/$book/$ch')) as Map<String, dynamic>;
  }

  Future<void> approveChapter(String book, String ch) async {
    await _post('/api/approve/$book/$ch');
  }

  Future<void> rejectChapter(String book, String ch) async {
    await _post('/api/reject/$book/$ch');
  }

  Future<void> editChapter(String book, String ch, String content) async {
    await _post('/api/edit/$book/$ch', body: {'content': content});
  }

  // === Stats ===
  Future<BookStats> getStats(String book) async {
    final data = await _get('/api/stats/$book') as Map<String, dynamic>;
    return BookStats.fromJson(data);
  }

  // === Outline ===
  Future<Outline> getOutline(String book) async {
    final data = await _get('/api/outline/$book') as Map<String, dynamic>;
    return Outline.fromJson(data);
  }

  // === Pipeline ===
  Future<void> triggerPipeline(String book, String ch) async {
    await _post('/api/queue/$book', body: {'chapter': ch});
  }

  Future<List<PipelineHistory>> getPipelineHistory(String book) async {
    final data = await _get('/api/history/$book') as Map<String, dynamic>;
    final runs = data['pipeline_runs'] as List<dynamic>?;
    if (runs == null) return [];
    return runs
        .map((e) => PipelineHistory.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  void dispose() {
    _client?.close();
  }
}

final novelApi = NovelApi();
