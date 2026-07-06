import 'package:flutter/material.dart';
import '../models/stats.dart';
import '../services/api.dart';

class StatsScreen extends StatefulWidget {
  final String bookName;
  const StatsScreen({super.key, required this.bookName});

  @override
  State<StatsScreen> createState() => _StatsScreenState();
}

class _StatsScreenState extends State<StatsScreen> {
  BookStats? _stats;
  List<PipelineHistory> _history = [];
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
      final s = await novelApi.getStats(widget.bookName);
      final h = await novelApi.getPipelineHistory(widget.bookName);
      if (!mounted) return;
      setState(() { _stats = s; _history = h; _loading = false; });
    } catch (e) {
      if (!mounted) return;
      setState(() { _error = e.toString(); _loading = false; });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('统计')),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Text('加载失败: $_error'),
      );
    }
    final s = _stats!;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _sectionTitle('章节概况'),
          const SizedBox(height: 8),
          _statGrid({
            '总章节': s.totalChapters.toString(),
            '已通过': s.approvedChapters.toString(),
            '待审核': s.pendingChapters.toString(),
            '已拒绝': s.rejectedChapters.toString(),
          }),
          const SizedBox(height: 24),
          _sectionTitle('字数统计'),
          const SizedBox(height: 8),
          _statGrid({
            '总字数': _formatWordCount(s.totalWords),
            '平均每章': _formatWordCount(s.avgChapterWords.toInt()),
          }),
          const SizedBox(height: 24),
          _sectionTitle('流水线'),
          const SizedBox(height: 8),
          _statGrid({
            '运行次数': s.pipelineRuns.toString(),
            '失败次数': s.pipelineFails.toString(),
          }),
          if (_history.isNotEmpty) ...[
            const SizedBox(height: 24),
            _sectionTitle('最近运行'),
            const SizedBox(height: 8),
            ..._history.reversed.take(10).map(_buildHistoryItem),
          ],
        ],
      ),
    );
  }

  Widget _sectionTitle(String title) {
    return Text(title, style: const TextStyle(
      fontWeight: FontWeight.bold, fontSize: 16),
    );
  }

  Widget _statGrid(Map<String, String> items) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: items.entries.map((e) => SizedBox(
        width: (MediaQuery.of(context).size.width - 40) / 2,
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              children: [
                Text(e.value,
                  style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
                Text(e.key, style: const TextStyle(color: Colors.grey, fontSize: 12)),
              ],
            ),
          ),
        ),
      )).toList(),
    );
  }

  Widget _buildHistoryItem(PipelineHistory h) {
    Color c;
    switch (h.status) {
      case 'completed': c = Colors.green; break;
      case 'failed': c = Colors.red; break;
      case 'running': c = Colors.orange; break;
      default: c = Colors.grey;
    }
    return ListTile(
      dense: true,
      leading: Icon(Icons.circle, size: 10, color: c),
      title: Text('${h.chapter} — ${h.status}', style: const TextStyle(fontSize: 13)),
      subtitle: h.durationSec > 0
          ? Text('${h.durationSec}s', style: const TextStyle(fontSize: 11))
          : null,
    );
  }

  String _formatWordCount(int n) {
    if (n >= 10000) return '${(n / 10000).toStringAsFixed(1)}万';
    if (n >= 1000) return '${(n / 1000).toStringAsFixed(1)}k';
    return n.toString();
  }
}
