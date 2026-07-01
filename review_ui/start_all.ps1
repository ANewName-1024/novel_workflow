# review_ui service manager
# - Manages Flask app on 127.0.0.1:21199
# - Manages reverse SSH tunnel VPS:127.0.0.1:9081 -> WEI3216:127.0.0.1:21199
# - Use: powershell -ExecutionPolicy Bypass -File start_all.ps1 [start|stop|status|restart]

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = "$env:TEMP"
$flaskLog = "$logDir\review_ui_flask.log"
$tunnelLog = "$logDir\review_ui_tunnel.log"
$sshKey = "$env:USERPROFILE\.ssh\id_rsa_vps_tunnel"
$vpsHost = "root@8.137.116.121"
$vpsPort = "2222"
$localPort = "21199"
$vpsTunnelPort = "9081"

function Start-Flask {
  if (Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -match "" -and $_.Path -like "*novel_workflow*review_ui*" }) {
    Write-Host "  Flask already running"
    return
  }
  Write-Host "  Starting Flask on 127.0.0.1:$localPort"
  Start-Process -FilePath "python" `
    -ArgumentList "app.py", "--port", $localPort, "--host", "127.0.0.1" `
    -WorkingDirectory $scriptDir `
    -RedirectStandardOutput $flaskLog `
    -RedirectStandardError "$flaskLog.err" `
    -WindowStyle Hidden
  Start-Sleep -Seconds 2
}

function Start-Tunnel {
  # Check if a tunnel is already running (by checking for sshd child on VPS)
  Write-Host "  Starting reverse SSH tunnel: VPS 127.0.0.1:$vpsTunnelPort -> WEI3216 127.0.0.1:$localPort"
  Start-Process -FilePath "ssh" `
    -ArgumentList @(
      "-i", $sshKey,
      "-p", $vpsPort,
      "-N",
      "-R", "${vpsTunnelPort}:127.0.0.1:${localPort}",
      "-o", "ServerAliveInterval=30",
      "-o", "ServerAliveCountMax=3",
      "-o", "ExitOnForwardFailure=yes",
      "-o", "StrictHostKeyChecking=accept-new",
      "-o", "UserKnownHostsFile=$env:USERPROFILE\.ssh\known_hosts_vps_tunnel",
      $vpsHost
    ) `
    -RedirectStandardOutput $tunnelLog `
    -RedirectStandardError "$tunnelLog.err" `
    -WindowStyle Hidden
  Start-Sleep -Seconds 3
}

function Stop-All {
  Write-Host "  Stopping Flask..."
  Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*review_ui*app.py*"
  } | Stop-Process -Force -ErrorAction SilentlyContinue
  Write-Host "  Stopping tunnel..."
  Get-Process ssh -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*$vpsTunnelPort*127.0.0.1*"
  } | Stop-Process -Force -ErrorAction SilentlyContinue
}

function Get-Status {
  Write-Host "=== Flask ==="
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$localPort/api/projects" -UseBasicParsing -TimeoutSec 3
    Write-Host "  http://127.0.0.1:$localPort → $($r.StatusCode) ($($r.Content.Length) bytes)"
  } catch {
    Write-Host "  http://127.0.0.1:$localPort → NOT RESPONDING"
  }
  Write-Host "=== Tunnel ==="
  $r = ssh -p $vpsPort -i $env:USERPROFILE\.ssh\id_rsa_vps $vpsHost "ss -tlnp 2>/dev/null | grep $vpsTunnelPort" 2>&1
  if ($r -match "LISTEN.*$vpsTunnelPort") {
    Write-Host "  VPS 127.0.0.1:$vpsTunnelPort → LISTENING"
  } else {
    Write-Host "  VPS 127.0.0.1:$vpsTunnelPort → NOT LISTENING"
  }
  Write-Host "=== External ==="
  try {
    $r = Invoke-WebRequest -Uri "http://8.137.116.121:9080/novel/" -UseBasicParsing -TimeoutSec 5
    Write-Host "  http://8.137.116.121:9080/novel/ → $($r.StatusCode) ($($r.Content.Length) bytes)"
  } catch {
    Write-Host "  http://8.137.116.121:9080/novel/ → NOT RESPONDING ($_)"
  }
}

switch ($args[0]) {
  "start" {
    Write-Host "=== Starting all ==="
    Start-Flask
    Start-Tunnel
    Write-Host ""
    Get-Status
  }
  "stop" {
    Write-Host "=== Stopping all ==="
    Stop-All
  }
  "restart" {
    Stop-All
    Start-Sleep -Seconds 2
    Start-Flask
    Start-Tunnel
    Get-Status
  }
  "status" {
    Get-Status
  }
  default {
    Write-Host "Usage: $PSCommandPath [start|stop|status|restart]"
  }
}