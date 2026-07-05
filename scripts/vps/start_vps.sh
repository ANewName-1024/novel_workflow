#!/bin/bash
# review_ui 启动脚本 (2026-07-03 修复 .env 注入)
cd /root/novel_workflow
# Inject .env vars into environment (so DEEPSEEK_API_KEY etc. are visible to os.environ)
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi
export PATH=$PATH:/root/.local/share/uv/python/cpython-3.11.15-linux-x86_64-gnu/bin
exec python3.11 -m review_ui.app --port 9180 --host 127.0.0.1