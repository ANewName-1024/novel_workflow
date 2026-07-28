"""Public novel service smoke test - all core API endpoints (URL-encoded)"""
import urllib.request, json, sys, urllib.parse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://8.137.116.121:9080"
passed = 0
failed = 0

def test(method, path, body=None, label=""):
    global passed, failed
    try:
        url = BASE + path
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data,
            headers={"Content-Type":"application/json"} if body else {},
            method=method)
        r = urllib.request.urlopen(req, timeout=30)
        is_json = (r.headers.get("Content-Type","") or "").startswith("application/json")
        raw = r.read().decode("utf-8", errors="replace")
        if is_json:
            try:
                resp = json.loads(raw)
            except json.JSONDecodeError:
                resp = raw
        else:
            resp = raw
        passed += 1
        if label:
            print(f"  [OK] [{method}] {label} -> {r.status} ({len(raw)} bytes)")
        else:
            print(f"  [OK] [{method}] {path} -> {r.status} ({len(raw)} bytes)")
        return resp
    except urllib.error.HTTPError as e:
        failed += 1
        detail = e.read().decode("utf-8", errors="replace")[:120]
        if label:
            print(f"  [FAIL] [{method}] {label} -> {e.code}: {detail}")
        else:
            print(f"  [FAIL] [{method}] {path} -> {e.code}: {detail}")
        return None
    except Exception as e:
        failed += 1
        print(f"  [FAIL] [{method}] {path} -> {type(e).__name__}: {e}")
        return None

print("=" * 60)
print(f"Public Novel Service Test: {BASE}")
print("=" * 60)

# 1. Pages
print("\n-- 1. Pages --")
test("GET", "/", label="home (root)")
test("GET", "/novel/", label="novel prefix")
test("GET", "/novel/overview", label="overview")
test("GET", "/novel/llm", label="llm config")

# 2. LLM
print("\n-- 2. LLM --")
test("GET", "/api/llm/providers", label="providers list")
test("POST", "/api/llm/health", {"provider": "deepseek"}, label="deepseek health")

# 3. Books
print("\n-- 3. Books (all 3, including Chinese name) --")
test("GET", "/api/projects", label="projects list")
test("GET", "/api/queue/test_book", label="queue test_book")
test("GET", "/api/queue/zzz", label="queue zzz")
test("GET", "/api/queue/" + urllib.parse.quote("测试书籍"), label="queue 测试书籍")
test("GET", "/api/stats/test_book", label="stats test_book")
test("GET", "/api/stats/" + urllib.parse.quote("测试书籍"), label="stats 测试书籍")

# 4. Chapter detail (5 chapters in 测试书籍)
print("\n-- 4. Chapter detail --")
for ch in ["ch_001", "ch_002", "ch_006", "ch_007", "ch_008"]:
    test("GET", f"/api/chapter/{urllib.parse.quote('测试书籍')}/{ch}", label=f"chapter {ch}")

# 5. Pipeline
print("\n-- 5. Pipeline --")
test("GET", "/api/pipeline/test_book/interruptions", label="interruptions test_book")
test("GET", "/api/pipeline/" + urllib.parse.quote("测试书籍") + "/interruptions", label="interruptions 测试书籍")

# 6. Health (local provider - should fail since llama not running)
print("\n-- 6. Local LLM (expected to fail) --")
test("POST", "/api/llm/health", {"provider": "local"}, label="local health (expected 502)")

print()
print("=" * 60)
print(f"Result: PASS={passed}  FAIL={failed}")
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
