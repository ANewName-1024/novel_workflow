import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../services/api.dart';
import '../services/logger.dart';

class LLMConfigScreen extends StatefulWidget {
  const LLMConfigScreen({super.key});

  @override
  State<LLMConfigScreen> createState() => _LLMConfigScreenState();
}

class _LLMConfigScreenState extends State<LLMConfigScreen> {
  static const _keyProvider = 'llm_provider';
  static const _keyModel = 'llm_model';
  static const _keyApiKey = 'llm_api_key';
  Map<String, dynamic> _providers = {};
  String? _currentProvider;
  String? _defaultProvider;

  final TextEditingController _modelCtrl = TextEditingController();
  final TextEditingController _keyCtrl = TextEditingController();
  String? _testingProvider;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _modelCtrl.dispose();
    _keyCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await novelApi.listLLMProviders();
      final prefs = await SharedPreferences.getInstance();
      setState(() {
        _providers = data['providers'] is Map ? Map<String, dynamic>.from(data['providers']) : <String, dynamic>{};
        _defaultProvider = data['default_provider'] as String?;
        _currentProvider = prefs.getString(_keyProvider) ?? _defaultProvider;
        // model 优先取当前 provider 的默认 model
        String? initialModel = prefs.getString(_keyModel);
        if (initialModel == null && _currentProvider != null) {
          final prov = _providers[_currentProvider];
          if (prov is Map && prov['model'] != null) {
            initialModel = prov['model'].toString();
          }
        }
        _modelCtrl.text = initialModel ?? '';
        _loading = false;
      });
    } catch (e) {
      appLogger.error('LLM config load failed', ctx: {'error': e.toString()});
      setState(() => _loading = false);
    }
  }

  Future<void> _save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyProvider, _currentProvider ?? '');
    await prefs.setString(_keyModel, _modelCtrl.text.trim());
    if (_keyCtrl.text.isNotEmpty) {
      await prefs.setString(_keyApiKey, _keyCtrl.text.trim());
    }
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('已保存')),
    );
  }

  Future<void> _test(String provider) async {
    setState(() => _testingProvider = provider);
    try {
      final result = await novelApi.llmHealthCheck(
        provider: provider,
        model: _modelCtrl.text.trim().isEmpty ? null : _modelCtrl.text.trim(),
      );
      if (!mounted) return;
      final ok = result['ok'] == true;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(ok ? '$provider 连通 ✅' : '$provider 失败: ${result['error']}'),
          backgroundColor: ok ? Colors.green : Colors.red,
        ),
      );
    } catch (e) {
      appLogger.error('LLM health check error', ctx: {'provider': provider, 'error': e.toString()});
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('$provider 错误: $e')),
      );
    } finally {
      if (mounted) setState(() => _testingProvider = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return Scaffold(
        appBar: AppBar(title: const Text('LLM 配置')),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    final entries = _providers.entries.toList();

    return Scaffold(
      appBar: AppBar(
        title: const Text('LLM 配置'),
        actions: [
          TextButton(
            onPressed: _save,
            child: const Text('保存', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('当前默认模型', style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 8),
                  Text(
                    _defaultProvider ?? '未配置',
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.primary,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),

          Text('Provider 列表', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),

          ...entries.map((e) {
            final name = e.key;
            final cfg = e.value as Map<String, dynamic>;
            final selected = _currentProvider == name;
            final testing = _testingProvider == name;
            return Card(
              color: selected ? Theme.of(context).colorScheme.primary.withValues(alpha: 0.1) : null,
              child: ListTile(
                title: Text(name, style: const TextStyle(fontWeight: FontWeight.bold)),
                subtitle: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('模型: ${cfg['model'] ?? '未设置'}'),
                    Text('API Base: ${cfg['api_base'] ?? '未设置'}'),
                    Row(
                      children: [
                        Icon(
                          cfg['api_key_configured'] == true ? Icons.check_circle : Icons.error,
                          size: 14,
                          color: cfg['api_key_configured'] == true ? Colors.green : Colors.grey,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          cfg['api_key_configured'] == true ? 'Key 已配置' : 'Key 缺失',
                          style: TextStyle(
                            color: cfg['api_key_configured'] == true ? Colors.green : Colors.grey,
                            fontSize: 12,
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
                trailing: testing
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : IconButton(
                        icon: const Icon(Icons.wifi_tethering),
                        onPressed: () => _test(name),
                        tooltip: '测试连接',
                      ),
                onTap: () {
                  setState(() {
                    _currentProvider = name;
                    _modelCtrl.text = cfg['model'] as String? ?? '';
                  });
                },
              ),
            );
          }),

          const SizedBox(height: 24),
          Text('自定义模型', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          TextField(
            controller: _modelCtrl,
            decoration: const InputDecoration(
              labelText: 'Model 名称',
              hintText: '例如: deepseek-chat',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _keyCtrl,
            obscureText: true,
            decoration: const InputDecoration(
              labelText: 'API Key (可选, 留空使用现有)',
              hintText: 'sk-...',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: () {
              _save();
              if (_currentProvider != null) _test(_currentProvider!);
            },
            icon: const Icon(Icons.save),
            label: const Text('保存并测试'),
          ),
        ],
      ),
    );
  }
}
