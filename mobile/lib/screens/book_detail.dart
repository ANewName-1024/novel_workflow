import 'package:flutter/material.dart';
import '../models/book.dart';
import '../models/chapter.dart';
import '../services/api.dart';
import 'chapter_detail.dart';
import 'outline.dart';
import 'stats.dart';

class BookDetailScreen extends StatefulWidget {
  final Book book;
  const BookDetailScreen({super.key, required this.book});

  @override
  State<BookDetailScreen> createState() => _BookDetailScreenState();
}

class _BookDetailScreenState extends State<BookDetailScreen> {
  List<Chapter> _chapters = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final chapters = await novelApi.listChapters(widget.book.name);
      if (!mounted) return;
      setState(() { _chapters = chapters; _loading = false; });
    } catch (e) {
      if (!mounted) return;
      setState(() { _error = e.toString(); _loading = false; });
    }
  }

  Color _statusColor(String? status) {
    switch (status) {
      case 'approved': return Colors.green;
      case 'rejected': return Colors.red;
      case 'pending': return Colors.orange;
      default: return Colors.grey;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.book.displayName),
        actions: [
          IconButton(
            icon: const Icon(Icons.bar_chart),
            onPressed: () => Navigator.push(context,
                MaterialPageRoute(builder: (_) => StatsScreen(bookName: widget.book.name))),
            tooltip: '统计',
          ),
          IconButton(
            icon: const Icon(Icons.account_tree),
            onPressed: () => Navigator.push(context,
                MaterialPageRoute(builder: (_) => OutlineScreen(bookName: widget.book.name))),
            tooltip: '大纲',
          ),
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('加载失败: $_error'),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: _load,
              icon: const Icon(Icons.refresh),
              label: const Text('重试'),
            ),
          ],
        ),
      );
    }
    if (_chapters.isEmpty) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.article_outlined, size: 64, color: Colors.grey),
            SizedBox(height: 16),
            Text('暂无章节'),
          ],
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView.separated(
        padding: const EdgeInsets.all(12),
        itemCount: _chapters.length,
        separatorBuilder: (_, _) => const Divider(height: 1),
        itemBuilder: (context, index) {
          final ch = _chapters[index];
          return ListTile(
            title: Text(ch.title, style: const TextStyle(fontWeight: FontWeight.w500)),
            subtitle: ch.updatedAt != null
                ? Text('${ch.status} · ${_formatDate(ch.updatedAt!)}',
                    style: const TextStyle(fontSize: 12))
                : Text(ch.status, style: const TextStyle(fontSize: 12)),
            trailing: ch.reviewStatus != null
                ? Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                    decoration: BoxDecoration(
                      color: _statusColor(ch.reviewStatus).withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(
                      ch.reviewStatus!,
                      style: TextStyle(
                        fontSize: 12,
                        color: _statusColor(ch.reviewStatus),
                      ),
                    ),
                  )
                : null,
            onTap: () {
              Navigator.push(context,
                MaterialPageRoute(builder: (_) => ChapterDetailScreen(
                  bookName: widget.book.name,
                  chapter: ch,
                )),
              );
            },
          );
        },
      ),
    );
  }

  String _formatDate(DateTime dt) {
    return '${dt.month}/${dt.day} ${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  }
}
