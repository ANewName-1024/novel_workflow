# scripts/_archive/

**2026-07-28 归档**: 把历史调试/复现/烟囱测试脚本从 `scripts/` 移到本目录。

## 包含的文件

### 复现 / 调试 (`repro_*.py`, `dump_error.py`, `verify_*.py`)
一次性复现当时的 bug，已修。保留用于：
- 历史回溯 (某个 bug 当时怎么发现的)
- 模式参考 (类似 bug 复现脚本结构)

### 公网/VPS 烟囱测试 (`smoke_test.py`, `public_smoke_test*.py`, `quick_smoke.py`)
调远程 endpoint (`http://8.137.116.121:9080/...`) 的烟囱测试。已完成的 regression 测试。

### 审计/检查 (`audit_*.py`, `check_*.py`, `test_ai_suggest.py`)
一次性审计脚本，当时跑完就完成使命。

### VPS 一次性 (`vps_check_db.py`, `vps_deep_flatten.py`, `vps_extract.py`, `vps_fix_nest.py`, `vps_flatten.py`, `vps_inspect.py`, `vps_probe.py`, `vps_unflatten.py`)
2026-07-08 那次 VPS 数据修复时写的一次性脚本。

### 杂项 (`list_books.py`, `list_vps_projects.sh`)
列书/列项目的临时脚本。

## 保留还是删除?

**保留**: 历史价值 + 已 work 过的事实证据。占用不到 ~50KB。
**不删除**: 这些 commit 都还在 git 历史里 (`git log --all --diff-filter=A -- scripts/_archive/...` 可追)。

## 生产脚本还在 `scripts/` 根目录

- `_start_review_ui.py` — 开发启动器
- `install_backup_task.ps1` — Windows backup 计划任务安装
- `run_backup_all.ps1` — Windows backup 全部项目
- `vps/` — VPS 部署/运维脚本 (production)