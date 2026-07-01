# novel_workflow v1.1 — Web 流水线管理面板 — 设计方案

> **范围**: 评审已有 web UI (review_ui), 流水线本身仍依赖 CLI. v1.1 把"写章节 / 看进度 / 看日志 / 看 token"全部 web 化.
> **作者**: 小爪 🐾
> **日期**: 2026-07-01
> **状态**: 设计阶段 (待评审)

---

## 1. 一句话目标

把 `python novel.py write 测试书籍 --chapters 8` 这种 CLI 操作, 变成**浏览器里点按钮 + 实时看进度 + 看日志 + 看 token 用量**.

## 2. 现状差距 (为什么需要 v1.1)

| 操作 | 现状 | 想要 |
|---|---|---|
| 触发写章节 | 敲 CLI 命令 | 浏览器按钮 |
| 看流水线跑到哪了 | 读 stdout / log 文件 | 进度条 + 阶段状态 |
| 看实时日志 | `tail -f logs/novel.log` | SSE 流式日志查看器 |
| 看 token 消耗 | 没有 (LLM 端也没暴露) | Chart.js 折线图 |
| 取消写任务 | `Ctrl+C` 进 cmd | 浏览器按钮 |
| 多书并发 | 1 本 1 个 cmd 窗口 | v1.1: 1 本锁; v1.2: 队列 |

---

## 3. 架构设计

### 3.1 总体思路

**复用 review_ui Flask app, 加 1 个新 blueprint (dashboard)** — 不另起服务

```
┌────────────────────────────────────────────────────────────┐
│ 浏览器:  http://127.0.0.1:21199/                            │
│                                                            │
│  /login  /  /book/<book>  /book/<book>/<ch>  ← 现有        │
│  /dashboard/<book>                          ← 新增 v1.1    │
└────────────────────────────────────────────────────────────┘
                           │ HTTP
                           ▼
┌────────────────────────────────────────────────────────────┐
│ review_ui Flask (单进程, 端口 21199)                       │
│  ├─ 现有 17 routes (M5 review 评审)                        │
│  └─ 新增 6 routes (v1.1 dashboard)                         │
│      ├─ POST /api/pipeline/start    触发写                 │
│      ├─ POST /api/pipeline/cancel   取消                   │
│      ├─ GET  /api/pipeline/status   阶段+PID+ETA           │
│      ├─ GET  /api/pipeline/logs     最近 N 行              │
│      ├─ GET  /api/pipeline/logs/stream  SSE 流             │
│      └─ GET  /api/pipeline/metrics  token 聚合             │
└────────────────────────────────────────────────────────────┘
                │                          │
       subprocess.Popen              read state file
                │                          │
                ▼                          ▼
┌──────────────────────────┐  ┌────────────────────────────┐
│ python novel.py write ... │  │ projects/<book>/             │
│ (子进程, PID 记到 state)  │  │  ├─ .pipeline_state.json    │
│ stdout → tee 到 log 文件  │  │  ├─ metrics.jsonl           │
│                           │  │  └─ logs/pipeline.log       │
└──────────────────────────┘  └────────────────────────────┘
```

### 3.2 为什么不做"另起服务"

- ✅ 共享 Basic Auth (单一鉴权入口)
- ✅ 共享端口 / 进程 (部署简单, 不需要 nginx 反代)
- ✅ 复用 review_ui 的 tests 框架 (conftest, fixtures)
- ❌ 缺点: review_ui 仍跑 Flask dev server, 多线程差. v1.1 可接受 (单用户), v1.2 切 waitress/gunicorn

### 3.3 关键决策 (7 个)

| # | 决策 | 选项 | 选定 | 理由 |
|---|---|---|---|---|
| 1 | 服务架构 | A) 复用 review_ui  B) 另起服务 | **A** | 共享鉴权 + 部署简单 |
| 2 | 任务执行模型 | A) 线程  B) 子进程  C) 消息队列 | **B** | 简单 + 跨平台 + 杀进程可控 |
| 3 | 状态持久化 | A) 内存  B) JSON 文件  C) SQLite | **B** | 跟 projects/ 同源, review_ui 重启不丢 |
| 4 | 实时日志 | A) 轮询  B) WebSocket  C) SSE | **C** | 单向推送够用, EventSource API 简单 |
| 5 | 阶段追踪 | A) parse stdout  B) 结构化 marker  C) 改 chapter.py 显式报告 | **C** | 最稳, 不依赖 stdout 格式 |
| 6 | Token 统计 | A) LLM 端 log 抓  B) 改 LLM 包装器记录 | **B** | 不依赖 llama-server 私有 API |
| 7 | 并发模型 | A) 1 本 1 进程  B) 1 全局 1 进程  C) 队列 | **A** | v1.1 简单; v1.2 升级队列 |

