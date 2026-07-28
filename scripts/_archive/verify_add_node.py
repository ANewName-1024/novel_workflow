"""Verify fix: addOutlineNode with/without parent_vol"""
import urllib.request, urllib.parse, json

book = urllib.parse.quote("测试书籍")
url = f"http://8.137.116.121:9080/api/outline/{book}/node"

# Test 1: mobile-style (no parent_vol)
print("=== Test 1: mobile style (no parent_vol) ===")
mobile_body = {
    "title": "测试章节_mobile",
    "summary": "AI 生成的一章",
    "key_events": ["事件1", "事件2"],
    "foreshadow": "伏笔",
    "pov_notes": "POV 视角",
}
data = json.dumps(mobile_body).encode("utf-8")
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=30)
    d = json.loads(r.read())
    print(f"  OK: {r.status}")
    print(f"  parent_vol (auto): {d.get('parent_vol')}")
    print(f"  node id: {d['node'].get('id')}, title: {d['node'].get('title')}")
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code}: {e.read().decode()[:300]}")

# Test 2: explicit parent_vol
print("\n=== Test 2: explicit parent_vol ===")
data2 = json.dumps({**mobile_body, "parent_vol": "vol_1", "title": "测试章节_explicit"}).encode("utf-8")
req2 = urllib.request.Request(url, data=data2, headers={"Content-Type": "application/json"}, method="POST")
try:
    r = urllib.request.urlopen(req2, timeout=30)
    d = json.loads(r.read())
    print(f"  OK: {r.status}, parent_vol: {d.get('parent_vol')}, id: {d['node'].get('id')}")
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code}: {e.read().decode()[:300]}")
