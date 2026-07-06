import 'package:flutter/material.dart';
import '../models/chapter.dart';
import '../services/api.dart';

class ChapterDetailScreen extends StatefulWidget {
  final String bookName;
  final Chapter chapter;
  const ChapterDetailScreen({
    super.key,
    required this.bookName,
    required this.chapter,
  });

  @override
  State<ChapterDetailScreen> createState() => _ChapterDetailScreenState();
}

class _ChapterDetailScreenState extends State<ChapterDetailScreen> {
  Chapter? _detail;
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
      final ch = await novelApi.getChapter(widget.bookName, widget.chapter.id);
      if (!mounted) return;
      setState(() { _detail = ch; _loading = false; });
    } catch (e) {
      if (!mounted) return;
      setState(() { _error = e.toString(); _loading = false; });
    }
  }

  Future<void> _approve() async {
    try {
      await novelApi.approveChapter(widget.bookName, widget.chapter.id);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('已批准'), backgroundColor: Colors.green),
      );
      _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('操作失败: $e'), backgroundColor: Colors.red),
      );
    }
  }

  Future<void> _reject() async {
    try {
      await novelApi.rejectChapter(widget.bookName, widget.chapter.id);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('已拒绝'), backgroundColor: Colors.orange),
      );
      _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('操作失败: $e'), backgroundColor: Colors.red),
      );
    }
  }

  Future<void> _runPipeline() async {
    try {
      await novelApi.triggerPipeline(widget.bookName, widget.chapter.id);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('流水线已触发'), backgroundColor: Colors.indigo),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('触发失败: $e'), backgroundColor: Colors.red),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final title = _detail?.title ?? widget.chapter.title;
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: _buildBody(),
      floatingActionButton: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          FloatingActionButton.small(
            heroTag: 'approve',
            onPressed: _approve,
            backgroundColor: Colors.green,
            child: const Icon(Icons.check, color: Colors.white),
          ),
          const SizedBox(width: 8),
          FloatingActionButton.small(
            heroTag: 'reject',
            onPressed: _reject,
            backgroundColor: Colors.orange,
            child: const Icon(Icons.close, color: Colors.white),
          ),
          const SizedBox(width: 8),
          FloatingActionButton.small(
            heroTag: 'pipeline',
            onPressed: _runPipeline,
            backgroundColor: Colors.indigo,
            child: const Icon(Icons.play_arrow, color: Colors.white),
          ),
        ],
      ),
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

    final content = _detail?.content ?? '(无内容)';
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (_detail?.reviewStatus != null)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              decoration: BoxDecoration(
                color: Colors.indigo.withValues(alpha: 0.1),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text('审核状态: ${_detail!.reviewStatus}',
                  style: const TextStyle(fontWeight: FontWeight.w500)),
            ),
          const SizedBox(height: 16),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: Colors.grey.withValues(alpha: 0.05),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: Colors.grey.withValues(alpha: 0.2)),
            ),
            child: SelectableText(
              content,
              style: const TextStyle(fontSize: 15, height: 1.8),
            ),
          ),
          const SizedBox(height: 80),
        ],
      ),
    );
  }
}
