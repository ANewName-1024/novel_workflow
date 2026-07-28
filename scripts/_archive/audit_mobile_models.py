"""Probe each mobile-relevant endpoint and compare shape to mobile model."""
import sys, io
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import urllib.request, urllib.parse, json

BASE = "http://8.137.116.121:9080"
book = urllib.parse.quote("测试书籍")
issues = []

def fetch(path):
    url = f"{BASE}{path}"
    try:
        r = urllib.request.urlopen(url, timeout=15)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")[:300]

def show(label, path):
    print(f"\n=== {label} ===")
    code, body = fetch(path)
    print(f"  HTTP {code}")
    if isinstance(body, dict):
        print(f"  keys: {list(body.keys())}")
        for k, v in body.items():
            t = type(v).__name__
            if isinstance(v, list) and v:
                print(f"    {k}: {t}[{len(v)}] sample[0] type={type(v[0]).__name__} sample={str(v[0])[:100]}")
            elif isinstance(v, dict):
                print(f"    {k}: {t} keys={list(v.keys())}")
            else:
                print(f"    {k}: {t}={str(v)[:80]}")
    elif isinstance(body, list):
        print(f"  list[{len(body)}] sample={str(body[0])[:200] if body else 'empty'}")
    return code, body

# 1. Projects list - Book model expects display_name, total_chapters etc
print("\n========= 1. /api/projects =========")
code, body = show("projects list", "/api/projects")
if isinstance(body, list):
    if body and isinstance(body[0], str):
        print(f"  ⚠️  WARNING: returns list of strings (book names only)")
        print(f"  Book.fromJson needs map keys like display_name/total_chapters")
        issues.append("projects list returns strings, mobile expects rich object")
    elif body and isinstance(body[0], dict):
        sample = body[0]
        print(f"  book keys: {list(sample.keys())}")
        # Check what mobile expects:
        for key in ("display_name", "total_chapters", "pending_reviews", "approved", "rejected", "last_pipeline_status"):
            present = key in sample
            print(f"    {key}: {'✓' if present else '✗ MISSING'}")

# 2. Outline detail
print("\n========= 2. /api/outline/<book> =========")
code, body = show("outline detail", f"/api/outline/{book}")
if isinstance(body, dict):
    vols = body.get("volumes", [])
    if vols:
        v = vols[0]
        chs = v.get("chapters", [])
        if chs:
            sample = chs[0]
            print(f"  volumes[0].chapters[0] type: {type(sample).__name__}")
            if not isinstance(sample, dict):
                print(f"  ⚠️  CONFIRMED: volumes[].chapters is list of {type(sample).__name__} NOT dict")
                issues.append("volumes[].chapters is labels not objects")
            else:
                print(f"  ✓ volumes[].chapters is dict")

# 3. Chapter detail
print("\n========= 3. /api/chapter/<book>/ch_001 =========")
code, body = show("chapter detail", f"/api/chapter/{book}/ch_001")
if isinstance(body, dict):
    for key in ("id", "ch", "title", "content", "status", "review_status", "updated_at"):
        present = key in body
        print(f"    {key}: {'✓' if present else '✗ MISSING'}")

# 4. Stats
print("\n========= 4. /api/stats/<book> =========")
code, body = show("stats", f"/api/stats/{book}")
if isinstance(body, dict):
    for key in ("total", "approved", "auto_passed", "false_positive", "human_edited", "needs_rewrite", "pending_review"):
        present = key in body
        print(f"    {key}: {'✓' if present else '✗ MISSING'}")

# 5. Queue (review)
print("\n========= 5. /api/queue/<book> =========")
code, body = show("review queue", f"/api/queue/{book}")
if isinstance(body, dict):
    for k, v in body.items():
        t = type(v).__name__
        if isinstance(v, list) and v:
            print(f"    {k}: {t}[{len(v)}] sample[0] type={type(v[0]).__name__}")
        else:
            print(f"    {k}: {t}={str(v)[:80]}")
elif isinstance(body, list):
    print(f"  list[{len(body)}] sample={str(body[0])[:200] if body else 'empty'}")

print("\n========= ISSUES FOUND =========")
for i in issues:
    print(f"  ⚠️  {i}")
if not issues:
    print("  ✅ No type-shape issues detected")