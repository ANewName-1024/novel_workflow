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
    try {
      return jsonDecode(r.body);
    } on FormatException {
      // Server may have returned a non-JSON body (e.g. text/plain chapter
      // markdown, or an HTML error page). Surface a clear error instead
      // of letting jsonDecode crash the caller.
      appLogger.warn('GET $url returned non-JSON body', ctx: {
        'len': r.body.length,
        'preview': r.body.substring(0, r.body.length > 200 ? 200 : r.body.length),
      });
      throw Exception('非 JSON 响应 (HTTP ${r.statusCode}): '
          '${r.body.substring(0, r.body.length > 80 ? 80 : r.body.length)}');
    }
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
    try {
      return jsonDecode(r.body);
    } on FormatException {
      appLogger.warn('POST $url returned non-JSON body', ctx: {
        'len': r.body.length,
        'preview': r.body.substring(0, r.body.length > 200 ? 200 : r.body.length),
      });
      throw Exception('非 JSON 响应 (HTTP ${r.statusCode}): '
          '${r.body.substring(0, r.body.length > 80 ? 80 : r.body.length)}');
    }
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

  /// Returns chapter detail. The server returns the body as text/plain
  /// markdown, NOT JSON, so we use getChapterRaw (Accept: text/plain)
  /// and wrap it into a Chapter object.
  Future<Chapter> getChapter(String book, String ch) async {
    final raw = await getChapterRaw(book, ch);
    return Chapter(
      id: ch,
      title: ch,  // server doesn't include title in text body
      content: raw is String ? raw : raw.toString(),
      status: 'loaded',
    );
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

  Future<List<dynamic>> aiSuggestOutline(String book, {int count = 3,
      int? nextNum, String? provider, String? model}) async {
    final body = <String, dynamic>{'count': count};
    if (nextNum != null) body['next_num'] = nextNum;
    if (provider != null) body['llm_provider'] = provider;
    if (model != null) body['llm_model'] = model;
    final data = await _post('/api/outline/$book/ai-suggest',
        body: body);
    return (data['chapters'] as List<dynamic>?) ?? [];
  }

  Future<Map<String, dynamic>> aiExpandOutline(String book, {required String title, required String summary, String? provider, String? model}) async {
    final body = <String, dynamic>{'title': title, 'summary': summary};
    if (provider != null) body['llm_provider'] = provider;
    if (model != null) body['llm_model'] = model;
    final data = await _post('/api/outline/$book/ai-expand',
        body: body);
    return (data as Map<String, dynamic>);
  }

  Future<void> saveOutlineNode(String book, String chapterId, Map<String, dynamic> patch) async {
    await _post('/api/outline/$book/node/$chapterId',
        body: patch);
  }

  Future<void> addOutlineNode(String book, Map<String, dynamic> node) async {
    await _post('/api/outline/$book/node', body: node);
  }

  Future<void> deleteOutlineNode(String book, String chapterId) async {
    await _post('/api/outline/$book/node/$chapterId/delete', body: {'id': chapterId});
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

  // === LLM Config ===
  Future<Map<String, dynamic>> listLLMProviders() async {
    final data = await _get('/api/llm/providers') as Map<String, dynamic>;
    return data;
  }

  Future<Map<String, dynamic>> llmHealthCheck({String? provider, String? model, String? book}) async {
    final data = await _post('/api/llm/health',
        body: {
          if (provider != null) 'provider': provider,
          if (model != null) 'model': model,
          if (book != null) 'book': book,
        });
    return (data as Map<String, dynamic>);
  }

  Future<Map<String, dynamic>> getBookConfig(String book) async {
    final data = await _get('/api/config/$book') as Map<String, dynamic>;
    return data;
  }

  Future<void> saveBookConfig(String book, Map<String, dynamic> patch) async {
    await _post('/api/config/$book', body: patch);
  }

  // === Chapter edit + diff ===
  Future<Map<String, dynamic>> editChapter(String book, String ch, String content, {String? notes, bool apply = true}) async {
    final data = await _post('/api/edit/$book/$ch',
        body: {'text': content, if (notes != null) 'notes': notes, 'apply': apply});
    return data as Map<String, dynamic>;
  }

  Future<dynamic> getChapterRaw(String book, String ch) async {
    final url = '$_baseUrl/api/chapter/$book/$ch';
    final r = await http.get(Uri.parse(url),
        headers: {'Accept': 'text/plain', 'X-Client-Version': 'apk-1.0.0'});
    if (r.statusCode >= 400) {
      throw Exception('HTTP ${r.statusCode}: ${r.body}');
    }
    return r.body;
  }

  void dispose() {
    _client?.close();
  }
}

final novelApi = NovelApi();
