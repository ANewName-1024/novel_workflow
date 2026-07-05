# VPS 部署脚本

> novel_workflow 在 VPS (8.137.116.121) 上的 nginx 反代 + 服务管理脚本

## 文件清单

| 文件 | 用途 |
|---|---|
| `start_vps.sh` | review_ui 启动脚本（注入 .env → `python3.11 -m review_ui.app --port 9180`） |
| `novel_api_proxy.conf` | nginx 配置片段：`/api/*` 转发到 review_ui:9180（无 auth，因 18888 后端已弃用） |
| `apply_novel_api_proxy.sh` | 把 `novel_api_proxy.conf` 插入 `/etc/nginx/conf.d/rc-9180.conf` 的 `location ^~ /api/` 之前（idempotent） |
| `fix_api_to_9180.sh` | 当 `^~ /api/` 仍指向 18888 时：移除旧的正则 block + 把 `^~ /api/` 改写到 9180 |

## 部署顺序

```bash
# 1. 拉代码
cd /root/novel_workflow && git pull

# 2. (首次/反代修复) 应用 nginx 配置
sudo cp scripts/vps/novel_api_proxy.conf /tmp/
sudo bash scripts/vps/apply_novel_api_proxy.sh
# 或修复旧的 ^~ /api/ block:
sudo bash scripts/vps/fix_api_to_9180.sh

# 3. 重启 nginx
sudo nginx -t && sudo systemctl reload nginx

# 4. 启动 review_ui
sudo bash scripts/vps/start_vps.sh
# （建议配合 systemd unit 或 supervisor）
```

## 端口分配（VPS 8.137.116.121）

| 端口 | 用途 | 反代到 |
|---|---|---|
| 9080 | nginx 入口（公网） | 18888 (旧后端) + 9180 (novel review_ui) + 9181 (ops-panel) |
| 9180 | novel review_ui（no auth） | — |
| 9181 | ops-panel | — |

## 反代 URL 前缀

nginx 把 novel app 挂在 `/novel/` 路径下，所以模板里所有 `url_for(...)` 改成直接路径 `/novel/...` 形式（避免 SCRIPT_NAME 处理复杂）。详见 `review_ui/templates/_navbar.html`。