---

## 4. 数据模型

### 4.1 `.pipeline_state.json` (per-book, 写时)

```json
{
  "book": "测试书籍",
  "status": "running",          // idle | running | done | failed | cancelled
  "pid": 12345,                 // 子进程 PID
  "started_at": "2026-07-01T22:50:00",
  "ended_at": null,             // null = 跑中
  "current_chapter": 8,
  "current_stage": "self_check", // context | writing | extract | summary | state | self_check | done
  "stage_started_at": "2026-07-01T22:53:42",
  "exit_code": null,            // 0 = 成功, != 0 = 失败
  "log_path": "projects/测试书籍/logs/pipeline.log",
  "error": null                 // 失败时的 stderr tail
}
```

### 4.2 `metrics.jsonl` (per-book, append-only)

```jsonl
{"ts":"2026-07-01T22:51:30","stage":"writing","ch":8,"model":"Qwen3.6","input_tokens":4521,"output_tokens":1203,"latency_ms":32100}
{"ts":"2026-07-01T22:52:15","stage":"extract","ch":8,"model":"Qwen3.6","input_tokens":3100,"output_tokens":410,"latency_ms":9200}
{"ts":"2026-07-01T22:52:25","stage":"summary","ch":8,"model":"Qwen3.6","input_tokens":2900,"output_tokens":280,"latency_ms":10100}
{"ts":"2026-07-01T22:52:35","stage":"state","ch":8,"model":"Qwen3.6","input_tokens":2900,"output_tokens":320,"latency_ms":9800}
{"ts":"2026-07-01T22:52:55","stage":"self_check","ch":8,"model":"Qwen3.6","input_tokens":2950,"output_tokens":180,"latency_ms":12000}
```

**每行 = 一次 LLM 调用**. 字段简洁, 后续易扩展 (cost_usd / temperature / stop_reason).

### 4.3 `pipeline.log` (per-book, 文本日志, tee 写入)

```
[2026-07-01T22:50:00] [PIPELINE] book=测试书籍 ch=8 stage=context status=start
[2026-07-01T22:50:01] [Chapter 8] 视角: 第一人称 | 字数目标: 2500
[2026-07-01T22:50:01] [Chapter 8] 上下文策略: full | 估算输入: ~12000 tok
[2026-07-01T22:50:01] [PIPELINE] book=测试书籍 ch=8 stage=context status=done duration=1s
[2026-07-01T22:50:01] [PIPELINE] book=测试书籍 ch=8 stage=writing status=start
[2026-07-01T22:51:30] [Chapter 8] ✓ 写入 ch_008.md (3500 chars)
[2026-07-01T22:51:30] [PIPELINE] book=测试书籍 ch=8 stage=writing status=done duration=89s tokens_in=4521 tokens_out=1203
[2026-07-01T22:51:31] [PIPELINE] book=测试书籍 ch=8 stage=extract status=start
...
```

**`[PIPELINE]` 前缀 = 阶段 marker, dashboard parse 这个显示进度条.**

---

## 5. 接口设计

### 5.1 6 个新 API (v1.1 蓝图 `dashboard_bp`)

| Method | Path | 用途 | 参数 | 返回 |
|---|---|---|---|---|
| POST | `/api/pipeline/start/<book>` | 触发写 | `chapters=8&auto_rewrite=true` (form) | `{status, pid, started_at}` |
| POST | `/api/pipeline/cancel/<book>` | 取消 | — | `{status, killed_pid}` |
| GET | `/api/pipeline/status/<book>` | 当前状态 | — | `.pipeline_state.json` |
| GET | `/api/pipeline/logs/<book>?tail=100` | 最近日志 | `tail=N` (默认 100) | `{lines: [...]}` |
| GET | `/api/pipeline/logs/<book>/stream` | SSE 流 | — | `text/event-stream` |
| GET | `/api/pipeline/metrics/<book>?range=7d` | token 聚合 | `range=7d\|all` | `{chapters: [{n, in, out, latency}], total_in, total_out}` |

**错误码**: 复用 `lib/errors.py` 的 `NovelError` (NOT_FOUND / INVALID_ARGS / GENERIC), 前端 fetch 捕获 `e.code` 显示提示

**鉴权**: 全部走 review_ui 现有 `before_request` + `_auth_gate` (Basic Auth + session)

### 5.2 1 个新页面 `/dashboard/<book>`

