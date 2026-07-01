# 小说生成工作流管理系统 — 设计方案 (DESIGN.md)

> 版本: v0.1 草稿
> 日期: 2026-07-01
> 起草: ANewName-1024 (魏超)
> 反馈对象: 设计评审 (您审阅后回复, 通过则进入实施)

---

## 1. 目标与范围

### 1.1 一句话目标

把 `novel_workflow` 从"个人 demo 脚本"升级为"可独立部署、可观测、可维护、有 Web UI 的本地长篇小说端到端生成管理系统"。

### 1.2 范围内 (v1.0)

- CLI 端: `novel.py` 15 个子命令保持稳定, 加 shell 自动补全 + 错误码统一
- LLM 端: 本地 Qwen3.6-35B / Qwythos-9B (llama-server :60443), 可配置切换其它模型
- Web UI 端: `review_ui` Flask + 反代, 支持章节预览 / 批量评审 / 进度大屏 / Basic Auth
- 数据端: 单机文件系统 (项目目录), JSON + Markdown, 不引入数据库
- 工程化: Git init / requirements.txt / pytest 骨架 / 日志 / 异常处理 / 配置管理
- 部署: 单 Windows 主机 + 反向 SSH 隧道 + VPS 公网入口, systemd 风格守护
- 可观测性: token 用量 / 章节字数 / 评审通过率 / LLM 时延, 4 个核心指标
- 安全: Basic Auth + 公网入口白名单, 后续可扩 JWT
- 备份: 项目目录每日快照 + 7 天保留

### 1.3 不在 v1.0 范围 (后续 Roadmap)

- ❌ 多用户/SSO/权限分级
- ❌ 第三方 LLM (OpenAI/Claude) API 通道
- ❌ AI 自动插画/封面
- ❌ Token 计费 (本地模型免计费)
- ❌ 数据库存储 (保持文件系统)
- ❌ 多端同步 (手机 App / 桌面客户端)
- ❌ 实时协同编辑

### 1.4 设计原则

| 原则 | 体现 |
|---|---|
| **本地优先** | 数据全在本地, LLM 在本地, 只在 VPS 做反代不打洞 |
| **单一职责** | lib/ 每个模块一个职责, CLI/UI/Web 只做编排 |
| **数据可读** | Markdown + JSON, 不依赖二进制, git friendly |
| **失败可见** | review_ui 不静默吞错, audit log 全留痕 |
| **配置外部化** | 模型/token/端口/路径全部从 config.yaml 读, 不硬编码 |
| **测试驱动** | 每个 lib 模块至少 1 个 smoke test, 入口子命令覆盖 ≥50% |
| **零破坏升级** | 现有数据格式不迁移, 老项目目录可直接跑新版 |

---

## 2. 架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                          使用者层                                       │
│  ┌────────────┐  ┌────────────────┐  ┌────────────────────────────┐    │
│  │  终端用户  │  │  浏览器 (PC)   │  │  浏览器 (手机/平板)         │    │
│  │  CLI 子命令│  │  review_ui     │  │  /novel/ 移动端响应式        │    │
│  └─────┬──────┘  └────────┬───────┘  └────────┬───────────────────┘    │
└────────┼──────────────────┼────────────────────┼──────────────────────┘
         │                  │                    │
         ▼                  ▼                    ▼
