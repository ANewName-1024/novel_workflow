import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import '../services/logger.dart';
import '../services/api.dart';

class DebugScreen extends StatefulWidget {
  const DebugScreen({super.key});

  @override
  State<DebugScreen> createState() => _DebugScreenState();
}

class _DebugScreenState extends State<DebugScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tabController;
  int _currentTab = 0;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 3, vsync: this);
    _tabController.addListener(() {
      if (mounted) setState(() => _currentTab = _tabController.index);
    });
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_currentTab == 0
            ? '日志'
            : _currentTab == 1
                ? '设备信息'
                : '远程调试'),
        bottom: TabBar(
          controller: _tabController,
          tabs: const [
            Tab(icon: Icon(Icons.list_alt), text: '日志'),
            Tab(icon: Icon(Icons.devices), text: '设备'),
            Tab(icon: Icon(Icons.wifi_tethering), text: '调试'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabController,
        children: const [
          _LogViewTab(),
          _DeviceInfoTab(),
          _RemoteDebugTab(),
        ],
      ),
    );
  }
}

// ── 日志查看 ──

class _LogViewTab extends StatefulWidget {
  const _LogViewTab();
  @override
  State<_LogViewTab> createState() => _LogViewTabState();
}

class _LogViewTabState extends State<_LogViewTab> {
  List<LogEntry> _logs = [];
  bool _expanded = false;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  void _refresh() {
    setState(() => _logs = appLogger.recentLogs);
  }

  Color _levelColor(LogLevel l) {
    switch (l) {
      case LogLevel.debug:
        return Colors.grey;
      case LogLevel.info:
        return Colors.blue;
      case LogLevel.warn:
        return Colors.orange;
      case LogLevel.error:
        return Colors.red;
      case LogLevel.fatal:
        return Colors.purple;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          child: Row(
            children: [
              Expanded(
                child: Text('${_logs.length} 条日志',
                    style: const TextStyle(color: Colors.grey, fontSize: 13)),
              ),
              TextButton.icon(
                onPressed: _refresh,
                icon: const Icon(Icons.refresh, size: 18),
                label: const Text('刷新'),
              ),
              const SizedBox(width: 8),
              TextButton.icon(
                onPressed: () => appLogger.flushNow().then((_) {
                  if (mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('已上传队列日志')),
                    );
                  }
                }),
                icon: const Icon(Icons.cloud_upload, size: 18),
                label: const Text('上传'),
              ),
              const SizedBox(width: 8),
              TextButton.icon(
                onPressed: () {
                  appLogger.debug('手动触发测试日志');
                  appLogger.info('设备信息测试日志',
                      ctx: {'deviceId': appLogger.deviceId});
                  appLogger.warn('警告测试日志');
                  appLogger.error('错误测试日志');
                  _refresh();
                },
                icon: const Icon(Icons.add, size: 18),
                label: const Text('测试'),
              ),
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(
          child: _logs.isEmpty
              ? const Center(
                  child: Text('暂无日志', style: TextStyle(color: Colors.grey)))
              : RefreshIndicator(
                  onRefresh: () async => _refresh(),
                  child: ListView.builder(
                    itemCount: _logs.length,
                    itemBuilder: (context, index) {
                      final log = _logs[index];
                      return ListTile(
                        dense: true,
                        visualDensity: VisualDensity.compact,
                        leading: Container(
                          width: 8,
                          height: 8,
                          decoration: BoxDecoration(
                            color: _levelColor(log.level),
                            shape: BoxShape.circle,
                          ),
                        ),
                        title: Text(
                          log.msg,
                          style: const TextStyle(
                              fontSize: 12, fontFamily: 'monospace'),
                          maxLines: _expanded ? 20 : 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                        subtitle: Text(
                          '${log.level.name} - ${log.id}',
                          style: const TextStyle(
                              fontSize: 10, color: Colors.grey),
                        ),
                        trailing: log.stack.isNotEmpty
                            ? const Icon(Icons.error_outline,
                                size: 16, color: Colors.red)
                            : null,
                        onTap: () {
                          setState(() => _expanded = !_expanded);
                        },
                      );
                    },
                  ),
                ),
        ),
      ],
    );
  }
}

