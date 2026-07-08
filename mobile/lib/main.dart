import 'package:flutter/material.dart';
import 'models/book.dart';
import 'services/api.dart';
import 'services/logger.dart';
import 'screens/book_detail.dart';
import 'screens/settings.dart';
import 'dart:ui' as ui;

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await novelApi.init();
  await appLogger.init();
  appLogger.info('App started', ctx: {
    'app_version': appLogger.appVersion,
    'package': appLogger.packageName,
    'device': appLogger.deviceId,
  });
  runApp(const NovelApp());
}

class NovelApp extends StatelessWidget {
  const NovelApp({super.key});

  @override
  Widget build(BuildContext context) {
    // ── Global error handler: framework errors (build / layout / paint) ──
    FlutterError.onError = (details) {
      FlutterError.presentError(details);
      // Log every framework error to server.
      appLogger.reportFlutterError(
        details.exceptionAsString(),
        details.stack?.toString() ?? '',
        context: {
          'library': details.library,
          'context': details.context?.toString(),
          'silent': details.silent,
        },
      );
    };

    // ── Custom ErrorWidget: show a friendly retry card instead of the
    // dreaded red/yellow "RenderFlex overflowed" box. The crash is still
    // recorded above via FlutterError.onError.
    ErrorWidget.builder = (FlutterErrorDetails details) {
      return _ErrorCard(
        message: details.exceptionAsString(),
        stack: details.stack?.toString(),
      );
    };

    // ── PlatformDispatcher: catches uncaught async errors (outside Flutter
    // framework, e.g. timer callbacks, futures, isolates).
    ui.PlatformDispatcher.instance.onError = (error, stack) {
      appLogger.reportAsyncError(error, stack);
      return true; // handled; do not crash
    };

    return MaterialApp(
      title: '小说工作流',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: Colors.indigo,
        useMaterial3: true,
        brightness: Brightness.light,
        appBarTheme: const AppBarTheme(
          centerTitle: true,
          elevation: 1,
        ),
      ),
      home: const HomeScreen(),
    );
  }
}

/// Friendly fallback widget shown when a build/layout/paint error occurs.
/// The actual crash is already uploaded to the server via FlutterError.onError.
class _ErrorCard extends StatelessWidget {
  final String message;
  final String? stack;
  const _ErrorCard({required this.message, this.stack});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.orange.shade50,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.warning_amber, color: Colors.orange.shade800, size: 24),
                const SizedBox(width: 8),
                const Text('界面渲染出错', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              '已上传到服务器。返回上一级或重启应用。',
              style: TextStyle(color: Colors.orange.shade900, fontSize: 12),
            ),
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(4),
                border: Border.all(color: Colors.orange.shade200),
              ),
              child: Text(
                message,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 11),
                maxLines: 6,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  List<Book> _books = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final books = await novelApi.listBooks();
      setState(() {
        _books = books;
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Color _statusColor(String? status) {
    switch (status) {
      case 'running':
        return Colors.orange;
      case 'completed':
        return Colors.green;
      case 'failed':
        return Colors.red;
      default:
        return Colors.grey;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('小说工作流'),
            const SizedBox(width: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
              decoration: BoxDecoration(
                color: Colors.white.withValues(alpha: 0.25),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                'v${appLogger.appVersion}',
                style: const TextStyle(fontSize: 11, color: Colors.white, fontWeight: FontWeight.w500),
              ),
            ),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _load,
            tooltip: '刷新',
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            onPressed: () async {
              await Navigator.push(
                  context, MaterialPageRoute(builder: (_) => const SettingsScreen()));
              _load();
            },
            tooltip: '设置',
          ),
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.cloud_off, size: 64, color: Colors.grey),
            const SizedBox(height: 16),
            Text('连接失败', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Text(_error!, style: const TextStyle(color: Colors.grey)),
            const SizedBox(height: 24),
            FilledButton.icon(
              onPressed: _load,
              icon: const Icon(Icons.refresh),
              label: const Text('重试'),
            ),
          ],
        ),
      );
    }
    if (_books.isEmpty) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.book_outlined, size: 64, color: Colors.grey),
            SizedBox(height: 16),
            Text('暂无项目', style: TextStyle(color: Colors.grey)),
          ],
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView.builder(
        padding: const EdgeInsets.all(12),
        itemCount: _books.length,
        itemBuilder: (context, index) => _buildBookCard(_books[index]),
      ),
    );
  }

  Widget _buildBookCard(Book book) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () {
          Navigator.push(
            context,
            MaterialPageRoute(
              builder: (_) => BookDetailScreen(book: book),
            ),
          );
        },
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      book.displayName,
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ),
                  if (book.lastPipelineStatus != null)
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(
                        color: _statusColor(book.lastPipelineStatus)
                            .withValues(alpha: 0.15),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text(
                        book.lastPipelineStatus!,
                        style: TextStyle(
                          fontSize: 12,
                          color: _statusColor(book.lastPipelineStatus),
                        ),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  _statChip('篇章', book.totalChapters.toString(), Colors.blue),
                  const SizedBox(width: 8),
                  _statChip('待审', book.pendingReviews.toString(), Colors.orange),
                  const SizedBox(width: 8),
                  _statChip('已过', book.approved.toString(), Colors.green),
                  const SizedBox(width: 8),
                  _statChip('已拒', book.rejected.toString(), Colors.red),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _statChip(String label, String value, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(value,
              style: TextStyle(
                  fontWeight: FontWeight.bold,
                  fontSize: 13,
                  color: color)),
          const SizedBox(width: 4),
          Text(label, style: const TextStyle(fontSize: 11, color: Colors.grey)),
        ],
      ),
    );
  }
}
