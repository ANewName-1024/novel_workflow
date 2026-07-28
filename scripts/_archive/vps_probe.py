"""VPS-side probe: test health endpoint same way curl/python would"""
import urllib.request, json
req = urllib.request.Request("http://127.0.0.1:9180/api/llm/health",
    data=json.dumps({"provider": "deepseek"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
r = urllib.request.urlopen(req, timeout=30)
print("status:", r.status)
print("body:", r.read().decode())
