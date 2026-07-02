# DESIGN v1.3 — 全项目进度大屏

## 目标
单页总览所有书籍的流水线状态，无需逐本打开 dashboard。

## 数据模型

### storage.list_projects()
- 扫描 `data/` 下所有子目录
- 返回 `[{name, title, current_chapter, pipeline_status, last_run}]`

### pipeline.get_overview_state(book)
- 读取 `data/{book}/pipeline_state.json`
- 返回 `None`（从未跑过）| `{"status": "idle"|"running"|"done"|"failed"|"cancelled", "ch": N, "stage": str, "started_at": ts, "pid": int?}`

## API

### GET /api/overview
返回所有书籍状态列表（轻量，不需要 Basic Auth）：
```json
{
  "projects": [
    {
      "name": "book-a",
      "title": "《书名》",
      "current_chapter": 5,
      "pipeline": null | {
        "status": "running",
        "ch": 5,
        "stage": "rewrite",
        "started_at": 1751466000,
        "pid": 1234
      },
      "last_run": 1751466000 | null
    }
  ]
}
```

### GET /api/overview/stream
SSE 实时推送所有书籍状态变化（book + ch + stage + status）。

## 页面: overview.html

### 布局
- 顶部导航栏：「← 首页 | 全项目总览 | [book1] [book2] ...」
- 主体：响应式卡片网格（auto-fill, min 280px）
- 每张卡片：书名 / 当前章节 / 流水线状态徽章 / 最近运行时间 / 快速入口按钮

### 卡片状态配色
| status | 徽章色 |
|---|---|
| idle / null | 灰 |
| running | 蓝 |
| done | 绿 |
| failed | 红 |
| cancelled | 黄 |

### 交互
- 点击卡片 → 跳转 `/dashboard/{book}`
- SSE 实时刷新状态（每 3s polling `/api/overview/stream`）

## 测试
- `test_overview_api.py`: GET /api/overview (无项目 / 1 本书 / 多本书 / pipeline 状态)
- `test_overview_page.py`: 页面渲染 + 卡片数量 + SSE 连接

## 文件变更
- `lib/storage.py`: +`list_projects()`
- `review_ui/dashboard.py`: +2 routes (GET overview, SSE stream)
- `review_ui/templates/overview.html`: 新建
- `review_ui/app.py`: 注册 `/overview` 路由
- `tests/test_overview_api.py`: 新建
- `tests/test_overview_page.py`: 新建
- `CHANGELOG.md`: +v1.3 M1 条目
