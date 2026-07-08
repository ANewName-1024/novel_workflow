"""VPS novel project list - check actual book names"""
import urllib.request, json
r = urllib.request.urlopen('http://8.137.116.121:9080/api/projects', timeout=10)
d = json.loads(r.read())
for name, info in d['projects'].items():
    print(f'  name={name!r}  total={info.get("total_chapters")}  pending={info.get("pending_reviews")}')