```
┌──────────────────────────────────────────────────────────────┐
│ 测试书籍 — 流水线面板                       [← 返回评审 UI]  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│ ┌─ 控制面板 ─────────────────────────────────────────────┐  │
│ │  进度: 4/20 章  阶段: idle  上次跑: 7/01 22:51 成功    │  │
│ │  [ 写下一章 (ch_008) ]   [ 取消当前 ]   [ 重跑自检 ]   │  │
│ └────────────────────────────────────────────────────────┘  │
│                                                              │
│ ┌─ 实时进度条 (running 时显示) ──────────────────────────┐  │
│ │  [context]─✓─[writing]─▶─[extract]─[summary]─[state]   │  │
│ │              ↑ 跑这里 89s/120s                          │  │
│ └────────────────────────────────────────────────────────┘  │
│                                                              │
│ ┌─ Token 用量 (近 7 天) ─────────────────────────────────┐  │
│ │     input   output                                       │  │
│ │  5k ┤    ▄▆█                                             │  │
│ │  4k ┤  ▃▆███                                             │  │
│ │  3k ┤▃██████                                             │  │
│ │  2k ┤███████                                             │  │
│ │       ch5 ch6 ch7 ch8                                    │  │
│ │  总: input 14,231 / output 2,893 / 4 章                  │  │
│ └────────────────────────────────────────────────────────┘  │
│                                                              │
│ ┌─ 实时日志 (auto-scroll, max 500 行, 可暂停) ───────────┐  │
│ │ [22:51:30] [PIPELINE] stage=writing status=start        │  │
│ │ [22:51:30] [Chapter 8] 视角: 第一人称 | 字数目标: 2500  │  │
│ │ [22:51:30] [Chapter 8] 上下文策略: full                 │  │
│ │ [22:51:30] [Chapter 8] 估算输入: ~12000 tok             │  │
│ │ [22:52:00] [Chapter 8] ✓ 写入 ch_008.md (3500 chars)    │  │
│ │ ...                                                     │  │
│ └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 5.3 与现有 review_ui 的协作

- book.html 顶部加 1 个按钮 **"进入流水线面板"** → 跳 `/dashboard/<book>`
- dashboard.html 顶部加 1 个按钮 **"← 返回评审"** → 跳 `/book/<book>`
- 共用 nav / CSS (`.chapter-pill` 等风格保持一致)

---

## 6. lib/ 模块改动

### 6.1 新增 `lib/pipeline.py` (~200 行)

```python
class PipelineRunner:
    """管理 1 本书的 1 个写章节子进程."""
    
    def start(self, book: str, chapters: int | list[int], 
              auto_rewrite: bool = True) -> dict:
        """subprocess.Popen(['python', 'novel.py', 'write', book, '--chapters', ...]) 
           + tee stdout 到 log + 写 .pipeline_state.json."""
    
    def status(self, book: str) -> dict | None:
        """读 .pipeline_state.json, 检查 PID 是否还活着 (os.kill(pid, 0))."""
    
    def cancel(self, book: str) -> dict:
        """kill 子进程 (Windows: CREATE_NEW_PROCESS_GROUP + taskkill /T)."""
    
    def tail_log(self, book: str, n: int = 100) -> list[str]:
        """读 log 文件最后 N 行."""
    
    def stream_log(self, book: str) -> Iterator[str]:
        """yield 新行 (用于 SSE 端点)."""
```

### 6.2 改 `lib/llm.py` (加 token 记录)

```python
def complete(self, prompt, system, **kwargs) -> str:
    # ... 现有代码 ...
    resp = self.client.chat.completions.create(...)
    # NEW: 记录
    usage = resp.usage  # prompt_tokens / completion_tokens
    if self._metrics_callback:
        self._metrics_callback(
            stage=kwargs.get("stage", "unknown"),
            ch=kwargs.get("ch", 0),
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            latency_ms=(time.time() - t0) * 1000,
        )
    return resp.choices[0].message.content or ""

def set_metrics_callback(self, cb):
    """由 PipelineRunner 注入, 每次 LLM 调用后回调."""
    self._metrics_callback = cb
```

### 6.3 改 `lib/chapter.py` (加 PIPELINE marker)

在 `write_chapter` 和 `run_post_write_pipeline` 每个阶段入口加 1 行:

```python
print(f"[PIPELINE] book={book} ch={chapter_num} stage=context status=start")
# ... context 阶段 ...
print(f"[PIPELINE] book={book} ch={chapter_num} stage=context status=done duration={elapsed}s")

print(f"[PIPELINE] book={book} ch={chapter_num} stage=writing status=start")
text = llm.complete(...)
print(f"[PIPELINE] book={book} ch={chapter_num} stage=writing status=done ...")

