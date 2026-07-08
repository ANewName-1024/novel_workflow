import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
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
          _serverCard(),
          const SizedBox(height: 12),

          // App version + device card
          _appCard(),
          const SizedBox(height: 12),

          // Remote debug card (helps me connect to your phone for hot-debug)
          _remoteDebugCard(context),
          const SizedBox(height: 12),

          // LLM config button
          SizedBox(
            width: double.maxFinite,
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
            width: double.maxFinite,
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
            width: double.maxFinite,
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
            child: Text('小说工作流 v${appLogger.appVersion}',
                style: TextStyle(fontSize: 12, color: Colors.grey[500])),
          ),
        ],
      ),
    );
  }

  Widget _serverCard() {
    return Card(
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
                IconButton(
                  icon: const Icon(Icons.copy, size: 16),
                  onPressed: () {
                    Clipboard.setData(ClipboardData(text: novelApi.baseUrl));
                  },
                  tooltip: '复制',
                ),
              ],
            ),
            const SizedBox(height: 4),
            const Text('地址已固定，如需修改请重新打包',
                style: TextStyle(fontSize: 11, color: Colors.grey)),
          ],
        ),
      ),
    );
  }

  Widget _appCard() {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('应用信息', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
            const SizedBox(height: 12),
            _infoRow(Icons.tag, '版本', '${appLogger.appVersion}',
                '(${appLogger.packageName})'),
            const SizedBox(height: 8),
            _infoRow(Icons.smartphone, '设备 ID', appLogger.deviceId, null),
            const SizedBox(height: 8),
            _infoRow(Icons.phone_android, '设备型号', appLogger.deviceModelPublic, null),
            const SizedBox(height: 8),
            _infoRow(
              Icons.info_outline, '日志', '崩溃/错误自动上传到服务器', null,
            ),
          ],
        ),
      ),
    );
  }

  Widget _infoRow(IconData icon, String label, String value, String? extra) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 14, color: Colors.grey),
        const SizedBox(width: 8),
        SizedBox(
          width: 64,
          child: Text(label, style: const TextStyle(fontSize: 12, color: Colors.grey)),
        ),
        Expanded(
          child: SelectableText(
            value,
            style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
          ),
        ),
        if (extra != null)
          Text(extra, style: const TextStyle(fontSize: 10, color: Colors.grey)),
      ],
    );
  }

  /// Remote debug card: tells the user how to connect ADB over network
  /// so I (the AI) can attach `flutter run` / `adb logcat` to this phone.
  Widget _remoteDebugCard(BuildContext context) {
    // We don't have a direct way to know the phone's IP from inside the
    // app without network calls, so we just give generic instructions
    // for connecting via USB + WiFi ADB.
    return Card(
      color: Colors.blue.shade50,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.usb, color: Colors.blue.shade800, size: 20),
                const SizedBox(width: 8),
                const Text('远程调试', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
              ],
            ),
            const SizedBox(height: 8),
            const Text(
              '需要让小爪直接调你的 APP 时, 请执行以下步骤:',
              style: TextStyle(fontSize: 12),
            ),
            const SizedBox(height: 8),
            _stepRow('1', '手机开"开发者选项" + "USB 调试", 用数据线连电脑'),
            _stepRow('2', '手机连上和电脑同一个 WiFi, 记下手机 IP (设置 → WiFi → 当前网络详情)'),
            _stepRow('3', '把"手机 IP"发给小爪, 小爪就能 adb connect 直接调试'),
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(4),
                border: Border.all(color: Colors.blue.shade200),
              ),
              child: const SelectableText(
                'adb tcpip 5555\n'
                'adb connect <手机IP>:5555\n'
                'flutter run --device-id=<手机IP>:5555',
                style: TextStyle(fontFamily: 'monospace', fontSize: 11),
              ),
            ),
            const SizedBox(height: 8),
            const Text(
              '提示: 崩溃/错误已经自动上传, 不调试也能看到日志',
              style: TextStyle(fontSize: 11, color: Colors.grey),
            ),
          ],
        ),
      ),
    );
  }

  Widget _stepRow(String num, String text) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 18, height: 18,
            decoration: BoxDecoration(
              color: Colors.blue.shade700,
              borderRadius: BorderRadius.circular(9),
            ),
            child: Center(
              child: Text(num, style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold)),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 12))),
        ],
      ),
    );
  }
}

/// Try to read Android IP via getprop (only works on Android).
class _AndroidInfo extends StatefulWidget {
  const _AndroidInfo();

  @override
  State<_AndroidInfo> createState() => _AndroidInfoState();
}

class _AndroidInfoState extends State<_AndroidInfo> {
  String? _wifiIp;

  @override
  void initState() {
    super.initState();
    _loadIp();
  }

  Future<void> _loadIp() async {
    try {
      // We can't easily get WiFi IP without platform channels.
      // Skip for now; user can read it from system settings.
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return const SizedBox.shrink();
  }
}