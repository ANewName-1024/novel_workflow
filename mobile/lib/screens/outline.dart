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

  // ── AI suggestions ────────────────────────────────────────────────

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

  // ── Manual edit / delete / reorder ─────────────────────────────────

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

  Future<void> _editNode(OutlineNode node) async {
    final result = await showModalBottomSheet<Map<String, dynamic>>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => _NodeEditSheet(node: node),
    );
    if (result == null) return;
    try {
      await novelApi.updateOutlineNode(widget.bookName, node.id, result);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('已保存')),
      );
      _load();
    } catch (e) {
      appLogger.error('update outline node', ctx: {'ch_id': node.id, 'err': e.toString()});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('保存失败: $e')),
      );
    }
  }

  Future<void> _deleteNode(OutlineNode node) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除节点'),
        content: Text('确定删除 "${node.title}" 吗？'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await novelApi.deleteOutlineNodeHard(widget.bookName, node.id);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('已删除')),
      );
      _load();
    } catch (e) {
      appLogger.error('delete outline node', ctx: {'ch_id': node.id, 'err': e.toString()});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('删除失败: $e')),
      );
    }
  }

  void _showNodeActions(OutlineNode node) {
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.edit),
              title: const Text('编辑'),
              onTap: () {
                Navigator.pop(ctx);
                _editNode(node);
              },
            ),
            ListTile(
              leading: const Icon(Icons.auto_fix_high),
              title: const Text('AI 扩写'),
              onTap: () {
                Navigator.pop(ctx);
                _aiExpand(title: node.title, summary: node.summary ?? '');
              },
            ),
            const Divider(),
            ListTile(
              leading: const Icon(Icons.delete, color: Colors.red),
              title: const Text('删除', style: TextStyle(color: Colors.red)),
              onTap: () {
                Navigator.pop(ctx);
                _deleteNode(node);
              },
            ),
          ],
        ),
      ),
    );
  }

  /// Reorder nodes across all volumes.
  /// Uses a single batch call to /api/outline/<book>/reorder.
  Future<void> _onReorder(int oldIndex, int newIndex) async {
    if (_outline == null) return;
    // Build a flat list of (vol, node) in current display order
    final flat = <_NodeRef>[];
    for (final vol in _outline!.volumes) {
      for (final n in vol.nodes) {
        flat.add(_NodeRef(vol: vol, node: n));
      }
    }
    if (oldIndex < 0 || oldIndex >= flat.length) return;
    final adjusted = newIndex > oldIndex ? newIndex - 1 : newIndex;
    final item = flat.removeAt(oldIndex);
    flat.insert(adjusted, item);

    // Build moves: each node's new_vol = its new volume, new_position = its index
    final moves = <Map<String, dynamic>>[];
    final newPositionByVol = <String, int>{};
    for (final ref in flat) {
      final pos = newPositionByVol[ref.vol.id] ?? 0;
      moves.add({
        'ch_id': ref.node.id,
        'new_vol': ref.vol.id,
        'new_position': pos,
      });
      newPositionByVol[ref.vol.id] = pos + 1;
    }

    // Optimistic UI update
    final newVols = _outline!.volumes.map((v) {
      final nodes = flat.where((r) => r.vol.id == v.id).map((r) => r.node).toList();
      return Volume(id: v.id, title: v.title, summary: v.summary, nodes: nodes);
    }).toList();
    setState(() {
      _outline = Outline(book: _outline!.book, volumes: newVols);
    });

    try {
      await novelApi.reorderOutlineNodes(widget.bookName, moves);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('已重排')),
      );
      _load();
    } catch (e) {
      appLogger.error('reorder outline', ctx: {'err': e.toString()});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('重排失败: $e')),
      );
      _load();  // reload to recover
    }
  }

  // ── Volume actions ─────────────────────────────────────────────────

  Future<void> _addVolume() async {
    final ctrl = TextEditingController(text: '');
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('新增卷'),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          decoration: const InputDecoration(
            labelText: '卷标题',
            hintText: '例如: 第二部 - 城市篇',
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('创建')),
        ],
      ),
    );
    if (ok != true) return;
    final title = ctrl.text.trim();
    if (title.isEmpty) return;
    try {
      await novelApi.addOutlineVolume(widget.bookName, title: title);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('已创建卷: $title')),
      );
      _load();
    } catch (e) {
      appLogger.error('add outline volume', ctx: {'err': e.toString()});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('创建失败: $e')),
      );
    }
  }

  Future<void> _renameVolume(Volume vol) async {
    final ctrl = TextEditingController(text: vol.title);
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('编辑卷'),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          decoration: const InputDecoration(labelText: '卷标题'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('保存')),
        ],
      ),
    );
    if (ok != true) return;
    final newTitle = ctrl.text.trim();
    if (newTitle.isEmpty || newTitle == vol.title) return;
    try {
      // We can either use add_volume + delete, or just call _update via
      // the node update endpoint with a special vol field. The cleanest
      // approach is to do a full outline PUT replacing the volume's title.
      await _patchVolumeTitle(vol.id, newTitle);
      _load();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('已修改卷名')),
        );
      }
    } catch (e) {
      appLogger.error('rename outline volume', ctx: {'err': e.toString()});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('修改失败: $e')),
      );
    }
  }

  Future<void> _patchVolumeTitle(String volId, String newTitle) async {
    if (_outline == null) return;
    // Server doesn't have a dedicated volume-update endpoint, so we do a
    // full PUT /api/outline/<book> with the modified title.
    final o = await novelApi.getOutline(widget.bookName);
    if (!o.volumes.any((v) => v.id == volId)) {
      throw Exception('volume $volId not found');
    }
    // We need the raw outline data; re-fetch by reading the file directly
    // via the api would be cleaner. Simpler: just call PUT with the loaded
    // outline mutated.
    final full = {
      'volumes': o.volumes.map((v) {
        return {
          'id': v.id,
          'title': v.id == volId ? newTitle : v.title,
          'summary': v.summary,
          'chapters': v.nodes.map((n) => n.id).toList(),
        };
      }).toList(),
      'chapters': o.volumes.expand((v) => v.nodes).map((n) => {
        'id': n.id,
        'title': n.title,
        'summary': n.summary,
        'pov': n.pov,
        'vol': n.vol,
        'key_events': n.keyEvents,
        'foreshadow': n.foreshadow,
      }).toList(),
    };
    await novelApi.replaceOutline(widget.bookName, full);
  }

  Future<void> _deleteVolume(Volume vol) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除卷'),
        content: Text('删除 "${vol.title}" 吗？\n卷内 ${vol.nodes.length} 章会转移到其他卷。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      final n = await novelApi.deleteOutlineVolume(widget.bookName, vol.id);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('已删除，$n 章已重分配')),
      );
      _load();
    } catch (e) {
      appLogger.error('delete outline volume', ctx: {'err': e.toString()});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('删除失败: $e')),
      );
    }
  }

  void _showVolumeActions(Volume vol) {
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.edit),
              title: const Text('编辑卷名'),
              onTap: () {
                Navigator.pop(ctx);
                _renameVolume(vol);
              },
            ),
            const Divider(),
            ListTile(
              leading: const Icon(Icons.delete, color: Colors.red),
              title: const Text('删除卷', style: TextStyle(color: Colors.red)),
              subtitle: const Text('卷内章节会重分配'),
              onTap: () {
                Navigator.pop(ctx);
                _deleteVolume(vol);
              },
            ),
          ],
        ),
      ),
    );
  }

  // ── Build ──────────────────────────────────────────────────────────

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
          IconButton(
            icon: const Icon(Icons.add_box_outlined),
            tooltip: '新增卷',
            onPressed: _addVolume,
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
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: _addVolume,
              icon: const Icon(Icons.add_box_outlined),
              label: const Text('手动新增卷'),
            ),
          ],
        ),
      );
    }
    // Build flat list of all nodes for ReorderableListView.
    final allItems = <_NodeRef>[];
    for (final vol in _outline!.volumes) {
      for (final n in vol.nodes) {
        allItems.add(_NodeRef(vol: vol, node: n));
      }
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: Row(
              children: [
                const Icon(Icons.info_outline, size: 14, color: Colors.grey),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    '长按节点弹出菜单 · 拖动 ⠿ 改顺序',
                    style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                  ),
                ),
              ],
            ),
          ),
          Expanded(
            child: ReorderableListView.builder(
              padding: const EdgeInsets.all(8),
              itemCount: allItems.length,
              onReorder: _onReorder,
              itemBuilder: (ctx, i) {
                final ref = allItems[i];
                final isFirstOfVol = i == 0 ||
                    allItems[i - 1].vol.id != ref.vol.id;
                return Column(
                  key: ValueKey(ref.node.id),
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (isFirstOfVol)
                      _volumeHeader(ref.vol),
                    _buildNodeTile(ref.node, i),
                  ],
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _volumeHeader(Volume vol) {
    return GestureDetector(
      onLongPress: () => _showVolumeActions(vol),
      child: Container(
        margin: const EdgeInsets.only(top: 8, bottom: 4),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: Colors.indigo.shade50,
          borderRadius: BorderRadius.circular(4),
        ),
        child: Row(
          children: [
            Icon(Icons.book, size: 16, color: Colors.indigo.shade700),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                vol.title,
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  color: Colors.indigo.shade700,
                ),
              ),
            ),
            Text('${vol.nodes.length} 章', style: TextStyle(fontSize: 11, color: Colors.grey.shade600)),
            IconButton(
              icon: const Icon(Icons.more_vert, size: 16),
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
              onPressed: () => _showVolumeActions(vol),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildNodeTile(OutlineNode node, int index) {
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 3),
      child: ListTile(
        dense: true,
        leading: ReorderableDragStartListener(
          index: index,
          child: const Icon(Icons.drag_handle, size: 18, color: Colors.grey),
        ),
        title: Text(node.title, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500)),
        subtitle: node.summary != null
            ? Text(node.summary!, maxLines: 2, overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12))
            : null,
        trailing: IconButton(
          icon: const Icon(Icons.more_vert, size: 18),
          onPressed: () => _showNodeActions(node),
        ),
        onLongPress: () => _showNodeActions(node),
        onTap: () => _editNode(node),
      ),
    );
  }
}

