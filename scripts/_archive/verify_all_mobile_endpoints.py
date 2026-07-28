"""Verify every mobile endpoint returns expected shape."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import urllib.request, urllib.parse, json

BASE = "http://8.137.116.121:9080"
book = urllib.parse.quote("测试书籍")

def get(path):
    try:
        r = urllib.request.urlopen(f"{BASE}{path}", timeout=10)
        ct = r.headers.get("content-type", "")
        raw = r.read()
        return r.status, ct, raw
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), e.read()

def post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, r.headers.get("content-type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), e.read()

cases = [
    ("GET", "/api/projects", None, "Map<String, Book> from data['projects']"),
    ("GET", f"/api/history/{book}", None, "Map with chapters[] (mobile reads data['chapters'])"),
    ("GET", f"/api/diff/{book}/ch_001", None, "Map with diff[] (ChapterDiff.fromJson)"),
    ("GET", f"/api/review/{book}/ch_001", None, "Map (review items)"),
    ("POST", f"/api/approve/{book}/ch_001", {}, "ignored return"),
    ("POST", f"/api/reject/{book}/ch_001", {}, "ignored return"),
    ("GET", f"/api/stats/{book}", None, "Map with total/approved/etc"),
    ("GET", f"/api/outline/{book}", None, "Map with chapters[], volumes[]"),
    ("POST", f"/api/outline/{book}/ai-suggest", {"count": 1, "next_num": 21}, "Map with chapters[]"),
    ("POST", f"/api/outline/{book}/ai-expand", {"title": "t", "summary": "s"}, "Map with key_events/foreshadow"),
    ("POST", f"/api/outline/{book}/node", {"title": "t"}, "ignored"),
    ("GET", f"/api/queue/{book}", None, "List<dict>"),
    ("GET", f"/api/llm/providers", None, "Map with providers[]"),
    ("POST", f"/api/llm/health", {"provider": "deepseek"}, "Map with ok"),
    ("GET", f"/api/config/{book}", None, "Map (book config)"),
]

for method, path, body, expected in cases:
    if method == "GET":
        status, ct, raw = get(path)
    else:
        status, ct, raw = post(path, body)
    is_json = "json" in ct
    print(f"\n{method} {path}")
    print(f"  status={status} ct={ct[:50]} json={is_json} bytes={len(raw)}")
    if is_json:
        try:
            d = json.loads(raw)
            t = type(d).__name__
            if isinstance(d, dict):
                print(f"  dict keys: {list(d.keys())[:10]}")
            elif isinstance(d, list):
                if d:
                    sample = d[0]
                    print(f"  list[{len(d)}] sample type={type(sample).__name__}")
                else:
                    print(f"  empty list")
        except Exception as e:
            print(f"  ❌ JSON parse failed: {e}")
            print(f"  raw[:200]: {raw[:200].decode('utf-8', errors='replace')}")
    else:
        print(f"  ❌ NOT JSON! raw[:100]: {raw[:100].decode('utf-8', errors='replace')!r}")
    print(f"  mobile expects: {expected}")