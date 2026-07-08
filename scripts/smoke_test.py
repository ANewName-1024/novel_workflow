"""novel_workflow smoke test -- all core API endpoints"""
import urllib.request, json, sys, os

# Force UTF-8 for stdout to avoid GBK encoding issues on Windows
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Python 3.14

BASE = "http://127.0.0.1:21199"
passed = 0
failed = 0

def test(name, method, path, body=None, expect_ok=True):
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
        ok = r.status == 200
        icon = "OK" if (expect_ok and ok) else "FAIL"
        if (expect_ok and ok):
            passed += 1
        else:
            failed += 1
        print(f"  [{icon}] [{method}] {path} -> {r.status}")
        return resp
    except urllib.error.HTTPError as e:
        ok = not expect_ok
        icon = "OK" if ok else "FAIL"
        detail = e.read().decode("utf-8", errors="replace")[:120]
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{icon}] [{method}] {path} -> {e.code}: {detail}")
        return {"_status": e.code}
    except Exception as e:
        failed += 1
        print(f"  [FAIL] [{method}] {path} -> {type(e).__name__}: {e}")
        return {"_error": str(e)}

print("=" * 60)
print("novel_workflow smoke test")
print("=" * 60)

# --- 1. Page rendering ---
print("\n-- 1. Page rendering --")
test("home", "GET", "/")
test("overview", "GET", "/overview")
test("llm config page", "GET", "/llm")

# --- 2. LLM ---
print("\n-- 2. LLM Provider --")
providers = test("provider list", "GET", "/api/llm/providers")
if isinstance(providers, dict):
    dp = providers.get("default_provider", "?")
    pk = list(providers.get("providers", {}).keys())
    print(f"    default: {dp}  providers: {pk}")

test("deepseek health", "POST", "/api/llm/health",
     {"provider": "deepseek"}, expect_ok=True)

test("local health (expect fail)", "POST", "/api/llm/health",
     {"provider": "local"}, expect_ok=False)

# --- 3. Discover available routes ---
print("\n-- 3. Route discovery --")
routes_to_try = [
    "/api/stats", "/api/books", "/api/queue",
    "/dashboard", "/api/projects",
]
for r in routes_to_try:
    test(f"try {r}", "GET", r, expect_ok=False)

# --- 4. Try stats with a known book name ---
print("\n-- 4. Project data --")
# read books from dashboard route if it exists
test("dashboard", "GET", "/dashboard", expect_ok=False)

print()
print("=" * 60)
print(f"Result: PASS={passed}  FAIL={failed}")
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