// ── 设备信息 ──

class _DeviceInfoTab extends StatelessWidget {
  const _DeviceInfoTab();

  @override
  Widget build(BuildContext context) {
    final items = <String, String>{
      '设备 ID': appLogger.deviceId,
      '服务器地址': novelApi.baseUrl,
      '日志模块': 'logger.dart v1',
      'API 端点': '12 endpoints',
    };

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: items.entries.map((e) {
                return Padding(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(
                        width: 100,
                        child: Text(e.key,
                            style: const TextStyle(
                                fontSize: 13, color: Colors.grey)),
                      ),
                      Expanded(
                        child: SelectableText(e.value,
                            style: const TextStyle(
                                fontSize: 13, fontFamily: 'monospace')),
                      ),
                    ],
                  ),
                );
              }).toList(),
            ),
          ),
        ),
        const SizedBox(height: 16),
        OutlinedButton.icon(
          onPressed: () {
            final list = appLogger.recentLogs;
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text('缓存 ${list.length} 条日志')),
            );
          },
          icon: const Icon(Icons.refresh, size: 18),
          label: const Text('刷新统计'),
        ),
      ],
    );
  }
}

// ── 远程调试 ──

class _RemoteDebugTab extends StatefulWidget {
  const _RemoteDebugTab();
  @override
  State<_RemoteDebugTab> createState() => _RemoteDebugTabState();
}

class _RemoteDebugTabState extends State<_RemoteDebugTab> {
  String _status = '检查中...';
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _checkConnection();
  }

  Future<void> _checkConnection() async {
    setState(() {
      _loading = true;
      _status = '连接中...';
    });
    try {
      final client = http.Client();
      final r = await client
          .get(Uri.parse('${novelApi.baseUrl}/api/app-log/health'))
          .timeout(const Duration(seconds: 10));
      client.close();
      if (r.statusCode == 200) {
        setState(() {
          _status = '已连接';
          _loading = false;
        });
      } else {
        setState(() {
          _status = '服务器返回 ${r.statusCode}';
          _loading = false;
        });
      }
    } catch (e) {
      setState(() {
        _status = '连接失败: $e';
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('日志上传服务',
                    style:
                        TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
                const SizedBox(height: 8),
                Row(
                  children: [
                    _loading
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.circle, size: 12),
                    const SizedBox(width: 8),
                    Text(_status, style: const TextStyle(fontSize: 13)),
                  ],
                ),
                const SizedBox(height: 8),
                Text('服务器: ${novelApi.baseUrl}/api/app-log',
                    style:
                        const TextStyle(fontSize: 11, color: Colors.grey)),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('远程调试指令',
                    style:
                        TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
                const SizedBox(height: 8),
                const Text(
                  '在服务器端查看日志：\n'
                  '  curl http://8.137.116.121:9080/api/app-log/devices\n'
                  '  curl http://8.137.116.121:9080/api/app-log/list?limit=20\n'
                  '  curl http://8.137.116.121:9080/api/app-log/stats',
                  style: TextStyle(
                      fontSize: 12, fontFamily: 'monospace', height: 1.6),
                ),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: _checkConnection,
                  icon: const Icon(Icons.wifi_find, size: 18),
                  label: const Text('测试连接'),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        Card(
          color: Colors.orange.withValues(alpha: 0.08),
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('手动触发上传',
                    style:
                        TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
                const SizedBox(height: 8),
                const Text(
                  '点击下方按钮，立刻上传队列中的所有日志。'
                  '\n日志将在 30 秒内自动上传，也可手动触发。',
                  style: TextStyle(fontSize: 12),
                ),
                const SizedBox(height: 12),
                FilledButton.icon(
                  onPressed: () async {
                    await appLogger.flushNow();
                    if (mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('日志已上传')),
                      );
                    }
                  },
                  icon: const Icon(Icons.cloud_upload, size: 18),
                  label: const Text('立即上传'),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
