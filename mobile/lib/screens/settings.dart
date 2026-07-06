import 'package:flutter/material.dart';
import '../services/api.dart';
import '../services/logger.dart';
import 'debug.dart';
import 'llm_config.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('设置')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // Server info card
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('服务器', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      const Icon(Icons.cloud, size: 16, color: Colors.grey),
                      const SizedBox(width: 8),
                      Expanded(
                        child: SelectableText(novelApi.baseUrl,
                            style: const TextStyle(fontFamily: 'monospace', fontSize: 13)),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  const Text('地址已固定，如需修改请重新打包',
                      style: TextStyle(fontSize: 11, color: Colors.grey)),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),

          // Device info card
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('设备', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      const Icon(Icons.phone_android, size: 16, color: Colors.grey),
                      const SizedBox(width: 8),
                      Expanded(
                        child: SelectableText(appLogger.deviceId,
                            style: const TextStyle(fontFamily: 'monospace', fontSize: 13)),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  const Text('此 ID 用于服务器端日志追踪',
                      style: TextStyle(fontSize: 11, color: Colors.grey)),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),

          // LLM config button
          SizedBox(
            width: double.infinity,
            child: OutlinedButton.icon(
              onPressed: () {
                Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const LLMConfigScreen()),
                );
              },
              icon: const Icon(Icons.psychology),
              label: const Text('LLM 配置'),
            ),
          ),
          const SizedBox(height: 24),

          // Debug button
          SizedBox(
            width: double.infinity,
            child: OutlinedButton.icon(
              onPressed: () {
                Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const DebugScreen()),
                );
              },
              icon: const Icon(Icons.bug_report),
              label: const Text('调试面板'),
            ),
          ),
          const SizedBox(height: 8),
          SizedBox(
            width: double.infinity,
            child: OutlinedButton.icon(
              onPressed: () async {
                await appLogger.flushNow();
                if (!context.mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('日志已上传到服务器')),
                );
              },
              icon: const Icon(Icons.cloud_upload),
              label: const Text('上传日志'),
            ),
          ),
          const SizedBox(height: 24),
          const Divider(),
          const SizedBox(height: 8),
          Center(
            child: Text('小说工作流 v1.0.0',
                style: TextStyle(fontSize: 12, color: Colors.grey[500])),
          ),
        ],
      ),
    );
  }
}
