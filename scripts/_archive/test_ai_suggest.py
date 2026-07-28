"""Trigger ai-suggest and extract error from HTML"""
import urllib.request, urllib.parse, json, re

book = urllib.parse.quote("测试书籍")
url = f"http://8.137.116.121:9080/api/outline/{book}/ai-suggest"
data = json.dumps({"count": 3, "strategy": "balanced"}).encode("utf-8")
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=120)
    print("OK status:", r.status)
    body = r.read().decode("utf-8", errors="replace")
    print(body[:800])
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    m = re.search(r'class="error-message">([^<]+)</p>', body)
    if m:
        print("ERROR-MSG:", m.group(1))
    m2 = re.search(r'class="error-detail">([^<]+)</pre>', body, re.DOTALL)
    if m2:
        print("ERROR-DETAIL:")
        print(m2.group(1)[:3000])
    else:
        # Try to find the error body in <pre> or just dump
        m3 = re.search(r'<pre[^>]*>([^<]+)</pre>', body, re.DOTALL)
        if m3:
            print("PRE:")
            print(m3.group(1)[:3000])
        else:
            print("FULL BODY (first 2000):")
            print(body[:2000])
