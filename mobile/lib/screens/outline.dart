import 'package:flutter/material.dart';
import '../models/outline.dart';
import '../services/api.dart';

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
      if (!mounted) return;
      setState(() { _error = e.toString(); _loading = false; });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('大纲')),
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
    if (_outline == null || _outline!.volumes.isEmpty) {
      return const Center(child: Text('暂无大纲'));
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
    );
  }
}
