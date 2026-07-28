# -*- coding: utf-8 -*-
"""Quick webUI smoke test using requests-like check."""
import sys
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:21199"

def test(url, name, expect=200):
    try:
        r = urllib.request.urlopen(url, timeout=8)
        status = r.status
        size = len(r.read())
        ok = "✓" if status == expect else "✗"
        print(f"  {ok} {name}: {status} ({size}B)")
        return status == expect
    except urllib.error.HTTPError as e:
        print(f"  ✗ {name}: HTTP {e.code}")
        return False
    except Exception as e:
        print(f"  ✗ {name}: {type(e).__name__}: {e}")
        return False

# URL-encode Chinese paths
from urllib.parse import quote
book = quote("测试书籍")

print("=== 本地 webUI 全面冒烟测试 ===\n")
print("[1] 全局页面")
test(f"{BASE}/", "首页 /")
test(f"{BASE}/overview", "总览 /overview")
test(f"{BASE}/llm", "LLM 配置 /llm")

print("\n[2] 项目内页面")
test(f"{BASE}/project/{book}", f"项目详情 /project/{book}")
test(f"{BASE}/outline/{book}", f"大纲 /outline/{book}")
test(f"{BASE}/chapter/{book}/ch_001", f"章节 /chapter/{book}/ch_001")
test(f"{BASE}/chapter/{book}/ch_002", f"章节 /chapter/{book}/ch_002")
test(f"{BASE}/chapter/{book}/ch_003", f"章节 /chapter/{book}/ch_003")
test(f"{BASE}/entities/{book}", f"实体 /entities/{book}")
test(f"{BASE}/notifications/{book}", f"通知 /notifications/{book}")
test(f"{BASE}/dashboard/{book}", f"仪表盘 /dashboard/{book}")

print("\n[3] API")
test(f"{BASE}/api/projects", "API /api/projects")
test(f"{BASE}/health", "API /health")
test(f"{BASE}/api/config", "API /api/config")
test(f"{BASE}/api/overview", "API /api/overview")
test(f"{BASE}/api/llm/list", "API /api/llm/list")

print("\n[4] 旧路径(应该都 404 现在)")
test(f"{BASE}/novel/", "旧 /novel/ (期望 404)")
test(f"{BASE}/book/{book}", "旧 /book/<name>")