import 'package:flutter/material.dart';
import '../models/chapter.dart';
import '../services/api.dart';
import '../services/logger.dart';

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
  bool _editing = false;
  final TextEditingController _editCtrl = TextEditingController();
  ChapterDiff? _diffData;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _editCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final ch = await novelApi.getChapter(widget.bookName, widget.chapter.id);
      if (!mounted) return;
      setState(() { _detail = ch; _editCtrl.text = ch.content ?? ''; _loading = false; });
    } catch (e) {
      if (!mounted) return;
      setState(() { _error = e.toString(); _loading = false; });
    }
  }

  Future<void> _loadDiff() async {
    try {
      final diff = await novelApi.getDiff(widget.bookName, widget.chapter.id);
      if (!mounted) return;
      setState(() => _diffData = diff);
    } catch (e) {
      appLogger.warn('diff load', ctx: {'err': e.toString()});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Diff 加载失败: $e')));
    }
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final data = await novelApi.editChapter(
        widget.bookName, widget.chapter.id, _editCtrl.text,
        notes: 'mobile edit',
      );
      if (!mounted) return;
      final applied = data['applied'] == true;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(applied ? '已保存并应用 ✅' : '已保存评论，待应用'),
          backgroundColor: Colors.green,
        ),
      );
      setState(() { _editing = false; });
      _load();
      _loadDiff();
    } catch (e) {
      appLogger.error('edit save', ctx: {'err': e.toString()});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('保存失败: $e')));
    } finally {
      if (mounted) setState(() => _saving = false);
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
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('操作失败: $e')));
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
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('操作失败: $e')));
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
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('触发失败: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final title = _detail?.title ?? widget.chapter.title;
    return Scaffold(
      appBar: AppBar(
        title: Text(title),
        actions: [
          if (!_editing && _detail != null)
            IconButton(
              icon: const Icon(Icons.edit),
              tooltip: '编辑',
              onPressed: () => setState(() => _editing = true),
            ),
          if (_editing)
            IconButton(
              icon: _saving
                  ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Icon(Icons.save),
              tooltip: '保存 (apply=true)',
              onPressed: _saving ? null : _save,
            ),
          if (_editing)
            IconButton(
              icon: const Icon(Icons.close),
              tooltip: '取消编辑',
              onPressed: _saving ? null : () {
                setState(() {
                  _editing = false;
                  _editCtrl.text = _detail?.content ?? '';
                });
              },
            ),
          IconButton(
            icon: const Icon(Icons.difference),
            tooltip: '查看 Diff',
            onPressed: _loadDiff,
          ),
        ],
      ),
      body: _buildBody(),
      floatingActionButton: _editing ? null : Row(
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

    if (_editing) return _buildEditor();

    return Column(
      children: [
        if (_diffData != null) _buildDiffView(_diffData!),
        Expanded(child: _buildReadOnly()),
      ],
    );
  }

  Widget _buildEditor() {
    final wc = _countWords(_editCtrl.text);
    final chars = _editCtrl.text.length;
    return Padding(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('编辑模式', style: Theme.of(context).textTheme.titleMedium),
              Text('字符: $chars  字数: $wc',
                  style: TextStyle(color: Colors.grey[600], fontSize: 12)),
            ],
          ),
          const SizedBox(height: 8),
          Expanded(
            child: TextField(
              controller: _editCtrl,
              maxLines: null,
              expands: true,
              textAlignVertical: TextAlignVertical.top,
              style: const TextStyle(fontSize: 14, height: 1.7),
              decoration: InputDecoration(
                filled: true,
                fillColor: Colors.grey.withValues(alpha: 0.05),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
                hintText: '在这里编辑章节...',
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildReadOnly() {
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

  Widget _buildDiffView(ChapterDiff diff) {
    if (!diff.hasDiff) {
      return Container(
        margin: const EdgeInsets.all(12),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Colors.green.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(children: [
          const Icon(Icons.check_circle_outline, color: Colors.green),
          const SizedBox(width: 8),
          const Text('当前章节无 diff（已应用最新版本）'),
        ]),
      );
    }
    return Container(
      margin: const EdgeInsets.all(12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.orange.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.difference, color: Colors.orange, size: 18),
              const SizedBox(width: 6),
              Text('${diff.entries.length} 处变更',
                  style: const TextStyle(fontWeight: FontWeight.bold)),
            ],
          ),
          const SizedBox(height: 8),
          ...diff.entries.take(20).map((e) => Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: e.isInsert ? Colors.green.withValues(alpha: 0.2) : Colors.red.withValues(alpha: 0.2),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    '${e.isInsert ? "+" : "-"}${e.text.length > 80 ? "${e.text.substring(0, 80)}..." : e.text}',
                    style: const TextStyle(fontSize: 12, fontFamily: 'monospace'),
                  ),
                ),
              )),
          if (diff.entries.length > 20)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('... +${diff.entries.length - 20} 行 (已折叠)',
                  style: TextStyle(color: Colors.grey[600], fontSize: 12)),
            ),
        ],
      ),
    );
  }

  int _countWords(String s) {
    final clean = s.replaceAll(RegExp(r'\s'), '').replaceAll(RegExp(r'[^\u4e00-\u9fa5a-zA-Z0-9]'), '');
    return clean.length;
  }
}
