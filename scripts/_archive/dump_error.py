"""Extract 500 error details and dump to file"""
import urllib.request, urllib.parse, json, re, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

book = urllib.parse.quote("测试书籍")
url = f"http://8.137.116.121:9080/api/outline/{book}/ai-suggest"
data = json.dumps({"count": 3, "strategy": "balanced"}).encode("utf-8")
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=120)
    print("OK status:", r.status)
    print(r.read().decode("utf-8")[:500])
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    with open("D:\\.openclaw\\workspace\\downloads\\error_ai_suggest.html", "w", encoding="utf-8") as f:
        f.write(body)
    print(f"HTTP {e.code}, body saved ({len(body)} bytes)")

    # Extract just the text parts
    for m in re.finditer(r'class="error-(\w+)">([^<]+)<', body):
        print(f"  {m.group(1)}: {m.group(2)}")
