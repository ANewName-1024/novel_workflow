# Changelog

novel_workflow 的所有重要变更按 [Keep a Changelog](https://keepachangelog.com/) 风格记录。

## [Unreleased]

### Fixed (修复)

**M6.1: review_ui auth 锁死** (commit ffd0816)
- `config.yaml.example`: `auth.enabled: true` → `false` (默认安全, 公网部署再开) + 注释说明启用方法
- `review_ui/app.py`: `_auth_gate` + `login()` 加空 password safeguard, 跟 `_check_basic_auth_header` 行为对称
- 修复 L64: `get_config()` 浅合并 `config.yaml.example` 覆盖 `_defaults()` 锁死 review_ui
- 修复 L65: enabled=True + password='' 视为配置错误, 全部放行
- 3 新 tests (`TestAuthEnabledButEmptyPassword`): /api/projects 200, /login 跳首页, / 放行
- 实战验证: review_ui 默认 config 启动后 /api/projects 200 ✓

## [1.0.0] - 2026-07-01

工程化重构首版 (M1-M5 5 个 commit, 82 tests, 18 子命令)

### Added (新增)

**M5: review_ui 增强** (commit 7514d1d)
- Basic Auth + session 表单登录 (`/login`, `/logout`, `_auth_gate`, Basic Auth header 兼容)
- 章节导航 (prev/next 按字典序)
- 服务端 diff (`difflib.unified_diff`, CSS 高亮 +/-/@@, 模板条件渲染)
- 批量审批 (`/api/batch-approve`, 独立 try/except, 聚合 ok/failed)
- 20 新 tests (`test_review_ui_auth.py` 15 + `test_review_ui_diff.py` 5)

**M4: 统一日志 + 错误码** (commit 54e6535)
- `lib/errors.py`: `ErrorCode` IntEnum (9 个退出码) + `NovelError` 带 code/message/detail
- `lib/logging_setup.py`: 单例 `setup_logging()`, `RotatingFileHandler` + stdout, `_parse_rotation` 支持 `10MB x 5`
- `novel.py` 重构: 顶层 `try/except`, 16 处 `sys.exit` → `raise NovelError`, `_dispatch()` 拆分
- 顺手 fix Windows GBK stdout emoji 渲染 (`sys.stdout.reconfigure(encoding="utf-8")`)
- 18 新 tests (`test_errors.py` 7 + `test_logging.py` 11)

**M3: pytest 骨架** (commit eb71fe0)
- 7 个 smoke test (44 cases): smoke_init / smoke_status / smoke_outline / smoke_write / smoke_review / smoke_export / smoke_backup
- `conftest.py` 提供 `tmp_projects_root` fixture, monkeypatch `storage.PROJECTS_ROOT`
- `requirements-dev.txt` 分离 dev 依赖 (pytest-cov / responses / ruff / mypy)

**M2: 全局配置 + doctor + backup** (commit 3327370)
- `lib/config_loader.py`: 3 层合并 (defaults < example < file) + env 占位符 `${VAR:-default}`
- `lib/doctor.py`: 9 项环境检查 (Python / 依赖 / LLM / 端口 / 磁盘 / Git / config / 写权限 / 项目目录)
- `lib/backup.py`: tar.gz 快照 helpers
- 新增子命令: `doctor`, `serve`, `backup`
- `config.yaml.example` + `.env.example`

**M1: 工程化基础** (commit ef1b447)
- `lib/` 模块化 (19 个文件, 单一职责)
- `__init__.py` + import 路径统一
- `requirements.txt` (openai / flask / tiktoken / pytest / responses)
- `.gitignore` 完整配置 (不入库: projects/* 运行时数据, config.yaml, .env, *.log, *.tar.gz)
- 子命令数从 15 → 18

### Changed

- `novel.py`: 35 KB, 19 个子命令, 统一错误码 (M4)
- `review_ui/app.py`: 24 routes, Basic Auth gate (M5)
- 退出码语义化 (M4, 9 个 ErrorCode 跟 sys.exit 对齐)

### Fixed

- 跨进程 review_service.edit() 不调 `mark_chapter_completed()` (L51 修复, M1 验证)
- L55 nginx `/novel/` path 前缀反向代理路径改写 (M2 期间)
- L57 monkeypatch storage.PROJECTS_ROOT 失败 (M1 fix)
- L60 Windows stdout GBK 不支持 emoji 渲染 (M4 fix)
- L61 "正常跳过" 误用 sys.exit(0) (M4 fix)

### Security

- review_ui Basic Auth 默认 disabled, 生产必须 `enabled: true` + 强密码
- `config.yaml` / `.env` 不入库 (`.gitignore` 已配)
- `FLASK_SECRET_KEY` 生产必须改 env, fallback 是 dev-only

### Deprecated

无

### Removed

无

## 版本号约定

- 主版本 (1.x): 不兼容 API / 数据格式变更 (目前承诺 0 破坏升级)
- 次版本 (x.0): 重大功能 (M1-M5 各自对应一次次版本)
- 修订号 (x.x.0): bug fix / 文档

## 路线图

- [ ] v1.1: Web UI 加进度大屏 + token 用量图表
- [ ] v1.2: 多书并发写 (后台任务队列)
- [ ] v1.3: 移动 App (Flutter, 复用 API)
- [ ] v2.0: 多用户 / 角色权限 (post-MVP)