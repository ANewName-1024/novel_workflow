# novel_workflow v1.1 → v2.0 实施计划

> **作者**: 小爪 🐾
> **日期**: 2026-07-01
> **状态**: 方案评审中（4 决策点已确定推荐方案）
> **目标**: 在 v1.0.0 工程化基础上，按推荐技术栈（A/Vue3 + B/RQ + B/SQLite + 文件）推进精细化管理
> **总周期**: v1.1.2 (3 天) → v1.2 (2 周) → v2.0 (4 周)

---

## 0. 推荐方案确定 (4 决策点)

| # | 决策 | 选项 | **选定** | 理由 |
|---|---|---|---|---|
| 1 | 前端栈 | A) Vue 3 SPA / B) Flask+HTMX / C) Next.js | **A** | 生态成熟，组件库全，适合做精细化表单/图表/拖拽 |
| 2 | 任务队列 | A) Dramatiq / B) RQ / C) 自研 asyncio | **B** | Redis 单依赖，dashboard 集成简单，水平扩展够用 |
| 3 | 元数据存储 | A) 纯文件 / B) SQLite+文件 / C) Postgres | **B** | 单机零运维；JSONL 迁 SQLite 平滑；后续可换 Postgres |
| 4 | 协作范围 | A) 单人 / B) 多人只读 / C) 多人协作 | **A→B** | 先单人跑通精细化核心；多人只读后期叠加，避免一上来就 RBAC 复杂化 |

---

## 1. 路线图总览

```
v1.0.0  ✅ 完成 (工程化重构)
   ↓
v1.1.1  ✅ 完成 (Dashboard + Pipeline M1-M5, 41 新测试)
   ↓
v1.1.2  ⏳ 锁稳定 (3 天)  ← 现在位置
   ↓
v1.2    📅 精细化管理核心 (2 周)
   ↓
v2.0    📅 架构升级 (4 周)
```

---

## 2. v1.1.2 — 锁稳定 (3 天)

### 2.1 目标
- 把 v1.1.1 真正"跑稳"，修遗留问题，**不引入新功能**

### 2.2 子任务

| # | 任务 | 工期 | DoD |
|---|---|---|---|
| 2.2.1 | 修 v1.1.1 遗留：commit `review_ui/app.py` import 修复（`from .dashboard` → `from dashboard`） | 0.5h | 1 commit + pytest 全过 |
| 2.2.2 | ch_008 死因 deep dive + 修（已知现象：30s 后 pipeline.log 断流） | 1.5h | 真跑通 ch_008，metrics.jsonl 有真实数据 |
| 2.2.3 | L70-L73 lessons 落 `D:\self-improving\domains\async-subprocess.md` | 0.5h | 文件存在 + commit |
| 2.2.4 | dashboard 浏览器端到端验证（写 1 章 + 看实时 stage + 看 metrics 图） | 2h | 浏览器截图 + 7 阶段全部点亮 |
| 2.2.5 | `scripts/_start_review_ui.py` 修 docstring + commit | 0.5h | 1 commit |
| 2.2.6 | v1.1.2 release commit + CHANGELOG | 0.5h | git tag v1.1.2 |

### 2.3 测试要求
- 不增删测试，只跑通已有 135 测试
- dashboard 端到端 smoke test（手动）

### 2.4 风险
- **R1**: ch_008 死因可能根因更深（如 LLM 网络），预留 0.5 天 buffer

---

## 3. v1.2 — 精细化管理核心 (2 周)

### 3.1 目标
把"生成小说每个环节"从**章节级粗粒度**细化到**实体级 + 行级 + 阶段级**，全部走 dashboard 统一入口

### 3.2 子里程碑

#### M1 (Day 1-3) — 实体管理
**目标**: 角色/事件/伏笔的 CRUD + 关联图谱

| # | 任务 | DoD |
|---|---|---|
| 3.2.1 | `lib/entity.py` — Entity 模型 + CRUD 通用层 | 4 实体类型各 1 个测试 |
| 3.2.2 | API: `/api/entities/<book>/<type>` GET/POST/PUT/DELETE | 8 路由 + 8 测试 |
| 3.2.3 | 抽取层改造：extract.py 输出从自由文本 → 结构化 entity diff | 旧测试全过 + 5 新测试 |
| 3.2.4 | dashboard 实体页面：列表 + 编辑表单 + **关联图谱可视化**（D3.js 力导向图）| 浏览器 1 实体展示完整 |

