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

  String get baseUrl => '$_defaultBase';

  Future<void> setBaseUrl(String url) async {
    // Ignore â€?server URL is fixed for this version
  }

  Future<Map<String, dynamic>> _get(String path) async {
    final url = '$_baseUrl$path';
    final r = await http.get(Uri.parse(url),
        headers: {'Accept': 'application/json', 'X-Client-Version': 'apk-1.0.0'});
    if (r.statusCode >= 400) {
      appLogger.logHttpError(url, r.statusCode, r.body);
      throw Exception('HTTP ${r.statusCode}: ${r.body}');
    }
    appLogger.debug('GET $url â†?${r.statusCode}');
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> _post(String path,
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
    appLogger.debug('POST $url â†?${r.statusCode}');
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  // === Projects / Books ===
  Future<List<Book>> listBooks() async {
    final data = await _get('/api/projects');
    final projects = data['projects'] as Map<String, dynamic>;
    return projects.entries
        .map((e) => Book.fromJson(e.value as Map<String, dynamic>, e.key))
        .toList();
  }

  // === Chapters ===
  Future<List<Chapter>> listChapters(String book) async {
    final data = await _get('/api/history/$book');
    return (data['chapters'] as List<dynamic>)
        .map((e) => Chapter.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Chapter> getChapter(String book, String ch) async {
    final data = await _get('/api/chapter/$book/$ch');
    return Chapter.fromJson(data);
  }

  Future<ChapterDiff> getDiff(String book, String ch) async {
    final data = await _get('/api/diff/$book/$ch');
    return ChapterDiff.fromJson(data);
  }

  // === Review ===
  Future<Map<String, dynamic>> getReview(String book, String ch) async {
    return _get('/api/review/$book/$ch');
  }

  Future<void> approveChapter(String book, String ch) async {
    await _post('/api/approve/$book/$ch');
  }

  Future<void> rejectChapter(String book, String ch) async {
    await _post('/api/reject/$book/$ch');
  }

  Future<void> editChapter(
      String book, String ch, String content) async {
    await _post('/api/edit/$book/$ch', body: {'content': content});
  }

  // === Stats ===
  Future<BookStats> getStats(String book) async {
    final data = await _get('/api/stats/$book');
    return BookStats.fromJson(data);
  }

  // === Outline ===
  Future<Outline> getOutline(String book) async {
    final data = await _get('/api/outline/$book');
    return Outline.fromJson(data);
  }

  // === Pipeline ===
  Future<void> triggerPipeline(String book, String ch) async {
    await _post('/api/queue/$book', body: {'chapter': ch});
  }

  Future<List<PipelineHistory>> getPipelineHistory(String book) async {
    final data = await _get('/api/history/$book');
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
