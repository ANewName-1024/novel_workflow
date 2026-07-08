#!/bin/bash
for d in /root/novel_workflow/projects/*/; do
  echo "=== $d ==="
  ls "$d" 2>/dev/null
done
