"""公网小说服务冒烟测试 - 验证 nginx 9080 -> review_ui 9180 全链路"""
import urllib.request, json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://8.137.116.121:9080"
passed = 0
failed = 0

def test(method, path, body=None):
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
        resp = json.loads(raw) if is_json else raw
        passed += 1
        print(f"  [OK] [{method}] {path} -> {r.status} ({len(raw)} bytes)")
        return resp
    except urllib.error.HTTPError as e:
        failed += 1
        print(f"  [FAIL] [{method}] {path} -> {e.code}: {e.read().decode()[:120]}")
        return None
    except Exception as e:
        failed += 1
        print(f"  [FAIL] [{method}] {path} -> {type(e).__name__}: {e}")
        return None

print("=" * 60)
print(f"Public Novel Service Test: {BASE}")
print("=" * 60)

# 1. 主页 (root)
print("\n-- 1. Pages --")
test("GET", "/")
test("GET", "/novel/")
test("GET", "/novel/overview")
test("GET", "/novel/llm")
test("GET", "/novel/dashboard")

# 2. API 路径
print("\n-- 2. API: /api/ (裸) --")
test("GET", "/api/projects")
test("GET", "/api/llm/providers")

print("\n-- 3. API: /novel-api/ (前缀) --")
test("GET", "/novel-api/projects")
test("GET", "/novel-api/llm/providers")

# 3. LLM health via public
print("\n-- 4. LLM Health (DeepSeek) --")
test("POST", "/novel-api/llm/health", {"provider": "deepseek"})

# 4. 章节级
print("\n-- 5. Chapter / Queue --")
test("GET", "/novel-api/queue/test_book")
test("GET", "/api/queue/test_book")
test("GET", "/api/queue/测试书籍")
test("GET", "/api/stats/测试书籍")

# 6. Pipeline
print("\n-- 6. Pipeline --")
test("GET", "/api/pipeline/test_book/interruptions")
test("GET", "/api/pipeline/测试书籍/interruptions")

print()
print("=" * 60)
print(f"Result: PASS={passed}  FAIL={failed}")
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
