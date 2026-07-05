#!/bin/bash
# Insert novel_api_proxy.conf snippet BEFORE `location ^~ /api/`
# So the novel regex wins over the auth-protected /api/ location
set -e
CONF=/etc/nginx/conf.d/rc-9180.conf
SNIPPET=/tmp/novel_api_proxy.conf
BACKUP="$CONF.bak-novelapi-$(date +%s)"

# Backup first
cp "$CONF" "$BACKUP"
echo "Backup: $BACKUP"

# Verify snippet file present
[ -f "$SNIPPET" ] || { echo "ERROR: snippet not found at $SNIPPET"; exit 1; }

python3 <<'PYEOF'
import re

CONF = '/etc/nginx/conf.d/rc-9180.conf'
SNIPPET = '/tmp/novel_api_proxy.conf'

with open(CONF, 'r') as f:
    content = f.read()

with open(SNIPPET, 'r') as f:
    snippet = f.read()

# Idempotency: if marker already inserted, bail
marker = '# === Novel Workflow API proxy (2026-07-03) ==='
if marker in content:
    print('Snippet already present, skipping insert')
    exit(0)

# Find the `location ^~ /api/` line and insert BEFORE it
target = re.search(r'^\s*location \^~ /api/', content, re.MULTILINE)
if not target:
    print('ERROR: could not find `location ^~ /api/`')
    exit(1)

insert_at = target.start()
new_content = content[:insert_at] + snippet + '\n' + content[insert_at:]

with open(CONF, 'w') as f:
    f.write(new_content)

print(f'Inserted {len(snippet)} chars at position {insert_at}')
PYEOF

echo ""
echo "=== nginx -t ==="
nginx -t

echo ""
echo "=== Inserted snippet (around marker) ==="
grep -nA 12 "$marker" "$CONF" | head -20