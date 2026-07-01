[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$cmds = @(
  "cat /etc/nginx/conf.d/rc-9080.conf | grep -n 'location'"
  "ls /root/.ssh/ 2>/dev/null"
  "ls /root/.ssh/authorized_keys 2>/dev/null"
  "cat /root/.ssh/authorized_keys 2>/dev/null | head -3"
)
foreach ($c in $cmds) {
  Write-Host "### $c" -ForegroundColor Cyan
  ssh -p 2222 -i $env:USERPROFILE\.ssh\id_rsa_vps root@8.137.116.121 $c 2>&1
  Write-Host ""
}