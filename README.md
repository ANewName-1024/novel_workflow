# novel_workflow

> 长篇小说端到端生成管理系统 — 本地 LLM + CLI + Web UI + 自动备份

把"灵光一现的脑洞"变成结构化、可审稿、可恢复的长篇: 三阶大纲、章节迭代生成、
自动评审、人工编辑、批量审批, 全在本地跑。

## ✨ 特性

- **本地优先** — LLM 走本地 llama-server (Qwen3.6-35B / Qwythos-9B 等), 数据全部在 `projects/` 下, 不依赖云端
- **端到端** — 从一行主 plot 到完整书稿, CLI 一条命令跑完
- **Web UI** — 浏览器 (PC + 移动) 看章节、diff、批量审批, 可 Basic Auth 保护
- **自动评审** — 章节写完自动跑规则检查 (字数 / 角色一致性 / 伏笔 / 文风漂移), 不通过进人工队列
- **可恢复** — 项目每日快照, 7 天保留; Git-friendly (Markdown + JSON)
- **工程化** — pytest 82 cases, 统一错误码 + 退出码, 结构化日志 (RotatingFileHandler)

## 🚀 30 秒上手

```bash
# 1. 启动本地 LLM (llama-server 跑在 :60443, 用你喜欢的模型)
llama-server -m qwen3.6-35b.gguf -ngl 99 --host 127.0.0.1 --port 60443 &

# 2. 装依赖
pip install -r requirements.txt

# 3. 初始化项目
python novel.py init my_book --main-plot "一个废弃矿坑里的少年, 醒来发现自己身处末世"

# 4. 生成大纲
python novel.py outline my_book

# 5. 写第 1 章
python novel.py write my_book --chapter 1

# 6. 看状态
python novel.py status my_book

# 7. 启动 Web UI
python novel.py serve my_book    # → http://127.0.0.1:21199/novel/
```

## 📋 子命令

| 命令 | 用途 |
|---|---|
| `init <书> --main-plot "..."` | 初始化项目, 写主 plot |
| `outline <书>` | 生成三阶大纲 (卷 / 章 / 节) |
| `write <书> --chapter N` | 写第 N 章 (含自动评审) |
| `continue <书>` | 从断点继续写 |
| `review <书> <ch>` | 触发评审 (默认自动跑, 这里手动) |
| `status <书>` | 进度面板 (字数 / 评审通过率 / LLM 时延) |
| `config` | 查看 / 改配置 |
| `export <书>` | 导出全书 (单本 Markdown) |
| `review-queue <书>` | 列出待人工评审的章节 |
| `review-show <书> <ch>` | 看某章详情 (含正文) |
| `review-approve <书> <ch>` | 人工通过 |
| `review-reject <书> <ch>` | 人工拒退 (重写) |
| `review-edit <书> <ch>` | 人工编辑 (v2 落盘) |
| `review-false-positive <书> <ch>` | 标记自检为误报 |
| `review-history <书>` | 汇总所有章节评审历史 |
| `extract <书>` | 从最新章节提取记忆 (人物/事件/伏笔) |
| `doctor` | 环境诊断 (Python / 依赖 / LLM / 端口 / 磁盘 / Git) |
| `serve <书>` | 启动 review_ui Web 界面 |
| `backup <书> [--clean]` | 立即备份, `--clean` 只留 7 天内 |

子命令都支持 `--help` 看参数。

## 🌐 Web UI (review_ui)

启动: `python novel.py serve <书>` → `http://127.0.0.1:21199/novel/`

| 路由 | 用途 |
|---|---|
| `/` | 项目列表 |
| `/book/<书>` | 单书总览 + 待审队列 + 批量审批 |
| `/book/<书>/<ch>` | 单章详情 + 上下章导航 + v1 vs v2 diff |
| `/api/projects` | JSON: 全部书 |
| `/api/queue/<书>` | JSON: 待审队列 |
| `/api/diff/<书>/<ch>` | JSON: unified diff |
| `/api/batch-approve/<书>` | POST: 批量批准 `{chapters, reviewer, notes}` |

**v1.1 流水线面板** (`/dashboard/<book>`):
- 浏览器触发写章节 (替代 `python novel.py write`)
- 实时日志流 (SSE / EventSource)
- 7 阶段进度条 (context → writing → extract → summary → state → self_check → done)
- Token 用量折线图 (Chart.js, 近 7 天 input/output)
- 启动 / 取消 / 状态轮询 (5s 间隔)
- 设计文档: `docs/DESIGN-v1.1-web-pipeline.md`