#### M2 (Day 4-6) — 评审增强
**目标**: 多人协作雏形（先单人 + 评论流）

| # | 任务 | DoD |
|---|---|---|
| 3.2.5 | API: `/api/review/comments/<book>/<item>` POST/GET（单条 review 的讨论流）| 4 路由 + 6 测试 |
| 3.2.6 | 行级 diff 锚点：review item 关联章节行号 + 上下文 | diff 视图点击跳转到对应行 |
| 3.2.7 | @提醒 + 通知中心（页面头部铃铛 + 未读计数）| 提醒创建/已读 API + 1 测试 |
| 3.2.8 | 批量审批增强：选择 + 过滤（按严重度/阶段/标签）+ 撤销 | 3 测试 |

#### M3 (Day 7-9) — 章节版本
**目标**: 章节正文版本控制 + 一键回滚

| # | 任务 | DoD |
|---|---|---|
| 3.2.9 | `lib/version.py` — 轻量版本控制（基于 git init 或自研文件版本）| 选型 + 1 测试 |
| 3.2.10 | 每次章节保存自动 snapshot（含元数据: ts/触发原因/字数差）| 5 snapshot 后能 list |
| 3.2.11 | API: `/api/chapter/versions/<book>/<ch>` GET + `/api/chapter/revert/<book>/<ch>/<v>` POST | 6 测试 |
| 3.2.12 | dashboard 章节页加 "版本历史" tab + "回滚到 v3" 按钮 | 浏览器端到端 |

#### M4 (Day 10-12) — 大纲编辑器
**目标**: 大纲节点拖拽 + 拆分 + 重排 + 版本对比

| # | 任务 | DoD |
|---|---|---|
| 3.2.13 | `lib/outline_editor.py` — 节点树 CRUD + 重排算法 | 8 测试 |
| 3.2.14 | API: `/api/outline/<book>` GET/PUT + `/api/outline/<book>/diff/<v1>/<v2>` GET | 6 路由 + 6 测试 |
| 3.2.15 | dashboard 大纲页：树视图 + 拖拽 (Vue Draggable) + 节点编辑 | 浏览器拖拽一次 OK |

#### M5 (Day 13-14) — 状态机细化
**目标**: 7 阶段每阶段独立 checkpoint + 可手动跳过/重跑

| # | 任务 | DoD |
|---|---|---|
| 3.2.16 | `lib/pipeline_v2.py` — 阶段 FSM + checkpoint 持久化 | 7 阶段各 1 测试 |
| 3.2.17 | API: `/api/pipeline/skip/<book>/<stage>` POST + `/api/pipeline/rerun/<book>/<stage>` POST | 4 路由 + 4 测试 |
| 3.2.18 | dashboard pipeline 页加 "跳过当前阶段" / "重跑某阶段" 按钮 | 浏览器跳过 1 次 OK |
| 3.2.19 | v1.2 release commit + CHANGELOG | tag v1.2.0 |

### 3.3 测试要求
- **新增测试**: 目标 60-80 个（v1.1 41 → v1.2 ~110-130 总）
- **覆盖率**: lib/ ≥ 80%, scripts/ ≥ 50%
- **回归**: 旧 135 测试必须全过

### 3.4 风险
- **R1**: 大纲拖拽编辑器复杂度高，M4 预留 buffer（拖拽做不到可降级为"上下移动按钮"）
- **R2**: 状态机细化可能与现有 pipeline.py 冲突，需要 refactor（预估 1 天）

---

## 4. v2.0 — 架构升级 (4 周)

### 4.1 目标
架构全面升级，支持**精细化管理 + 多人协作 + 大规模**，为长期演进铺路

### 4.2 子里程碑

#### M1 (Week 1) — 前端栈迁移
- Vue 3 + Vite + Pinia + Vue Router + Element Plus
- 现有 dashboard.html 重写为 SPA（保留 Flask 提供的 REST API）
- 路由：/books, /book/:id, /book/:id/chapter/:n, /book/:id/entities, /book/:id/review, /book/:id/pipeline, /book/:id/outline, /settings

#### M2 (Week 2) — 任务队列升级
- RQ + Redis（持久化队列 + 多 worker + 失败重试）
- 单书 pipeline 互斥（Redis 锁）
- 多书并发（按书 hash 分桶）
- 任务优先级（评审/写作/抽取可设优先级）
- 任务取消支持 checkpoint 回滚

