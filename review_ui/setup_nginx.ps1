[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Create the nginx config snippet for /novel/ → reverse-tunneled Flask
$nginxConf = @'
# Novel Workflow Review UI (reverse-tunneled from WEI3216)
# See: D:\.openclaw\workspace\novel_workflow\review_ui\
location ^~ /novel/ {
    proxy_pass http://127.0.0.1:9081/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /novel;
    proxy_read_timeout 60s;
    proxy_send_timeout 60s;
    proxy_buffering off;
}

# Also expose API at /novel-api/ for direct programmatic access
location ^~ /novel-api/ {
    rewrite ^/novel-api/(.*)$ /api/$1 break;
    proxy_pass http://127.0.0.1:9081;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Prefix /novel-api;
    proxy_read_timeout 60s;
    proxy_send_timeout 60s;
    proxy_buffering off;
}
'@

Write-Host "=== Adding nginx location ==="
ssh -p 2222 -i $env:USERPROFILE\.ssh\id_rsa_vps root@8.137.116.121 "cat >> /etc/nginx/conf.d/rc-9080.conf <<'NGINX_EOF'

# === Novel Workflow Review UI (2026-07-01) ===
location ^~ /novel/ {
    proxy_pass http://127.0.0.1:9081/;
    proxy_http_version 1.1;
    proxy_set_header Host `$host;
    proxy_set_header X-Real-IP `$remote_addr;
    proxy_set_header X-Forwarded-For `$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto `$scheme;
    proxy_set_header X-Forwarded-Prefix /novel;
    proxy_read_timeout 60s;
    proxy_send_timeout 60s;
    proxy_buffering off;
}

location ^~ /novel-api/ {
    rewrite ^/novel-api/(.*)$ /api/`$1 break;
    proxy_pass http://127.0.0.1:9081;
    proxy_http_version 1.1;
    proxy_set_header Host `$host;
    proxy_set_header X-Real-IP `$remote_addr;
    proxy_set_header X-Forwarded-For `$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Prefix /novel-api;
    proxy_read_timeout 60s;
    proxy_send_timeout 60s;
    proxy_buffering off;
}
NGINX_EOF
echo WROTE"

Write-Host ""
Write-Host "=== Test config ==="
ssh -p 2222 -i $env:USERPROFILE\.ssh\id_rsa_vps root@8.137.116.121 "nginx -t" 2>&1 | Select-Object -First 5