# extract / summary / state / self_check 同理
```

`PIPELINE` 标志的输出会被 `PipelineRunner.start()` 时启动的 `tee` 写入 `pipeline.log`, dashboard 解析用.

### 6.4 改 `lib/config_loader.py` (新增 dashboard 配置)

```yaml
# config.yaml.example 新增
dashboard:
  enabled: true
  log_tail_default: 100
  log_max_buffer: 500       # SSE 客户端 buffer 上限
  metrics_retention_days: 30
  cancel_grace_seconds: 5   # cancel 后等子进程退出的宽限
```

---

## 7. review_ui 改动 (6 routes + 1 页面 + 1 按钮)

### 7.1 `review_ui/dashboard.py` (新文件, ~350 行)

```python
from flask import Blueprint, Response, request, jsonify, stream_with_context
import subprocess, json, time

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

@dashboard_bp.route("/<book>")
def dashboard_page(book): ...

@dashboard_bp.route("/api/pipeline/start/<book>", methods=["POST"])
def api_start(book): ...

@dashboard_bp.route("/api/pipeline/cancel/<book>", methods=["POST"])
def api_cancel(book): ...

@dashboard_bp.route("/api/pipeline/status/<book>")
def api_status(book): ...

@dashboard_bp.route("/api/pipeline/logs/<book>")
def api_logs(book): ...

@dashboard_bp.route("/api/pipeline/logs/<book>/stream")
def api_logs_stream(book): ...

@dashboard_bp.route("/api/pipeline/metrics/<book>")
def api_metrics(book): ...
```

### 7.2 `review_ui/app.py` 改 2 处

```python
# 1. 注册蓝图
from .dashboard import dashboard_bp
app.register_blueprint(dashboard_bp)

# 2. 模板路径加 dashboard.html
```

### 7.3 `review_ui/templates/dashboard.html` (新文件, ~250 行)

- 用 vanilla JS (不引前端框架) + Chart.js (CDN, 1 个 <script>)
- EventSource API 接 SSE
- fetch() 接 6 个 API
- 进度条用 CSS animation
- 实时日志: `<pre id="log">` 配合 setInterval 自动 scroll 到底

### 7.4 `review_ui/templates/book.html` 改 1 处

顶部加 1 个按钮:
```html
<a class="nav-btn" href="{{ url_for('dashboard.dashboard_page', book=book) }}">
  ⚙️ 流水线面板
