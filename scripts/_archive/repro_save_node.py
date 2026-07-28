"""Reproduce mobile 'save to node' failure - same payload as mobile app sends"""
import urllib.request, urllib.parse, json

book = urllib.parse.quote("测试书籍")
url = f"http://8.137.116.121:9080/api/outline/{book}/node"

# This is the exact body the mobile _addNode() sends
mobile_body = {
    "title": "测试章节",
    "summary": "AI 生成的一章",
    "key_events": ["事件1", "事件2"],
    "foreshadow": "伏笔",
    "pov_notes": "POV 视角",
}
data = json.dumps(mobile_body).encode("utf-8")
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=30)
    print(f"OK: {r.status}")
    print(r.read().decode()[:500])
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}")
