import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import urllib.request, re

r = urllib.request.urlopen("http://127.0.0.1:21199/", timeout=5)
body = r.read().decode("utf-8", errors="replace")
novel_links = re.findall(r'href="(/novel/[^"]*)"', body)
top_links = re.findall(r'href="(/[^"]*)"', body)
print(f"Found {len(novel_links)} /novel/ links")
for m in novel_links[:8]:
    print(f"  {m}")
print(f"--- top hrefs ({len(top_links)}) ---")
for m in top_links[:8]:
    print(f"  {m}")
print(f"--- /novel/ route test ---")
try:
    r2 = urllib.request.urlopen("http://127.0.0.1:21199/novel/", timeout=5)
    print(f"  /novel/  status={r2.status} bytes={len(r2.read())}")
except urllib.error.HTTPError as e:
    print(f"  /novel/  HTTP {e.code}")
try:
    r3 = urllib.request.urlopen("http://127.0.0.1:21199/", timeout=5)
    print(f"  /        status={r3.status} bytes={len(r3.read())}")
except urllib.error.HTTPError as e:
    print(f"  /        HTTP {e.code}")
