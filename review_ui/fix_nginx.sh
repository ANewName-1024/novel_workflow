#!/bin/bash
# Fix /etc/nginx/conf.d/rc-9080.conf: insert novel locations INSIDE the server block
set -e
CONF=/etc/nginx/conf.d/rc-9080.conf
SNIPPET=/tmp/novel_nginx.conf
cp "$CONF" "$CONF.bak-novel-$(date +%s)"

# Find the LAST closing brace '}' in the file - this is the end of server{}
# All locations must be BEFORE this brace
# The snippet was already inserted AFTER the brace by the buggy script, so:
# 1) Remove everything after the closing '}' (current "tail" includes our snippet)
# 2) Re-insert BEFORE the closing '}'

# Step 1: truncate at first occurrence of the snippet comment after the brace
python3 <<'PYEOF'
with open('/etc/nginx/conf.d/rc-9080.conf', 'r') as f:
    content = f.read()
marker = '# === Novel Workflow Review UI (2026-07-01) ==='
if marker in content:
    # Cut at the marker
    idx = content.index(marker)
    content = content[:idx].rstrip() + '\n'
    with open('/etc/nginx/conf.d/rc-9080.conf', 'w') as f:
        f.write(content)
    print(f'Truncated at marker, kept {len(content)} chars')
else:
    print('Marker not found, no truncation needed')
PYEOF

# Step 2: insert snippet before the last '}'
python3 <<'PYEOF'
with open('/etc/nginx/conf.d/rc-9080.conf', 'r') as f:
    content = f.read()
with open('/tmp/novel_nginx.conf', 'r') as f:
    snippet = f.read()
# Last '}' is the end of server block
last = content.rfind('}')
if last == -1:
    print('ERROR: no closing brace found')
    exit(1)
new = content[:last] + snippet + '\n' + content[last:]
with open('/etc/nginx/conf.d/rc-9080.conf', 'w') as f:
    f.write(new)
print(f'Inserted {len(snippet)} chars before position {last}')
PYEOF

echo ===
nginx -t
echo ===
tail -32 "$CONF"