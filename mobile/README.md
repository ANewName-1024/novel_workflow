# 小说工作流 Mobile (novel_app)

**Flutter 移动 App, 配套 novel_workflow web 工作流使用**

📦 **状态**: v1.3 M8 ✅ (2026-07-31)

---

## 这是什么

移动端 App, 让作者能在手机/平板上管理小说项目:

- 📚 **项目列表**: 看所有书的总览 (章节数 / 待审 / 通过 / 拒绝 / 流水线状态)
- 📖 **章节详情**: 阅读 / 在线编辑 / 看 diff
- 🌲 **大纲**: 浏览全书大纲 (卷 → 章), 支持手动编辑
- 🤖 **AI 大纲助手**: 一键调用 LLM 展开章节内容
- 🧠 **LLM 配置**: 选 provider (deepseek / minimax / local / gpt) + model, **保存即同步到所有 books 后端**
- 📊 **统计**: 各书进度一览
- 🐛 **调试面板**: 看 crash 日志 + 远程调试 (需要时启用)
- ⚙️ **设置**: 服务器地址 / LLM 配置 / 调试 / 关于

---

## 截图

> 真截图需 APK 重编译 + Android SDK (本机未装, 跳过)。下面是 ASCII 框图替代:

### 主屏 — 项目列表

```
┌──────────────────────────────────┐
│  📚 小说工作流         v1.0.0+1  │
├──────────────────────────────────┤
│  🟢 砚柒安传        ▶ 进入      │
│     10 章 · 待审 2 · 通过 8      │
├──────────────────────────────────┤
│  🟡 剑破苍穹        ▶ 进入      │
│     24 章 · 待审 5 · 通过 18     │
├──────────────────────────────────┤
│  ⚪ 短篇合集         ▶ 进入      │
│     3 章 · 待审 0 · 通过 3       │
└──────────────────────────────────┘
```

### LLM 配置 (v1.3 M8 重点)

```
┌──────────────────────────────────┐
│  ← LLM 配置                      │
├──────────────────────────────────┤
│  ▣ 选 Provider                   │
│  ┌────────────────────────────┐  │
│  │ 🟢 deepseek  (默认)  [测试]│  │
│  │ 🟡 minimax-cn   [测试]    │  │
│  │ ⚪ local-llama   [测试]    │  │
│  │ ⚪ openai        [测试]    │  │
│  └────────────────────────────┘  │
│  Model 名称: [deepseek-chat    ]  │
│  API Key:    [sk-...           ]  │
│                                  │
│  [ 💾 保存并同步到所有 books ]    │
│  (同步中… ✅ "已保存 + 同步到 2 本书")
└──────────────────────────────────┘
```

---

## 安装 / 构建

### 依赖

| 软件 | 版本 | 备注 |
|------|------|------|
| Flutter | 3.41.6 stable | `D:\flutter\bin\flutter` |
| Dart | 3.11.4 (Flutter bundled) | |
| Android SDK | API 34+ (compileSdk) | WinGet platform-tools 不够, 需完整 SDK |
| Java | 17 (Gradle 8.x) | |

### 拉代码

```bash
cd D:/.openclaw/workspace/novel_workflow/mobile
flutter pub get
```

### 跑 (debug, 真机/模拟器)

```bash
flutter devices                    # 列设备
flutter run -d <device-id>         # 跑
```

### 构建 APK

```bash
flutter build apk --debug          # ~150MB, 装手机调试用
flutter build apk --release        # ~30MB, 上架用
# 输出: build/app/outputs/flutter-apk/app-release.apk
```

### Flutter analyze

```bash
flutter analyze                    # 必须 0 issues
```

### 跑测试 (M5 完成时未建, 见 TODO)

```bash
flutter test                       # 目前无测试, 待补
```

---

## 目录结构

```
mobile/
├── lib/
│   ├── main.dart                 # 入口 + global error handler
│   ├── models/
│   │   └── book.dart             # Book 模型
│   ├── services/
│   │   ├── api.dart              # 后端 HTTP client (50+ endpoints)
│   │   └── logger.dart           # crash log 上报 + 远程调试
│   ├── screens/
│   │   ├── book_detail.dart      # 项目详情 + 章节列表
│   │   ├── chapter_detail.dart   # 章节阅读 + 编辑 + diff
│   │   ├── outline.dart          # 大纲树 (卷 → 章)
│   │   ├── llm_config.dart       # LLM 配置 (M8 同步)
│   │   ├── stats.dart            # 统计
│   │   ├── settings.dart         # 设置入口
│   │   └── debug.dart            # 调试面板
│   └── widgets/                  # 共享 widget
├── android/                      # Android 项目
├── test/                         # 单元测试 (空, 待补)
├── pubspec.yaml
└── README.md                     # 本文件
```

---

## 主要功能 vs 后端 API

| 屏幕 | 调用的 API |
|------|-----------|
| 项目列表 | `GET /api/projects` |
| 项目详情 | `GET /api/projects/<book>` |
| 章节详情 | `GET /api/chapter/<book>/<ch>` + `POST /api/edit/<book>/<ch>` |
| 大纲 | `GET /api/outline/<book>` + `POST /api/outline/<book>/edit` |
| AI 大纲助手 | `POST /api/outline/<book>/ai-expand` + `ai-suggest` |
| LLM 配置 | `GET /api/llm/providers` + `POST /api/config/<book>` (M8 同步所有 books) |
| 统计 | `GET /api/stats/<book>` |

完整 API 列表见 `review_ui/app.py` (40+ endpoints) 和 `docs/API.md`。

---

## v1.3 M8: LLM 设置同步

**改动**: `lib/screens/llm_config.dart` `_save()`

**之前**: 改完只存本机 `SharedPreferences`, web book.html 和其他设备看不到。

**之后**: 保存时同时:
1. 写 `SharedPreferences` (本地快速读)
2. 调 `listBooks()` 拿所有 books
3. 对每本书 `POST /api/config/<book>` 同步 `llm_provider` + `llm_model`
4. SnackBar 显示 "已保存 + 同步到 N 本书" 或部分失败提示

**数据流 + 边界 + 测试覆盖**: 见 `docs/MOBILE-LLM-SYNC.md`

**为什么需要这个**:
- 单书多人协作 (手机 + 电脑 + 平板) 需要统一 LLM 设置
- 之前 web 改了, mobile 必须重开 + 测试连通性才能感知

---

## 已知问题 / TODO

| 问题 | 影响 | TODO |
|------|------|------|
| 无单元测试 | 修改 UI 后只能手动验证 | 建 `test/` + mock api.dart |
| 无 E2E 测试 | 完整工作流没自动化覆盖 | integration_test/ |
| 截图文档缺真图 | 用户看不到实际样子 | APK 重编 + 拍照 |
| iOS 配置 | macOS 才能编 iOS | 留给 macOS 用户 |

---

## 相关 commit

- v1.3 M8 代码: `d390251` (sync) + `136c57b` (anti-pattern fix)
- v1.3 M5 原始: `80e3430` (LLM 配置 + AI 大纲 + 章节编辑)
- v1.3 M6+ AI 统一: `c8b3320` (book.html AI 设置)
- v1.3 M7 Project CRUD: `66aafcf` (create/edit/delete books)

---

## 相关文档

- `docs/MOBILE-LLM-SYNC.md` — M8 同步数据流 + 边界
- `docs/进展追踪.md` — v1.3 整体进展
- `CHANGELOG.md` — v1.3 M5-M8 完整 changelog