#### M3 (Week 3) — SQLite 迁移
- 建表：metrics / audit_log / review_comments / entities / notifications / users / sessions
- 迁移脚本：JSONL → SQLite
- 索引：按 book/ts/stage 组合索引
- 备份：sqlite3 .backup 命令（每日）

#### M4 (Week 4) — 多人协作 + RBAC
- 用户模型：username / password_hash / role (admin/editor/reviewer)
- RBAC 中间件：路由级权限检查
- 操作审计：所有写操作入 audit_log
- "只读分享"链接：图书可生成分享链接，无需账号即可查看
- 通知系统：@提醒 + 邮件/SSE 推送

### 4.3 测试要求
- **新增测试**: 目标 100+（v1.2 ~130 → v2.0 ~230+ 总）
- **E2E**: 至少 3 个 Playwright 场景（写一章/批一条 review/回滚一版本）

### 4.4 风险
- **R1**: Vue 3 迁移量大，组件复用率低（dashboard.html 重写而非迁移）
- **R2**: SQLite 单文件并发写有限制（启用 WAL 模式 + 写队列）
- **R3**: RBAC 复杂度可能爆炸，先做"管理员/普通用户"两角色

---

## 5. 优先级矩阵（用户决策辅助）

| 模块 | 业务价值 | 实现成本 | **优先级** | 版本 |
|---|---|---|---|---|
| ch_008 死因修 | 高 | 低 | **P0** | v1.1.2 |
| dashboard 端到端验证 | 高 | 低 | **P0** | v1.1.2 |
| 实体管理 | **高** | 中 | **P1** | v1.2 M1 |
| 章节版本 | **高** | 中 | **P1** | v1.2 M3 |
| 大纲编辑器 | 中 | 高 | **P2** | v1.2 M4 |
| 评审增强 | 中 | 中 | **P1** | v1.2 M2 |
| 状态机细化 | 中 | 中 | **P1** | v1.2 M5 |
| Vue 3 SPA | 中 | **高** | **P2** | v2.0 M1 |
| RQ 队列 | 中 | 中 | **P2** | v2.0 M2 |
| SQLite | 低 | 中 | **P3** | v2.0 M3 |
| RBAC | 中 | 高 | **P2** | v2.0 M4 |

---

## 6. 关键路径与依赖

```
v1.1.2 (锁稳)
   ↓ 依赖：现有 dashboard 已 commit
v1.2 M1 (实体管理)
   ↓ 依赖：M1 完成才能在评审中引用实体
v1.2 M2 (评审增强)
   ↓ 并行 M3 (版本) + M4 (大纲)
v1.2 M5 (状态机)
   ↓ 依赖：M3 完成才能可靠回滚
v2.0 M1 (Vue 3) ─┬─→ M2 (RQ) ─┬─→ M3 (SQLite) ─→ M4 (RBAC)
                │            │
                ↓            ↓
            (前端组件解耦)  (后端 API 解耦)
```

---

## 7. DoD 通用准则

每个里程碑必须满足：
1. **代码 review**: 所有 commit 可独立 revert
2. **测试通过**: pytest 全过 + 新增测试齐
3. **文档同步**: CHANGELOG + DESIGN 段更新
4. **端到端验证**: 浏览器/CLI 跑通核心场景
5. **回滚方案**: 数据/状态可回到上一版本

---

## 8. 不在本计划范围 (v2.1+ 候选)

- **多 LLM Provider 智能路由**（按任务类型选模型）
- **知识图谱可视化增强**（3D 视图、时间线回放）
- **自动伏笔回收建议**（AI 扫描埋设的伏笔 + 提示回收位置）
- **写作风格一致性检测**（与已写章节对比风格偏移）
- **多书统一世界观管理**（跨书角色/事件共享）
- **导出多格式**（EPUB/Markdown/PDF/微信公众号）
- **协作冲突解决**（多人同时编辑同一章节）

---

## 9. 今晚可启动事项（睡前拍板）

1. **现在 commit v1.1.2 第 1 项**（import 修复 + _start_review_ui.py 整理）→ 0.5h
2. **明天启动 ch_008 死因 deep dive** → 1.5h
3. **明天 dashboard 浏览器验证** → 2h

需要我直接执行 (1)，还是先到这明早再说？