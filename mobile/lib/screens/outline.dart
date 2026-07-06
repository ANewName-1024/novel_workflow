import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/outline.dart';
import '../services/api.dart';
import '../services/logger.dart';

class OutlineScreen extends StatefulWidget {
  final String bookName;
  const OutlineScreen({super.key, required this.bookName});

  @override
  State<OutlineScreen> createState() => _OutlineScreenState();
}

class _OutlineScreenState extends State<OutlineScreen> {
  Outline? _outline;
  bool _loading = true;
  String? _error;
  bool _aiBusy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final o = await novelApi.getOutline(widget.bookName);
      if (!mounted) return;
      setState(() { _outline = o; _loading = false; });
    } catch (e) {
      appLogger.error('outline load', ctx: {'err': e.toString()});
      if (!mounted) return;
      setState(() { _error = e.toString(); _loading = false; });
    }
  }

  Future<String?> _savedLlmProvider() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString('llm_provider');
  }

  Future<String?> _savedLlmModel() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString('llm_model');
  }

  Future<void> _aiSuggest({int count = 3}) async {
    setState(() => _aiBusy = true);
    try {
      final provider = await _savedLlmProvider();
      final model = await _savedLlmModel();
      final list = await novelApi.aiSuggestOutline(widget.bookName,
          count: count, provider: provider, model: model);
      if (!mounted) return;
      if (list.isEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('LLM 没生成建议')),
        );
        return;
      }
      _showAiSuggestionsDialog(list);
    } catch (e) {
      appLogger.error('ai-suggest', ctx: {'err': e.toString()});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('错误: $e')),
      );
    } finally {
      if (mounted) setState(() => _aiBusy = false);
    }
  }

  void _showAiSuggestionsDialog(List<dynamic> suggestions) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('AI 大纲建议'),
        content: SizedBox(
          width: double.maxFinite,
          height: 400,
          child: ListView.builder(
            itemCount: suggestions.length,
            itemBuilder: (_, i) {
              final s = suggestions[i] as Map<String, dynamic>;
              return Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('章节 ${s['num'] ?? '?'}: ${s['title'] ?? '(无标题)'}',
                          style: const TextStyle(fontWeight: FontWeight.bold)),
                      const SizedBox(height: 6),
                      Text(s['summary']?.toString() ?? '', style: const TextStyle(fontSize: 13)),
                      const SizedBox(height: 8),
                      Row(
                        children: [
                          OutlinedButton.icon(
                            icon: const Icon(Icons.fullscreen, size: 14),
                            label: const Text('扩写'),
                            onPressed: () async {
                              Navigator.pop(ctx);
                              await _aiExpand(title: s['title'] ?? '', summary: s['summary'] ?? '');
                            },
                          ),
                          const SizedBox(width: 8),
                          FilledButton.icon(
                            icon: const Icon(Icons.add, size: 14),
                            label: const Text('加入'),
                            onPressed: () async {
                              Navigator.pop(ctx);
                              await _addNode(s);
                            },
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              );
            },
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('关闭')),
        ],
      ),
    );
  }

  Future<void> _aiExpand({required String title, required String summary}) async {
    if (title.isEmpty || summary.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('需要 title 和 summary 才能扩写')),
      );
      return;
    }
    setState(() => _aiBusy = true);
    try {
      final provider = await _savedLlmProvider();
      final model = await _savedLlmModel();
      final result = await novelApi.aiExpandOutline(widget.bookName,
          title: title, summary: summary, provider: provider, model: model);
      if (!mounted) return;
      _showExpansionDialog(result, title: title, summary: summary);
    } catch (e) {
      appLogger.error('ai-expand', ctx: {'err': e.toString()});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('错误: $e')));
    } finally {
      if (mounted) setState(() => _aiBusy = false);
    }
  }

  void _showExpansionDialog(Map<String, dynamic> result, {required String title, required String summary}) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('AI 扩写结果'),
        content: SizedBox(
          width: double.maxFinite,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                _kvRow('关键事件', (result['key_events'] as List<dynamic>?)?.join('\n• ', ) ?? '(无)'),
                const SizedBox(height: 8),
                _kvRow('伏笔', result['foreshadow']?.toString() ?? '(无)'),
                const SizedBox(height: 8),
                _kvRow('POV 备注', result['pov_notes']?.toString() ?? '(无)'),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('关闭'),
          ),
          FilledButton.icon(
            icon: const Icon(Icons.save, size: 16),
            label: const Text('保存到节点'),
            onPressed: () async {
              Navigator.pop(ctx);
              await _addNode({
                'title': title,
                'summary': summary,
                'key_events': result['key_events'],
                'foreshadow': result['foreshadow'],
                'pov_notes': result['pov_notes'],
              });
            },
          ),
        ],
      ),
    );
  }

  Widget _kvRow(String k, String v) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(k, style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.indigo)),
        const SizedBox(height: 4),
        Text(v.isEmpty ? '(无)' : v, style: const TextStyle(fontSize: 13)),
      ],
    );
  }

  Future<void> _addNode(Map<String, dynamic> ai) async {
    try {
      await novelApi.addOutlineNode(widget.bookName, ai);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已加入节点')));
      _load();
    } catch (e) {
      appLogger.error('add outline node', ctx: {'err': e.toString()});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('加入失败: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('${widget.bookName} · 大纲'),
        actions: [
          IconButton(
            icon: const Icon(Icons.psychology),
            tooltip: 'AI 建议下一批章节',
            onPressed: _aiBusy ? null : _aiSuggest,
          ),
          IconButton(icon: const Icon(Icons.refresh), onPressed: _load),
        ],
      ),
      body: _buildBody(),
      floatingActionButton: _aiBusy
          ? const FloatingActionButton(
              onPressed: null,
              child: SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white)),
            )
          : null,
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
    if (_outline == null || _outline!.volumes.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('暂无大纲 — 用上面的 🧠 AI 建议下一批章节 试试'),
            const SizedBox(height: 12),
            FilledButton.icon(
              onPressed: _aiBusy ? null : _aiSuggest,
              icon: const Icon(Icons.psychology),
              label: const Text('AI 生成下一批'),
            ),
          ],
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(12),
        children: _outline!.volumes.map((vol) => _buildVolumeCard(vol)).toList(),
      ),
    );
  }

  Widget _buildVolumeCard(Volume vol) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ExpansionTile(
        title: Text(vol.title, style: const TextStyle(fontWeight: FontWeight.bold)),
        subtitle: vol.summary != null
            ? Text(vol.summary!, maxLines: 2, overflow: TextOverflow.ellipsis)
            : null,
        children: vol.nodes.isNotEmpty
            ? vol.nodes.map((n) => _buildNodeTile(n)).toList()
            : [const Padding(
                padding: EdgeInsets.all(16),
                child: Text('空卷', style: TextStyle(color: Colors.grey)),
              )],
      ),
    );
  }

  Widget _buildNodeTile(OutlineNode node) {
    return ListTile(
      dense: true,
      leading: const Icon(Icons.article_outlined, size: 18),
      title: Text(node.title, style: const TextStyle(fontSize: 14)),
      subtitle: node.summary != null
          ? Text(node.summary!, maxLines: 2, overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12))
          : null,
      trailing: IconButton(
        icon: const Icon(Icons.auto_fix_high, size: 18),
        tooltip: 'AI 扩写本节',
        onPressed: _aiBusy ? null : () => _aiExpand(title: node.title, summary: node.summary ?? ''),
      ),
    );
  }
}
