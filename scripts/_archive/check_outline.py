import urllib.request, urllib.parse, json
book = urllib.parse.quote("测试书籍")
url = f"http://8.137.116.121:9080/api/outline/{book}"
try:
    r = urllib.request.urlopen(url, timeout=30)
    d = json.loads(r.read())
    print(f"GET outline: {r.status}")
    vols = d.get("volumes", [])
    chs = d.get("chapters", [])
    print(f"volumes: {len(vols)}, chapters: {len(chs)}")
    for v in vols:
        print(f"  vol: {v.get('id')} title={v.get('title')} chapters={len(v.get('chapters', []))}")
    print(f"\nlast 3 chapters:")
    for ch in chs[-3:]:
        print(f"  id={ch.get('id')} title={ch.get('title','')[:30]} vol={ch.get('vol')}")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()[:500]}")