class _NodeRef {
  final Volume vol;
  final OutlineNode node;
  _NodeRef({required this.vol, required this.node});
}

/// Bottom sheet for editing a single node's editable fields.
class _NodeEditSheet extends StatefulWidget {
  final OutlineNode node;
  const _NodeEditSheet({required this.node});

  @override
  State<_NodeEditSheet> createState() => _NodeEditSheetState();
}

class _NodeEditSheetState extends State<_NodeEditSheet> {
  late final TextEditingController _title;
  late final TextEditingController _summary;
  late final TextEditingController _pov;
  late final TextEditingController _keyEvents;
  late final TextEditingController _foreshadow;

  @override
  void initState() {
    super.initState();
    _title = TextEditingController(text: widget.node.title);
    _summary = TextEditingController(text: widget.node.summary ?? '');
    _pov = TextEditingController(text: widget.node.pov ?? '');
    _keyEvents = TextEditingController(text: widget.node.keyEvents.join('\n'));
    _foreshadow = TextEditingController(text: widget.node.foreshadow.join('\n'));
  }

  @override
  void dispose() {
    _title.dispose();
    _summary.dispose();
    _pov.dispose();
    _keyEvents.dispose();
    _foreshadow.dispose();
    super.dispose();
  }

  void _save() {
    final events = _keyEvents.text
        .split('\n')
        .map((s) => s.trim())
        .where((s) => s.isNotEmpty)
        .toList();
    final fores = _foreshadow.text
        .split('\n')
        .map((s) => s.trim())
        .where((s) => s.isNotEmpty)
        .toList();
    Navigator.pop(context, {
      'title': _title.text.trim(),
      'summary': _summary.text.trim(),
      'pov': _pov.text.trim(),
      'key_events': events,
      'foreshadow': fores,
    });
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(
        left: 16, right: 16, top: 16,
        bottom: MediaQuery.of(context).viewInsets.bottom + 16,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                const Icon(Icons.edit, color: Colors.indigo),
                const SizedBox(width: 8),
                Text('编辑节点: ${widget.node.id}',
                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
              ],
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _title,
              decoration: const InputDecoration(
                labelText: '标题',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _summary,
              maxLines: 3,
              decoration: const InputDecoration(
                labelText: '摘要',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _pov,
              decoration: const InputDecoration(
                labelText: 'POV',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _keyEvents,
              maxLines: 4,
              decoration: const InputDecoration(
                labelText: '关键事件（每行一条）',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _foreshadow,
              maxLines: 3,
              decoration: const InputDecoration(
                labelText: '伏笔（每行一条）',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('取消'),
                ),
                const Spacer(),
                FilledButton.icon(
                  onPressed: _save,
                  icon: const Icon(Icons.save, size: 16),
                  label: const Text('保存'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}