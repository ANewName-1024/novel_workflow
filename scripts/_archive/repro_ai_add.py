"""Reproduce mobile '加入' button — exact body from AI suggestion"""
import urllib.request, urllib.parse, json

book = urllib.parse.quote("测试书籍")
url = f"http://8.137.116.121:9080/api/outline/{book}/node"

# This is what ai-suggest returns per chapter (from prior test):
# {"title": "意外的转机", "summary": "...", "key_events": [...], "foreshadow": "...", "pov": "李明"}
# mobile's ai-suggest is the same endpoint, so suggestion dict looks like:
suggestion = {
    "num": 9,
    "title": "意外的转机",
    "summary": "一位神秘老主顾提出投资书店改造计划，李明面临抉择。",
    "key_events": [
        "一位气质不凡的老主顾在书店逗留良久，对李明提起书店改造的设想",
        "老主顾坦言愿意提供资金支持，但要求书店必须保留原汁原味的社区氛围",
        "李明在激动与犹豫中，想起之前因盲目扩张而失败的教训",
    ],
    "foreshadow": "这位老主顾似乎与李明父亲有旧，暗示更深层的联系",
    "pov": "李明",
}
print(f"=== Test: mobile '加入' (sends AI suggestion as-is) ===")
data = json.dumps(suggestion).encode("utf-8")
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=30)
    d = json.loads(r.read())
    print(f"  OK: {r.status}")
    print(f"  parent_vol: {d.get('parent_vol')}")
    print(f"  node id: {d['node'].get('id')}, title: {d['node'].get('title')}")
    # cleanup
    ch_id = d['node']['id']
    del_url = f"http://8.137.116.121:9080/api/outline/{book}/node/{ch_id}"
    req2 = urllib.request.Request(del_url, method="DELETE")
    r2 = urllib.request.urlopen(req2, timeout=10)
    print(f"  cleaned up: DELETE {ch_id} -> {r2.status}")
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}")
