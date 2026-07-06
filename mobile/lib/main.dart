import 'package:flutter/material.dart';
import 'models/book.dart';
import 'services/api.dart';
import 'services/logger.dart';
import 'screens/book_detail.dart';
import 'screens/settings.dart';


void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await novelApi.init();
  await appLogger.init();
  appLogger.info('App started');
  runApp(const NovelApp());
}

class NovelApp extends StatelessWidget {
  const NovelApp({super.key});

  @override
  Widget build(BuildContext context) {
    // Global error handler
    FlutterError.onError = (details) {
      FlutterError.presentError(details);
      appLogger.reportCrash(
        details.exceptionAsString(),
        details.stack?.toString() ?? '',
      );
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
        title: const Text('小说工作流'),
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
