# 安装指南 (INSTALL.md)

> Windows 11 / Ubuntu 22.04+ 验证通过

## 1. 系统要求

| 项 | 最低 | 推荐 |
|---|---|---|
| OS | Windows 10 21H2 / Ubuntu 20.04 | Windows 11 24H2 / Ubuntu 24.04 |
| Python | 3.10 | 3.12+ |
| 内存 | 8 GB | 32 GB (跑 35B 模型) |
| 显存 | 6 GB (9B) | 12 GB+ (35B) |
| 磁盘 | 10 GB (源码 + 测试项目) | 200 GB (含多个项目 + 备份) |
| Git | 2.30+ | 最新 |

## 2. Python 环境

### 2.1 创建 venv (推荐)

```bash
# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
```

### 2.2 装依赖

```bash
pip install --upgrade pip
pip install -r requirements.txt        # 运行时
pip install -r requirements-dev.txt    # 开发 + 测试
```

### 2.3 验证

```bash
python -c "import openai, flask, tiktoken, pytest; print(\"deps OK\")"
```

## 3. 本地 LLM (llama-server)

### 3.1 下载模型

支持任何 OpenAI-compat 模型, 验证过的:

| 模型 | 大小 | 显存 | 适用场景 |
|---|---|---|---|
| **Qwythos-9B-Claude-Mythos-5** (Q8_0) | ~9.8 GB | 8 GB+ | 日常写作首选, 速度快 |
| Qwen3.6-35B-A3B-UD-Q4_K_M | ~22 GB | 14 GB+ (混合) | 长 context / 复杂推理 |

下载 (HuggingFace):
```bash
# 例: Qwythos-9B
huggingface-cli download empero-ai/Qwythos-9B-Claude-Mythos-5-1M-GGUF \
    Qwythos-9B-Claude-Mythos-5-1M-MTP-Q8_0.gguf \
    --local-dir D:\download

# 可选: 多模态 mmproj
huggingface-cli download empero-ai/Qwythos-9B-Claude-Mythos-5-1M-GGUF \
    mmproj-Qwythos-9B-Claude-Mythos-5-1M-F16.gguf \
    --local-dir D:\download
```

### 3.2 安装 llama.cpp

下载预编译包 (Windows x64 CUDA 13.1):
- 仓库: <https://github.com/ggerganov/llama.cpp/releases>
- 文件: `llama-b9196-bin-win-cuda-13.1-x64.zip`
- 解压到 `D:\application\llama-b9196-bin-win-cuda-13.1-x64\`

### 3.3 启动 server

`D:\application\llama-b9196-bin-win-cuda-13.1-x64\start_qwythos.bat`:

```bat
@echo off
chcp 65001 > nul
llama-server.exe ^
 -m D:\download\Qwythos-9B-Claude-Mythos-5-1M-MTP-Q8_0.gguf ^
 --mmproj D:\download\mmproj-Qwythos-9B-Claude-Mythos-5-1M-F16.gguf ^
 -ngl 99 --flash-attn on --jinja ^
 -c 65536 --reasoning off ^
 -t 14 -tb 14 -b 1024 -ub 256 ^
 --cache-type-k q8_0 --cache-type-v q4_0 ^
 --cont-batching --parallel 2 ^
 --mlock --host 127.0.0.1 --port 60443
```

启动: 双击 `start_qwythos.bat` 或 `Start-Process .\start_qwythos.bat`

验证:
```bash
curl http://127.0.0.1:60443/health
# → {"status":"ok"}
curl http://127.0.0.1:60443/v1/models
# → 列出已加载的 model id
```

### 3.4 关键参数说明

| 参数 | 值 | 说明 |
|---|---|---|
| `-ngl` | 99 | 99 层全 GPU, dense 模型必加 |
| `-c` | 65536 | context window (9B 安全值, 35B 可调 131072) |
| `--reasoning` | off | Qwen3 系列必加, 不然 40 token 用光 |
| `--mmproj` | 路径 | 多模态投影, 视觉任务必加 |
| `--mlock` | (无值) | 锁内存防 swap, 跑长 context 推荐 |
| `-b / -ub` | 1024 / 256 | batch size, RTX 5070 12GB 最佳 |
| `--parallel` | 2 | 并发槽 (批审/批章节) |

## 4. novel_workflow 部署

### 4.1 clone 仓库

```bash
git clone https://github.com/ANewName-1024/novel_workflow.git
cd novel_workflow
```

### 4.2 复制配置

```bash
cp config.yaml.example config.yaml
cp .env.example .env

# 改 .env (生产必改 SECRET_KEY + LLM URL)
notepad .env  # Windows
nano .env     # Linux
```

`.env` 内容:
```
LLM_API_BASE=http://127.0.0.1:60443/v1
FLASK_SECRET_KEY=<random 32+ chars>
REVIEW_UI_USER=weichao
REVIEW_UI_PASSWORD=<your-strong-password>
```

### 4.3 跑 doctor

```bash
python novel.py doctor
```

期望: **7✅ 1⚠ 0❌** (1 个 ⚠ 是 backup retention 可关)

### 4.4 初始化测试项目

```bash
python novel.py init demo --main-plot "测试主 plot"
python novel.py status demo
python novel.py backup demo    # 测试备份
```

## 5. Web UI 公网访问 (VPS 反代)

> 反向 SSH 隧道 + nginx, 不打洞

### 5.1 本地启 Web UI

```bash
python novel.py serve demo    # 监听 127.0.0.1:21199
```

### 5.2 建 SSH 隧道 (PowerShell)

`scripts/ssh_tunnel.ps1`:
```powershell
$sshKey = "$env:USERPROFILE\.ssh\id_rsa_vps"
ssh -p 2222 -i $sshKey -N -R 0.0.0.0:9080:127.0.0.1:21199 root@8.137.116.121
```

### 5.3 VPS 端 nginx

`/etc/nginx/sites-available/novel.conf`:
```nginx
server {
    listen 9080;
    server_name _;

    location /novel/ {
        proxy_pass http://127.0.0.1:21199/novel/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

启用: `ln -s /etc/nginx/sites-available/novel.conf /etc/nginx/sites-enabled/ && nginx -t && systemctl reload nginx`

## 6. 自动备份 (Windows Task Scheduler)

```powershell
# 注册每日 03:00 跑 backup --clean
.\scripts\install_backup_task.ps1
```

查看: `Get-ScheduledTask -TaskName "NovelWorkflowBackup"`

卸载: `Unregister-ScheduledTask -TaskName "NovelWorkflowBackup" -Confirm:$false`

## 7. 故障排查

### 7.1 LLM 调不通

```bash
# 1. health check
curl http://127.0.0.1:60443/health

# 2. doctor 详查
python novel.py doctor

# 3. 手动 curl
curl -X POST http://127.0.0.1:60443/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"Qwythos-9B\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
```

### 7.2 Web UI 启动失败

```bash
# 1. 端口占用
netstat -ano | findstr :21199
# 2. 杀掉占用进程
taskkill /F /PID <pid>
# 3. 重启
python novel.py serve demo
```

### 7.3 测试过不了

```bash
# 1. 看具体错误
python -m pytest tests/test_X.py -v

# 2. 重置临时数据
python -m pytest tests/ -q --cache-clear

# 3. 验证 deps
pip install -r requirements-dev.txt --upgrade
```

## 8. 升级

```bash
git pull
pip install -r requirements.txt --upgrade
python -m pytest tests/ -q   # 验证
```

零破坏升级: 老 `projects/<书>/` 目录直接跑新版, 不迁移。