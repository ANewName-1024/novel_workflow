#!/bin/bash
# Remove the prior regex location (which is shadowed by ^~ /api/),
# then rewrite `location ^~ /api/` to proxy to novel review_ui 9180 (no auth).
set -e
CONF=/etc/nginx/conf.d/rc-9180.conf
BACKUP="$CONF.bak-fix-api-$(date +%s)"
cp "$CONF" "$BACKUP"
echo "Backup: $BACKUP"

python3 <<'PYEOF'
CONF = '/etc/nginx/conf.d/rc-9180.conf'

with open(CONF, 'r') as f:
    content = f.read()

# Step 1: Remove the dead regex block (between the two markers)
import re
dead_block = re.compile(
    r'    # === Novel Workflow API proxy \(2026-07-03\) ===\n'
    r'    # Forward novel.*?\n'
    r'.*?'                                          # comment
    r'    location ~ \^/api/\(projects.*?\n'         # open
    r'(?:.*?\n)*?'                                  # body
    r'    \}\n',                                    # close brace
    re.DOTALL
)
content_new, n = dead_block.subn('', content, count=1)
if n == 0:
    print('WARN: dead block not found via regex, trying manual removal')
    # Fallback: line-based removal
    lines = content.split('\n')
    out = []
    skip = False
    depth = 0
    for ln in lines:
        if not skip and 'Novel Workflow API proxy (2026-07-03)' in ln:
            skip = True
        if skip:
            if '{' in ln: depth += ln.count('{')
            if '}' in ln: depth -= ln.count('}')
            if depth <= 0 and '}' in ln:
                skip = False
            continue
        out.append(ln)
    content_new = '\n'.join(out)
print(f'Step 1: removed dead block')

# Step 2: Replace `location ^~ /api/` block (proxy to 18888 + auth) -> 9180 no auth
old_api_block = re.compile(
    r'    location \^~ /api/ \{\n'
    r'(        [^\n]*\n)+?'
    r'    \}\n',
    re.MULTILINE
)
m = old_api_block.search(content_new)
if not m:
    print('ERROR: could not find `location ^~ /api/`')
    raise SystemExit(1)

new_api_block = '''    # === Novel API: proxy /api/* to review_ui 9180 (no auth, since 18888 is dead) ===
    location ^~ /api/ {
        proxy_pass http://127.0.0.1:9180;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Prefix /novel-api;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
        proxy_buffering off;
    }
'''

content_new = content_new[:m.start()] + new_api_block + content_new[m.end():]
print(f'Step 2: replaced /api/ block')

with open(CONF, 'w') as f:
    f.write(content_new)
print('Saved')
PYEOF

echo ""
echo "=== nginx -t ==="
nginx -t
echo ""
echo "=== relevant lines ==="
grep -nE 'location \^~ /api/|location /api/|Novel API' "$CONF"