| API | 用途 |
|---|---|
| `POST /api/pipeline/start/<book>` | 触发写 1 章节 (form: chapters, auto_rewrite) |
| `POST /api/pipeline/cancel/<book>` | 杀子进程 + 标 cancelled (5s grace) |
| `GET /api/pipeline/status/<book>` | 读 `.pipeline_state.json` + PID 校准 |
| `GET /api/pipeline/logs/<book>?tail=100` | 返回 log 最后 N 行 (默认 100) |
| `GET /api/pipeline/logs/<book>/stream` | SSE 流 (text/event-stream) |
| `GET /api/pipeline/metrics/<book>?range=7d` | token 用量聚合 (calls / in / out / ms) |

**Auth** (`config.yaml` 的 `review_ui.auth` 块):
- `enabled: false` (默认) — 全部放行
- `enabled: true` + `password: "..."` — 走 `/login` 表单登录, 兼容 `Authorization: Basic ...` header

## ⚙️ 配置

`config.yaml` (从 `config.yaml.example` 复制, 不入库):

```yaml
llm:
  api_base: http://127.0.0.1:60443/v1    # llama-server OpenAI-compat
  default_model: Qwythos-9B
  fallback_models: [Qwen3.6-35B]
  timeout_sec: 600
  max_retries: 3

review_ui:
  host: 127.0.0.1
  port: 21199
  proxy_prefix: /novel
  auth:
    enabled: true
    user: ${REVIEW_UI_USER:-weichao}
    password: ${REVIEW_UI_PASSWORD:-}

backup:
  enabled: true
  retention_days: 7
  schedule_time: "03:00"

logging:
  level: INFO
  file: logs/novel_workflow.log
  rotation: "10MB x 5"   # 单文件 10MB, 保留 5 个

projects:
  root: projects
  normalize_ascii_book_name: true
```

`config.yaml` 走 3 层合并 (defaults < example < file), env 占位符 `${VAR:-default}` 在 file 层替换。
`FLASK_SECRET_KEY` env 配 session 加密 key (生产必须改)。

## 🧪 测试

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q         # 82 cases, ~2.5s
python -m pytest tests/ --cov=lib  # 覆盖率
```

## 📁 项目结构

```
novel_workflow/
├── novel.py             # CLI 入口 (19 个子命令, 统一错误码)
├── lib/                 # 业务模块
│   ├── llm.py           # OpenAI-compat LLM 包装
│   ├── storage.py       # Markdown + JSON 持久化
│   ├── outline.py       # 三阶大纲
│   ├── chapter.py       # 单章生成 + 提取 + 摘要 + 自检
│   ├── review_service.py# 自动评审 + 人工编辑落盘
│   ├── config_loader.py # 3 层 config 合并 + env 替换
│   ├── doctor.py        # 环境诊断
│   ├── backup.py        # tar.gz 快照
│   ├── errors.py        # ErrorCode IntEnum + NovelError
│   └── logging_setup.py # 单例 logger + RotatingFileHandler
├── review_ui/           # Flask Web UI
│   ├── app.py           # 24 routes (M5 加 auth / diff / batch)
│   └── templates/
├── tests/               # pytest, 82 cases
├── config.yaml.example  # 配置模板
├── DESIGN.md            # 详细设计文档
├── INSTALL.md           # 安装步骤 (Win / Linux)
└── CHANGELOG.md         # 版本变更
```

## 🔍 环境诊断

```bash
python novel.py doctor
```

9 项检查: Python 版本 / 依赖完整性 / LLM 连通性 / 端口可用 / 磁盘空间 / Git 状态 / config 合法性 / 写权限 / 项目目录结构。

## 💾 备份

- **手动**: `python novel.py backup <书>` (生成 `projects/<书>/backups/yyyymmdd.tar.gz`)
- **清理旧备份**: 加 `--clean` (按 `backup.retention_days` 删)
- **每日自动**: 见 `scripts/install_backup_task.ps1` (Windows Task Scheduler)

## 🚪 退出码 (M4 起)

| code | 含义 |
|---|---|
| 0 | OK |
| 1 | 通用错误 (未分类) |
| 2 | 参数错误 |
| 3 | 项目/章节不存在 |
| 4 | LLM 调用失败 |
| 5 | config 加载/解析失败 |
| 6 | IO 错误 (读写/磁盘) |
| 7 | 认证失败 |
| 8 | 评审失败 (章节自检硬阻断) |

## 📜 License

MIT