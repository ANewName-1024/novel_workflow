"""Audit all mobile endpoints vs mobile models."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import urllib.request, urllib.parse, json

BASE = "http://8.137.116.121:9080"
book = urllib.parse.quote("测试书籍")

endpoints = [
    ("/api/projects", "GET"),
    ("/api/stats/test_book", "GET"),
    (f"/api/stats/{book}", "GET"),
    (f"/api/queue/{book}", "GET"),
    (f"/api/chapters/{book}", "GET"),
    (f"/api/llm/providers", "GET"),
    (f"/api/notifications/{book}", "GET"),
    (f"/api/dashboard/{book}", "GET"),
]

for path, method in endpoints:
    try:
        req = urllib.request.Request(f"{BASE}{path}", method=method)
        r = urllib.request.urlopen(req, timeout=10)
        ct = r.headers.get("content-type", "")
        raw = r.read()
        print(f"\n{method} {path}")
        print(f"  status={r.status} ct={ct[:50]} bytes={len(raw)}")
        if "json" in ct:
            try:
                d = json.loads(raw)
                if isinstance(d, dict):
                    print(f"  dict keys: {list(d.keys())[:10]}")
                elif isinstance(d, list):
                    if d:
                        print(f"  list[{len(d)}] sample type={type(d[0]).__name__} sample={str(d[0])[:150]}")
                    else:
                        print(f"  empty list")
            except Exception as e:
                print(f"  JSON parse fail: {e}")
                print(f"  raw[:200]: {raw[:200].decode('utf-8', errors='replace')}")
        else:
            print(f"  raw[:200]: {raw[:200].decode('utf-8', errors='replace')}")
    except urllib.error.HTTPError as e:
        print(f"\n{method} {path} -> HTTP {e.code}")
        print(f"  body: {e.read()[:200].decode('utf-8', errors='replace')}")