</a>
```

---

## 8. 测试策略

### 8.1 新增 `tests/test_pipeline.py` (~12 cases)

- `PipelineRunner.start()` → 子进程起来, state.json 写入
- `PipelineRunner.start()` 重复调用 → 抛 NovelError(AUTH_ERROR 等价: "已有任务在跑")
- `PipelineRunner.status()` → 读 state.json, PID 还活着
- `PipelineRunner.cancel()` → 子进程被杀, state.json 标 cancelled
- `PipelineRunner.tail_log()` → 返回正确行数
- `PipelineRunner.stream_log()` → generator, 持续 yield 新行
- mock subprocess: 不真起进程, 验参数正确
- 跨平台: Windows / Linux 都能跑 (用 psutil 替代 taskkill)

### 8.2 新增 `tests/test_dashboard_api.py` (~10 cases)

- 6 个 API 各 1 个 happy path
- 401 未授权
- 404 找不到项目
- 409 重复 start (已有任务在跑)
- SSE 流: 推 3 行后断开, 验证 content-type + 3 个 `data:` 块

### 8.3 改 `tests/test_llm.py` (~4 cases)

- `LLM.complete()` 调用 metrics_callback
- 回调收到正确的 input/output tokens
- 多次调用, callback 多次触发 (顺序对)
- callback 异常不破坏 LLM 调用

### 8.4 改 `tests/test_chapter.py` (~2 cases) — 新建

- 跑 write_chapter, 验 stdout 含 `[PIPELINE]` marker
- 验各 stage 的 start/done 配对完整

**总新增测试**: ~28 cases, 总测试 94 → 122

---

## 9. 实施 Roadmap (里程碑)

> **预估**: AI 工作量 ~5-6h, 人类审核 ~1.5h. 跨 2-3 个工作日.

### M1: 任务调度基础 ✅ **基础必须先打**
- 估时: ~1h (AI 45min + 审核 15min)
- 产出:
  - `lib/pipeline.py` 完整实现
  - `lib/config_loader.py` 加 dashboard 节
  - `tests/test_pipeline.py` 8 cases
  - `config.yaml.example` 更新
- 验收: `PipelineRunner` 单测全过, 跨平台 (Win + Linux) 都能 subprocess 起停

### M2: API 端点 + Web 触发器
- 估时: ~45min
- 产出:
  - `review_ui/dashboard.py` 6 routes
  - `review_ui/app.py` 注册蓝图
  - book.html 加按钮
  - `tests/test_dashboard_api.py` 6 cases (各 API happy path + 401 + 404)
- 验收: `curl -X POST /api/pipeline/start/测试书籍` 真起子进程, 30s 后 status 显示 running

### M3: 实时日志流 (SSE)
- 估时: ~1h
- 产出:
  - `api_logs_stream` 完整 SSE 实现
  - dashboard.html 接 EventSource
  - `tests/test_dashboard_api.py` +2 cases (SSE 流)
- 验收: dashboard 上实时滚动 log, 无明显延迟 (<2s)

### M4: 流水线阶段可视化
- 估时: ~45min
- 产出:
  - `lib/chapter.py` 加 8 个 `[PIPELINE]` marker
  - dashboard.html 加进度条组件
  - `tests/test_chapter.py` 2 cases (verify marker)
- 验收: 跑一次写章节, dashboard 进度条 8 个阶段全亮, done 后停在 ✓

### M5: Token 用量统计 + 图表
- 估时: ~1.5h
- 产出:
  - `lib/llm.py` 改 complete/call 加 metrics callback
  - `lib/pipeline.py` 注入 callback, 写 metrics.jsonl
  - `api_metrics` 聚合 endpoint
  - dashboard.html 加 Chart.js 折线图
  - `tests/test_llm.py` +4 cases
- 验收: dashboard 显示近 7 天 token 趋势, 数字跟 metrics.jsonl 行数对得上

### M6: 文档 + 验收
- 估时: ~30min
- 产出:
  - `CHANGELOG.md` v1.1 完整条目
  - `README.md` 加 dashboard 截图/描述
  - `INSTALL.md` 提到 dashboard 配置
  - E2E 手动测试 checklist
- 验收: 新人按 README 装, 5min 内能跑通 dashboard

### M7 (可选, 收尾清理)
- 删 v1.0 时加的 `_shots_v2/` 等残留
- 跑全量 122 测试
- review_ui 重启一次, 验无回归
- commit 1 个 v1.1 大版本

---

## 10. 风险 & 回滚

| 风险 | 概率 | 缓解 |
|---|---|---|
| Windows `taskkill /T` 杀不掉子进程 | 中 | M1 用 psutil (跨平台), fallback SIGTERM |
| SSE 在某些代理下被缓冲 | 低 | nginx 配置 `X-Accel-Buffering: no` (v1.2 部署时再说) |
| metrics.jsonl 无限增长 | 低 | config 配 retention_days=30, 启动时清理 |
| review_ui Flask dev server 多线程 | 高 | v1.1 单用户 + 锁 (1 本 1 进程), v1.2 切 waitress |
| 改 chapter.py 加 marker 破坏现有测试 | 中 | marker 是 print 输出, 不影响 return value, 现有 5 个 chapter test 应该不挂 |

**回滚**: 每个 M 独立 commit, 任意 M 出问题可 `git revert <commit>`. 不破坏 v1.0.

---

## 11. 验收标准 (v1.1 Definition of Done)

- [ ] 全量 122 测试过
- [ ] 浏览器打开 `http://127.0.0.1:21199/dashboard/测试书籍`, 看到进度条 + 0 token 用量 + 空日志
- [ ] 点 **"写下一章"** 按钮, 30s 内看到进度条进 writing 阶段
- [ ] 实时日志滚动, 跟 `tail -f pipeline.log` 内容一致
- [ ] 写完 1 章, 自动停在 done, 进度 5/20, token 数字更新
- [ ] 写章节过程中点 **"取消"**, 子进程被杀, state 标 cancelled
- [ ] `git log --oneline` 有 6 个 v1.1 commit (M1-M6)
- [ ] README 文档能引导新用户 5min 内看到 dashboard

---

## 12. 不在 v1.1 范围 (Roadmap 后置)

- ❌ 多书并发 (队列) → **v1.2**
- ❌ 移动 App 触发 → **v1.3**
- ❌ 多人协作 / 角色权限 → **v2.0**
- ❌ 流水线 DAG 可视化 (目前是固定 6 阶段) → **v1.2+**
- ❌ 成本预估 (按 model × token 算 $) → **v1.2** (需要 pricing data)

---

**v1.1 范围确认?** 接下来开 M1 (PipelineRunner 基础) 还是先细化某块?