┌─────────────────────────────────┐    ┌─────────────────────────┐
│  Python CLI (novel.py)         │    │  VPS Nginx (:9080)        │
│  15 个子命令                  │    │  - /novel/*  → :9081     │
│  直接调 lib/*                 │    │  - /novel-api/* → :9081   │
└────────────┬──────────────────┘    └────────────┬──────────────┘
             │                                     │
             ▼                                     ▼ (sshd 反向隧道)
┌─────────────────────────────────┐    ┌─────────────────────────┐
│  业务逻辑 lib/*                  │    │  本机 sshd (:9081)       │
│  storage / llm / prompts        │    │  ↓                      │
│  outline / chapter / extract    │    │  Flask review_ui :21199  │
│  review / state / summary / ... │    │  ProxyFix + url_for      │
└────────┬───────────────┬────────┘    └──────────────┬──────────────┘
         │               │                            │
         ▼               ▼                            ▼
┌─────────────────┐  ┌───────────────────┐  ┌──────────────────┐
│  文件系统         │  │  llama-server     │  │  配置文件         │
│  projects/<书>   │  │  :60443 Qwen3.6   │  │  config.yaml     │
│  ├ config.json  │  │  / Qwythos-9B     │  │  (全局 + 项目)   │
│  ├ outline.json │  └───────────────────┘  └──────────────────┘
│  ├ chapters/    │
│  ├ reviews/     │
│  ├ memory/      │
│  └ backups/     │
└─────────────────┘
         │
         ▼ (同步)
┌─────────────────────────────────────────┐
│  备份: 每日 03:00 tar.gz 到 backups/    │
│  保留 7 天, 旧快照自动清理             │
└─────────────────────────────────────────┘
```

**关键设计决策**:

| # | 决策 | 理由 |
|---|---|---|
| D1 | **3 层架构: CLI / Web / lib** | CLI 给 power user, Web 给所有人, lib 是纯逻辑, 三者共用同一组 API, 不重复实现 |
| D2 | **文件系统而非 DB** | 章节是 Markdown, 中间产物是 JSON, 都没有跨表查询需求; 文件 + git 比 DB 简单 |
| D3 | **反代走 SSH 不走端口** | VPS 没有直接开内网穿透, sshd 是已经跑的服务, 复用现有 9080 路径; 隧道自动重连 |
| D4 | **Flask 不切生产 WSGI** | 单用户本地工具, dev server 足够; 写明 "非生产 WSGI" 在 README, 后续按需切 waitress/gunicorn |
| D5 | **llama-server 单进程共享** | 已经跑 8 小时稳定的 Qwen3.6-35B, 任何模型切换只能 stop+restart; v1.0 不做无缝切换 |
| D6 | **每本书独立目录** | 多本书可并行, 互不污染; 但 book 名带中文会引 GBK 坑 (TOOLS.md L27), 强制 ASCII 规范化 |
| D7 | **进度一致性靠 helper 守住** | L51 bug 已修, `mark_chapter_completed()` 是唯一写入点; 不许 read-modify-write |

---

## 3. 数据模型

### 3.1 目录结构

```
novel_workflow/
├── novel.py                  # CLI
├── lib/                      # 业务逻辑 14 个模块
├── projects/                 # N 本书
│   └── <book>/
│       ├── config.json       # per-book 配置 (题材/主角/章节数/字数)
│       ├── outline.json      # 2卷 × 10章 = 20章大纲
│       ├── progress.json     # 进度快照 (phase/current_chapter/completed)
│       ├── state.json        # 主角状态 + 时间线 + 伏笔
│       ├── style.json        # 风格锚点
│       ├── chapters/
│       │   └── ch_NNN.md
│       ├── summaries/
│       │   └── ch_NNN.txt
│       ├── self_checks/
│       │   └── ch_NNN.json
│       ├── reviews/
│       │   ├── ch_NNN.review.json
│       │   ├── ch_NNN.v2.md
│       │   └── audit.log
│       ├── memory/
│       │   ├── characters.json
│       │   ├── events.json
│       │   ├── foreshadowing.json
│       │   ├── relationships.json
│       │   └── world.json
│       └── backups/
│           └── 2026-07-01.tar.gz
├── review_ui/                # Flask Web UI
├── tests/                    # pytest 骨架
├── config.yaml               # 全局配置 (NEW)
├── requirements.txt          # 依赖 (NEW)
├── README.md                 # 用户文档 (NEW)
├── INSTALL.md                # 安装手册 (NEW)
├── DESIGN.md                 # 本文档 (NEW)
├── CHANGELOG.md              # 版本变更 (NEW)
└── LICENSE                   # MIT (NEW)
```

### 3.2 JSON Schema 摘要

**config.json** (per-book):
```json
{
  "book_name": "string",
  "genre": "string",
  "tone": "string",
  "protagonist": "string",
  "antagonist": "string | null",
  "main_plot": "string",
  "style": "string",
  "language": "zh|en",
  "target_chapters": 20,
  "words_per_chapter": 2500
}
```

**outline.json**:
```json
{
  "volumes": [
    {
      "volume_id": "vol_1",
      "title": "string",
      "chapters": [
        {"num": 1, "title": "string", "summary": "string", "key_events": ["..."], "characters": ["..."]}
      ]
    }
  ]
}
```

**progress.json** (L51 fix, 单一写入点):
```json
{
  "phase": "string (init|outline|writing|editing|done)",
  "current_chapter": 7,
  "total_chapters": 20,
  "chapters_completed": ["ch_001", "ch_002", "ch_006", "ch_007"],
  "last_updated": "2026-07-01T08:30:19"
}
```

**state.json**:
```json
{
  "protagonist_status": {"name": "...", "current_position": "...", "current_time": "..."},
  "timeline": [{"at": "2026-07-01T...", "event": "..."}],
  "active_foreshadowing": [{"id": "...", "planted_at": "...", "description": "..."}]
}
```

**config.yaml** (全局, NEW):
```yaml
llm:
  api_base: "http://127.0.0.1:60443/v1"
  default_model: "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
  fallback_models:
    - "Qwythos-9B-Claude-Mythos-5-1M-MTP-Q8_0.gguf"
  timeout_sec: 600
  max_retries: 3

review_ui:
  host: "127.0.0.1"
  port: 21199
  auth:
    enabled: true
    user: "weichao"
    password: "${REVIEW_UI_PASSWORD}"   # 从环境变量读
  log_level: "INFO"

backup:
  enabled: true
  schedule: "daily 03:00"
  retention_days: 7
  path: "projects/{book}/backups"

logging:
  level: "INFO"
  file: "logs/novel_workflow.log"
  rotation: "10MB x 5"
```

---

## 4. 接口设计

### 4.1 CLI 子命令 (现状 + 微调)

| 子命令 | 作用 | 状态 |
|---|---|---|
| `novel init <书>` | 初始化项目 | 稳定 |
| `novel outline <书>` | 生成大纲 | 稳定 |
| `novel write <书> --chapters 1-5` | 写章节 | 稳定 |
| `novel continue <书>` | 续写 | 稳定 |
| `novel review <书> [chapter]` | 评审 | 稳定 |
| `novel status <书>` | 查看进度 | 稳定 |
| `novel config <书> key=value` | 修改配置 | 稳定 |
| `novel export <书>` | 导出全书 Markdown | 稳定 |
| `novel review-queue/show/approve/reject/edit/false-positive/history` | 评审命令 | 稳定 |
| `novel extract <书> [chapter]` | 提取记忆 | 稳定 |
| `novel serve [--port N] [--host H]` | **NEW** 启动 review_ui Flask | 新增 |
| `novel backup <书>` | **NEW** 立即备份 | 新增 |
| `novel doctor` | **NEW** 环境诊断 (llama-server / python 版本 / 磁盘 / 端口) | 新增 |
| `novel schema <module>` | **NEW** 打印某模块的 JSON Schema | 新增 |

**关键改动**:
- 所有子命令统一错误码: `0=ok, 1=user error, 2=llm error, 3=storage error, 4=fatal`
- 所有子命令支持 `--json` 输出 (供脚本调用)
- 所有子命令支持 `--dry-run` (演练模式)

### 4.2 Web UI 路由 (现状 + 增强)

| Method | Path | 现状 | 增强后 |
|---|---|---|---|
| GET | `/` | 项目列表 | ✅ 不变 + 进度大屏 |
| GET | `/book/<book>` | 章节列表 + 评审队列 | ✅ + 摘要字数准确化 |
| GET | `/book/<book>/<ch>` | 单章详情 + 评审 | ✅ + 上一章/下一章导航 + 修订对比 |
| POST | `/api/approve/<book>/<ch>` | 批准 | ✅ + 支持批量 {chapter_ids: [...]} |
| POST | `/api/reject/<book>/<ch>` | 拒绝 | ✅ + 批量 |
| POST | `/api/edit/<book>/<ch>` | 人工编辑 | ✅ + diff 视图 |
| POST | `/api/batch-review` | **NEW** 批量审批 | 新增 |
| GET | `/api/projects` | 项目列表 | ✅ |
| GET | `/api/queue/<book>` | 待审队列 | ✅ |
| GET | `/api/review/<book>/<ch>` | 评审详情 | ✅ |
| GET | `/api/chapter/<book>/<ch>` | 章节正文 | ✅ |
| GET | `/api/stats/<book>` | 统计 | ✅ + token 用量 |
| GET | `/api/history/<book>` | audit log | ✅ |
| GET | `/api/diff/<book>/<ch>` | **NEW** 原文 vs 编辑后 diff | 新增 |
| GET | `/api/export/<book>/<format>` | **NEW** 导出 Markdown / JSON | 新增 |
| GET | `/api/metrics` | **NEW** 全局指标 (token/字数/通过率/latency) | 新增 |

**Web UI 设计原则**:
- 暗色主题 (现有), 移动端响应式 (现有)
- 中文优先, 状态徽章用图标不靠颜色 (色盲友好)
- 章节页关键改: 上一章/下一章链接 + diff 视图 (原文 vs v2 编辑后)
- 进度大屏: 4 象限展示 (已完成/在写/待审/待重写), 一眼看出阻塞

### 4.3 lib/ 模块 API (稳定契约)

| 模块 | 关键入口 | 备注 |
|---|---|---|
| `llm.LLM` | `complete()` / `call()` / `stream_completion()` / `estimate_cost()` | OpenAI-compat, 含 retry + token 估算 |
| `storage` | `project_root()` / `read_json()` / `write_json()` / `mark_chapter_completed()` | filesystem 抽象 |
| `prompts` | (常量模板, 每次调用直接 import) | 不写函数 |
| `memory` | `add/update/merge/dump` | 5 个 JSON 文件 |
| `outline` | `generate_outline()` / `load/save/get_chapter_info()` | LLM 调用 + JSON 校验 |
| `chapter` | `write_chapter()` / `run_post_write_pipeline()` | **核心入口** |
| `extract` | `extract_memory_from_chapter()` | NER + 事件抽取 |
| `review` | `review_chapter()` / `full_book_review()` | 一致性评审 |
| `review_service` | `auto_flag()` / `approve()` / `reject()` / `edit()` / `mark_false_positive()` / `apply_edit_to_chapter()` | 评审业务 |
| `self_check` | `run_self_check()` / `auto_rewrite()` | 关键问题检测 |
| `state` | `snapshot_protagonist()` / `update_timeline()` | 主角状态 |
| `summary` | `roll_summary()` | 滚动摘要 |
| `style` | `lock_style()` / `enforce_style()` | 风格锚点 |
| `context` | `build_context()` | 上下文窗口 |

**契约原则**: 每个公开函数加 type hints + docstring (Google style), 关键函数 (write_chapter / approve / extract) 加 pytest test case。

---

## 5. 部署模型

### 5.1 部署拓扑

```
单 Windows 主机 (WEI3216)
├─ llama-server :60443      # 已知, 长跑
├─ Flask review_ui :21199   # start_all.ps1 start
├─ sshd :22 客户端           # 用 -R 9081 反代到 VPS
└─ 备份脚本 (Task Scheduler) # 每日 03:00

VPS (8.137.116.121)
└─ nginx :9080
   └─ location /novel/ → 127.0.0.1:9081 (sshd reverse tunnel)
   └─ location /novel-api/ → 127.0.0.1:9081 (同上)
```

### 5.2 部署步骤 (INSTALL.md 摘要)

1. **前置**: Python 3.12+, Git, llama-server, ssh 客户端
2. **克隆**: `git clone https://github.com/ANewName-1024/novel-workflow.git`
3. **依赖**: `pip install -r requirements.txt`
4. **配置**: 复制 `config.yaml.example` → `config.yaml`, 设 password
5. **测试**: `python novel.py doctor`
6. **启动**: `pwsh review_ui/start_all.ps1 start`
7. **公网**: (可选) VPS 配 nginx + 反向 ssh 隧道
8. **守护**: Task Scheduler 设每小时检查 + 断了自动重启 (OpenClaw 已经实现 watchdog-like)

### 5.3 进程守护

| 进程 | 启动 | 故障处理 |
|---|---|---|
| llama-server | `start_qwythos.bat` | OpenClaw 已知会重启; 失败 spawn-fail 报警 |
| Flask review_ui | `start_all.ps1 start` | Task Scheduler 每 5 分钟检查, 死了拉起 |
| SSH tunnel | `start_all.ps1 start` | ClientAliveInterval=30 + AutoReconnect=yes |
| 备份脚本 | Task Scheduler 03:00 每天 | 失败写 log, 不阻塞 |

---

## 6. 安全模型

### 6.1 认证层级

| 层 | 当前 | 增强后 |
|---|---|---|
| **本地 (127.0.0.1:21199)** | 无 | 无 (trust loopback) |
| **VPS 公网 (:9080/novel/)** | 无 | **Basic Auth** (user/pass from env) |
| **VPS 公网 API** | 无 | Basic Auth + IP 白名单 (可选) |

### 6.2 数据安全

- 项目目录 git ignore (不提交内容, 只提交模板/代码)
- 备份目录 `projects/<书>/backups/` 不入 git, 不上传
- 配置 `config.yaml` 不入库, `config.yaml.example` 才入库
- 环境变量 `REVIEW_UI_PASSWORD` / `OPENAI_API_KEY` 走 .env (git ignored)

### 6.3 后续可扩 (Roadmap)

- JWT + refresh token
- 多用户 + 角色 (author / reviewer / admin)
- audit log 不可篡改 (hash chain)
- 章节内容水印 + 防截图

---

## 7. 可观测性

### 7.1 4 个核心指标

| 指标 | 指标名 | 来源 | 用途 |
|---|---|---|---|
| **L1 Token 用量** | `tokens_total` (input + output) | LLM `estimate_cost()` | 控制 LLM 成本 (本地免费但有上限) |
| **L2 章节字数** | `chapter_word_count` | `len(章节正文)` | 监控章节质量, 防止 LLM 写超 |
| **L3 评审通过率** | `review_pass_rate = approved/total` | review_service audit | 监控 LLM 一致性 |
| **L4 LLM 时延** | `llm_latency_p50/p95` | wall-clock 包 | 监控 llama-server 性能 |

### 7.2 暴露方式

- **CLI**: `novel doctor` 输出 4 个指标的当前值
- **Web UI**: `/api/metrics` JSON, 进度大屏 4 象限展示
- **未来**: Prometheus exporter + Grafana dashboard (v1.0 不含)

### 7.3 日志

- **结构化日志**: JSON Lines (LOG_LEVEL=INFO 时输出 INFO; 异常带 traceback)
- **轮转**: 单文件 10MB × 5 备份
- **位置**: `logs/novel_workflow.log`

---

## 8. 测试策略

### 8.1 测试金字塔

```
              ┌────────────────────┐
              │  E2E (人工抽查)    │  ≤ 10 min
              │  novel doctor      │
              └────────────────────┘
        ┌──────────────────────────────┐
        │  集成 (review_ui / Flask)    │  ≤ 5 min
        │  HTTP /api/projects 等       │
        └──────────────────────────────┘
   ┌──────────────────────────────────────┐
   │  单元 (lib/storage/llm/prompts)      │  ≤ 30s
   │  pytest tests/test_*.py              │
   └──────────────────────────────────────┘
```

### 8.2 必须有的测试

| 模块 | 测试 |
|---|---|
| `storage` | read_json / write_json / mark_chapter_completed idempotent |
| `llm` | mock OpenAI, 验证 retry / token 估算 |
| `review_service` | approve / reject / edit / mark_false_positive 4 个回归 (含 progress 同步 L51) |
| `chapter` | write_chapter 后 state / progress / memory 都对 |
| `extract` | 已知章节字符串 → 固定 events/characters |
| `outline` | generate 出来后 2卷 × 10章 = 20 |
| **prompt_too_long** | 给一个超长 prompt, 不会 OOM, 返回明确错误 |
| **gbk_safe** | 输出中文不被 GBK 编码 (L27 fix 回归) |
| **retry_exhausted** | mock 连续 fail, 最终抛 RuntimeError |

### 8.3 测试基础设施

- `pytest >=8.0`
- `pytest-cov` 覆盖率报告
- `responses` 库 mock OpenAI
- 不依赖真实 llama-server

---

## 9. 实施 Roadmap (按你推荐的 b+c)

### 9.1 Phase 1 — 工程化基础 (估 5h AI, ~你审阅 1h)

| 步骤 | 工作量 | 验收 |
|---|---|---|
| 9.1.1 `requirements.txt` (含版本) | 15min | `pip install -r` 成功 |
| 9.1.2 `config.yaml` + `.example` + 加载逻辑 | 30min | `novel doctor` 输出配置 |
| 9.1.3 `tests/` 骨架 + 7 个 smoke test | 1.5h | `pytest` 7/7 pass |
| 9.1.4 结构化日志 + 异常处理统一 | 1h | logs/error.log 有 traceback |
| 9.1.5 Git init + .gitignore + 首次 commit | 30min | `git log` 有 first commit |
| 9.1.6 `README.md` + `INSTALL.md` | 1h | README 包含 quick start |

### 9.2 Phase 2 — Web UI 增强 (估 4h AI, ~你审阅 1h)

| 步骤 | 工作量 | 验收 |
|---|---|---|
| 9.2.1 Basic Auth (env-based) | 1h | 公网访问要求用户名密码 |
| 9.2.2 章节详情加 上一章/下一章导航 | 30min | 点击跳章节 |
| 9.2.3 diff 视图 (`/api/diff`) | 1h | 原文 vs v2.md 红绿对比 |
| 9.2.4 批量审批 API + UI | 1h | `/api/batch-review` + 复选框 |
| 9.2.5 进度大屏 (首页 4 象限) | 30min | 一眼看出瓶颈 |
| 9.2.6 `metrics` API + token 用量统计 | 30min | `/api/metrics` JSON |

### 9.3 Phase 3 — 文档 + 部署 (估 1.5h AI, ~你审阅 30min)

| 步骤 | 工作量 | 验收 |
|---|---|---|
| 9.3.1 `CHANGELOG.md` (v0.1 → v1.0) | 15min | 历史可见 |
| 9.3.2 备份脚本 (Task Scheduler) | 45min | `novel backup` + 自动任务 |
| 9.3.3 `LICENSE` (MIT) | 5min | 标准 |
| 9.3.4 部署验证 (本地 + 公网) | 15min | 公网 + Basic Auth 都通 |

**总投入**: 估 ~10.5h AI + ~2.5h 你审阅 = **~13h**。我可以分批做, 每次 1-2h 后回 commit + 你检查。

### 9.4 Phase 4 (Roadmap, 不在 v1.0)

- 多用户 + JWT
- Prometheus exporter
- AI 插画 (ComfyUI 集成, OpsHub 已经搞了)
- 移动 App (Flutter remote-control-app)

---

## 10. 验收标准

### 10.1 v1.0 完工定义 (DoD)

- [ ] `git log` 有 v1.0.0 tag
- [ ] `pytest` 通过率 100%, 覆盖率 ≥40% (lib/ 关键模块)
- [ ] `novel doctor` 在干净环境 1 次性输出 ✅ 全绿
- [ ] 公网 `http://8.137.116.121:9080/novel/` + Basic Auth 能登录 → 进入项目 → 审批 → diff 视图 → 导出
- [ ] 每日 03:00 自动备份, 7 天后旧快照自动清理
- [ ] README 用 5 分钟能让新用户跑起来
- [ ] 4 个核心指标都有数据 (不是 0, 是真实值)

### 10.2 性能基线

| 操作 | 当前 (实测) | v1.0 目标 |
|---|---|---|
| 单章生成 (~2500字) | 60-90s | ≤ 90s |
| 1 个章节端到端 (生成→extract→summary→state→self_check) | 2-3 min | ≤ 3 min |
| review_ui 首页加载 | ~50ms | ≤ 200ms |
| 批量审批 10 章 | ~3s | ≤ 5s |

---

## 11. 风险与回滚

| 风险 | 概率 | 缓解 |
|---|---|---|
| llama-server 模型切错, 把"生成小说"变成"胡说八道" | 中 | config.yaml 默认 model + 启动前 `novel doctor` 探活 + 评审机制 (人工兜底) |
| 反向 ssh 隧道不稳, 公网 502 | 低 | ClientAliveInterval=30 + start_all.ps1 restart + watchdog |
| 备份脚本失败导致历史全丢 | 低 | 只读快照, 不修改数据; 每天跑一次足够; 月底手动 tar.gz 二次备份到 OneDrive |
| Flask dev server 不稳 | 中 | 写明 "非生产 WSGI", 提示切 waitress/gunicorn (v1.1) |
| review_ui 公网被爬虫扫到爆破 | 中 | Basic Auth + 失败 5 次锁 1h + audit log 看板 |

---

## 12. 待您决策 (this version)

| # | 选项 | 推荐 |
|---|---|---|
| Q1 | **Phase 1 + Phase 2 一起做** (推荐 b+c), 还是先 Phase 1 再 Phase 2? | 一起做, 一次到位 |
| Q2 | **Backup 策略**: Task Scheduler 自动 (v1.0 推荐) vs 手动命令 (你说 `novel backup` 一次就归档) | 自动 + 提供手动命令 |
| Q3 | **Web UI UI 框架**: 保留现有 Flask + 暗色模板 vs 切 HTMX / Vue | 保留现有 (单用户工具, 前端复杂度低) |
| Q4 | **部署基线**: WEI3216 主机 + VPS 公网 vs 仅本机 | 双轨 (本机默认, 公网可选) |
| Q5 | **Git 远程**: GitHub (ANewName-1024/novel-workflow) vs 仅本地 | GitHub (代码可分享/审阅/备份) |

---

## 附: 文件清单 (本次实施新增/修改)

| 路径 | 类型 | 备注 |
|---|---|---|
| `config.yaml` | 新增 | 全局配置 |
| `config.yaml.example` | 新增 | 模板 |
| `requirements.txt` | 新增 | 依赖 |
| `tests/` | 新增 | 7+ 测试 |
| `README.md` | 新增 | 用户文档 |
| `INSTALL.md` | 新增 | 安装手册 |
| `DESIGN.md` | **新增 (本文档)** | 设计方案 |
| `CHANGELOG.md` | 新增 | 版本变更 |
| `LICENSE` | 新增 | MIT |
| `.gitignore` | 新增 | git ignore |
| `.env.example` | 新增 | 环境变量模板 |
| `logs/` | 新增 | 日志目录 |
| `lib/config_loader.py` | 新增 | 加载 config.yaml |
| `lib/logging_setup.py` | 新增 | 结构化日志 |
| `lib/doctor.py` | 新增 | 环境诊断 |
| `lib/backup.py` | 新增 | 备份 + 清理 |
| `review_ui/auth.py` | 新增 | Basic Auth |
| `review_ui/diff_view.html` | 新增 | diff 模板 |
| `review_ui/batch_review.html` | 新增 | 批量 UI |
| `review_ui/templates/*.html` | 修改 | url_for + 上一章/下一章 + 进度大屏 |
| `review_ui/app.py` | 修改 | + 9 路由 (batch/diff/export/metrics) |
| `novel.py` | 修改 | + 3 子命令 (serve / backup / doctor / schema) |
| `lib/review_service.py` | 修改 | L51 helper 已加, 增量修 |

— END DESIGN.md —
