"""scripts/_start_review_ui.py - 启动 review_ui (Windows 后台, 重定向日志)."""
import subprocess, sys, os
from pathlib import Path

log_path = Path("logs/review_ui.out.log")
log_path.parent.mkdir(exist_ok=True)
log_f = open(log_path, "ab")

# 用 DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP 让它真正后台
# 不依赖 shell, 不被当前 session kill
import subprocess
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

p = subprocess.Popen(
    [sys.executable, "novel.py", "serve"],
    cwd=str(Path.cwd()),
    stdout=log_f,
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    close_fds=True,
)
print(f"Started PID {p.pid}")
print(f"Log: {log_path.resolve()}")