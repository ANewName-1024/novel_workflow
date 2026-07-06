import 'dart:convert';
import 'dart:io';
import 'dart:async';
import 'package:shared_preferences/shared_preferences.dart';
import 'api.dart';

enum LogLevel { debug, info, warn, error, fatal }

class LogEntry {
  final String id;
  final double ts;
  final LogLevel level;
  final String deviceId;
  final String deviceModel;
  final String appVersion;
  final String msg;
  final String stack;
  final Map<String, dynamic> context;

  LogEntry({
    required this.id,
    required this.ts,
    required this.level,
    required this.deviceId,
    required this.deviceModel,
    required this.appVersion,
    required this.msg,
    this.stack = '',
    this.context = const {},
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'ts': ts,
        'level': level.name,
        'device_id': deviceId,
        'device_model': deviceModel,
        'app_version': appVersion,
        'msg': msg,
        'stack': stack,
        'context': context,
      };

  factory LogEntry.fromJson(Map<String, dynamic> j) => LogEntry(
        id: j['id'] ?? '',
        ts: (j['ts'] ?? 0).toDouble(),
        level: LogLevel.values.firstWhere(
            (l) => l.name == j['level'],
            orElse: () => LogLevel.info),
        deviceId: j['device_id'] ?? '',
        deviceModel: j['device_model'] ?? '',
        appVersion: j['app_version'] ?? '',
        msg: j['msg'] ?? '',
        stack: j['stack'] ?? '',
        context: (j['context'] as Map<String, dynamic>?) ?? {},
      );
}

class AppLogger {
  static final AppLogger _instance = AppLogger._();
  factory AppLogger() => _instance;
  AppLogger._();

  final List<LogEntry> _pendingBatch = [];
  final List<LogEntry> _localHistory = [];
  static const int _maxLocalHistory = 500;
  static const int _batchSize = 20;
  static const Duration _flushInterval = Duration(seconds: 30);

  String _deviceId = 'flutter-unknown';
  String _deviceModel = '';
  String _appVersion = '1.0.0+1';
  Timer? _flushTimer;
  bool _initialized = false;
  bool _flushInProgress = false;

  Future<void> init() async {
    if (_initialized) return;
    _initialized = true;

    _deviceModel = '${Platform.operatingSystem} ${Platform.operatingSystemVersion}';

    final prefs = await SharedPreferences.getInstance();
    _deviceId = prefs.getString('_log_device_id') ?? '';
    if (_deviceId.isEmpty) {
      _deviceId = 'android-${DateTime.now().millisecondsSinceEpoch}';
      await prefs.setString('_log_device_id', _deviceId);
    }

    _appVersion = prefs.getString('_app_version') ?? '1.0.0+1';
    _flushTimer = Timer.periodic(_flushInterval, (_) => _flushBatch());
  }

  String get deviceId => _deviceId;

  void _log(LogLevel level, String msg, {String? stack, Map<String, dynamic>? context}) {
    final entry = LogEntry(
      id: DateTime.now().millisecondsSinceEpoch.toString(),
      ts: DateTime.now().millisecondsSinceEpoch / 1000,
      level: level,
      deviceId: _deviceId,
      deviceModel: _deviceModel,
      appVersion: _appVersion,
      msg: msg,
      stack: stack ?? '',
      context: context ?? {},
    );

    _localHistory.add(entry);
    if (_localHistory.length > _maxLocalHistory) _localHistory.removeAt(0);

    _pendingBatch.add(entry);
    if (_pendingBatch.length >= _batchSize) _flushBatch();
  }

  void debug(String msg, {Map<String, dynamic>? ctx}) => _log(LogLevel.debug, msg, context: ctx);
  void info(String msg, {Map<String, dynamic>? ctx}) => _log(LogLevel.info, msg, context: ctx);
  void warn(String msg, {String? stack, Map<String, dynamic>? ctx}) => _log(LogLevel.warn, msg, stack: stack, context: ctx);
  void error(String msg, {String? stack, Map<String, dynamic>? ctx}) => _log(LogLevel.error, msg, stack: stack, context: ctx);
  void fatal(String msg, {String? stack, Map<String, dynamic>? ctx}) => _log(LogLevel.fatal, msg, stack: stack, context: ctx);

  void logHttpError(String url, int statusCode, String body) {
    error('HTTP $statusCode: $url', ctx: {
      'url': url,
      'status': statusCode,
      'body': body.length > 500 ? '${body.substring(0, 500)}...' : body,
    });
  }

  Future<void> _flushBatch() async {
    if (_pendingBatch.isEmpty || _flushInProgress) return;
    _flushInProgress = true;
    final batch = List<LogEntry>.from(_pendingBatch);
    _pendingBatch.clear();

    try {
      final payload = jsonEncode({
        'entries': batch.map((e) => e.toJson()).toList(),
      });

      final client = HttpClient();
      client.connectionTimeout = const Duration(seconds: 10);
      final uri = Uri.parse('${novelApi.baseUrl}/api/app-log/batch');
      final request = await client.postUrl(uri).timeout(const Duration(seconds: 10));
      request.headers.set('Content-Type', 'application/json');
      request.contentLength = payload.length;
      request.write(payload);
      final response = await request.close().timeout(const Duration(seconds: 10));
      await response.drain();
      client.close();
    } catch (e) {
      // Re-queue on network/timeout error
      _pendingBatch.insertAll(0, batch);
      if (_pendingBatch.length > _batchSize * 2) {
        _pendingBatch.removeRange(0, _pendingBatch.length - _batchSize * 2);
      }
    } finally {
      _flushInProgress = false;
    }
  }

  Future<void> flushNow() async => _flushBatch();

  void reportCrash(String msg, String stack) {
    final entry = LogEntry(
      id: 'crash-${DateTime.now().millisecondsSinceEpoch}',
      ts: DateTime.now().millisecondsSinceEpoch / 1000,
      level: LogLevel.fatal,
      deviceId: _deviceId,
      deviceModel: _deviceModel,
      appVersion: _appVersion,
      msg: 'CRASH: $msg',
      stack: stack,
    );
    _pendingBatch.add(entry);
    _flushBatch();
  }

  List<LogEntry> get recentLogs => _localHistory.reversed.take(100).toList();
}

final appLogger = AppLogger();
