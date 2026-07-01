<#
.SYNOPSIS
    一键启动 novel_workflow 全套运行环境: llama-server + review_ui.

.DESCRIPTION
    后台启动两个进程, 前台 tail 两边日志:

      1. llama-server  (LLM 后端, 默认 127.0.0.1:60443)
         - 路径可改: -LlamaPath / -ModelPath / -MmprojPath
         - 已运行时自动跳过, 不会重复拉

      2. novel serve  (review_ui Web, 默认 127.0.0.1:21199)
         - 用当前目录的 novel.py + config.yaml

    退出方式:
      - Ctrl+C 触发 cleanup, 同时 Kill 两个进程
      - 或新开终端跑 .\tools\stop_driver.ps1

    日志位置:
      - logs/llama_server.log
      - logs/review_ui.log

.PARAMETER LlamaPath
    llama-server.exe 路径, 默认 D:\application\llama-b9196-bin-win-cuda-13.1-x64\llama-server.exe.

.PARAMETER ModelPath
    GGUF 模型路径, 默认 D:\download\Qwythos-9B-Claude-Mythos-5-1M-MTP-Q8_0.gguf (视觉版).

.PARAMETER MmprojPath
    视觉投影 mmproj 路径, 默认 D:\download\mmproj-Qwythos-9B-Claude-Mythos-5-1M-F16.gguf.
    设为空字符串跳过视觉.

.PARAMETER LlamaPort
    llama-server 端口, 默认 60443.

.PARAMETER UiHost
    review_ui host, 默认 127.0.0.1.

.PARAMETER UiPort
    review_ui port, 默认 21199.

.PARAMETER NoLlama
    跳过启动 llama-server (假设外部已跑).

.EXAMPLE
    PS> .\tools\run_driver.ps1
    启动 LLM + Web UI, tail logs.

.EXAMPLE
    PS> .\tools\run_driver.ps1 -NoLlama
    只启动 review_ui (llama-server 已经在跑).

.EXAMPLE
    PS> .\tools\run_driver.ps1 -ModelPath D:\download\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
    切到 35B MoE 模型 (重推理但慢).
#>

[CmdletBinding()]
param(
    [string]$LlamaPath  = "D:\application\llama-b9196-bin-win-cuda-13.1-x64\llama-server.exe",
    [string]$ModelPath  = "D:\download\Qwythos-9B-Claude-Mythos-5-1M-MTP-Q8_0.gguf",
    [string]$MmprojPath = "D:\download\mmproj-Qwythos-9B-Claude-Mythos-5-1M-F16.gguf",
    [int]$LlamaPort     = 60443,
    [string]$UiHost     = "127.0.0.1",
    [int]$UiPort        = 21199,
    [switch]$NoLlama
)

$ErrorActionPreference = "Stop"

# ---------- 0. 前置检查 ----------

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..") | Select-Object -ExpandProperty Path
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$llamaLog  = Join-Path $LogDir "llama_server.log"
$uiLog     = Join-Path $LogDir "review_ui.log"
$stateFile = Join-Path $LogDir ".run_driver.pids"

Write-Host "===== run_driver: novel_workflow 一键启动 =====" -ForegroundColor Cyan
Write-Host "  repo root:    $RepoRoot"
Write-Host "  llama-server: $($LlamaPath | Split-Path -Leaf) (port $LlamaPort)"
Write-Host "  review_ui:    $UiHost`:$UiPort"
Write-Host "  log dir:      $LogDir"
Write-Host ""

# ---------- 1. 启动 llama-server (后台) ----------

$llamaProc = $null
if (-not $NoLlama) {
    if (-not (Test-Path $LlamaPath)) {
        throw "llama-server not found at $LlamaPath. Use -LlamaPath 或 -NoLlama 跳过."
    }
    if (-not (Test-Path $ModelPath)) {
        throw "Model not found at $ModelPath. Use -ModelPath 指定."
    }

    # 检查端口是否已被占用 (说明已有人在跑)
    $llamaAlready = Get-NetTCPConnection -LocalPort $LlamaPort -State Listen -ErrorAction SilentlyContinue
    if ($llamaAlready) {
        Write-Host "[llama] 端口 $LlamaPort 已在监听, 跳过启动 (外部已跑)" -ForegroundColor Yellow
    } else {
        $llamaArgs = @(
            "-m", "`"$ModelPath`""
            "-ngl", "99", "--flash-attn", "on", "--jinja"
            "-c", "65536", "--reasoning", "off"
            "-t", "14", "-tb", "14", "-b", "1024", "-ub", "256"
            "--cache-type-k", "q8_0", "--cache-type-v", "q4_0"
            "--cont-batching", "--parallel", "2"
            "--mlock", "--host", "127.0.0.1", "--port", "$LlamaPort"
        )
        if ($MmprojPath -and (Test-Path $MmprojPath)) {
            $llamaArgs += @("--mmproj", "`"$MmprojPath`"")
        }

        $llamaCmd = "& `"$LlamaPath`" $($llamaArgs -join ' ')"
        Write-Host "[llama] 启动: $llamaCmd"
        $llamaProc = Start-Process -FilePath powershell.exe `
            -ArgumentList "-NoProfile", "-Command", $llamaCmd `
            -RedirectStandardOutput $llamaLog `
            -RedirectStandardError  (Join-Path $LogDir "llama_server.err") `
            -WindowStyle Hidden `
            -PassThru
        Write-Host "[llama] PID = $($llamaProc.Id), 等待 ready ..." -ForegroundColor Green

        # 轮询 /health
        $ready = $false
        for ($i = 0; $i -lt 120; $i++) {
            Start-Sleep -Seconds 1
            try {
                $r = Invoke-WebRequest -Uri "http://127.0.0.1:$LlamaPort/health" -TimeoutSec 2 -UseBasicParsing
                if ($r.StatusCode -eq 200) {
                    $ready = $true
                    Write-Host "[llama] ready in $i s" -ForegroundColor Green
                    break
                }
            } catch {
                # 还没起来, 继续等
            }
        }
        if (-not $ready) {
            throw "llama-server 启动超时 (120s). 看 log: $llamaLog"
        }
    }
} else {
    Write-Host "[llama] -NoLlama, 假设外部在跑" -ForegroundColor Yellow
}

# ---------- 2. 启动 review_ui (后台) ----------

$uiProc = $null
$uiAlready = Get-NetTCPConnection -LocalPort $UiPort -State Listen -ErrorAction SilentlyContinue
if ($uiAlready) {
    Write-Host "[ui] 端口 $UiPort 已在监听, 跳过启动" -ForegroundColor Yellow
} else {
    $uiCmd = "python novel.py serve --host $UiHost --port $UiPort"
    Write-Host "[ui] 启动: $uiCmd"
    $uiProc = Start-Process -FilePath powershell.exe `
        -ArgumentList "-NoProfile", "-Command", $uiCmd `
        -RedirectStandardOutput $uiLog `
        -RedirectStandardError  (Join-Path $LogDir "review_ui.err") `
        -WindowStyle Hidden `
        -PassThru `
        -WorkingDirectory $RepoRoot
    Write-Host "[ui] PID = $($uiProc.Id), 等待 ready ..." -ForegroundColor Green

    $uiReady = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $r = Invoke-WebRequest -Uri "http://$($UiHost):$UiPort/login" -TimeoutSec 2 -UseBasicParsing
            if ($r.StatusCode -in 200, 302) {
                $uiReady = $true
                Write-Host "[ui] ready in $i s" -ForegroundColor Green
                break
            }
        } catch {
            # 还没起来
        }
    }
    if (-not $uiReady) {
        Write-Host "[ui] WARNING: 30s 内没看到 /login 200/302, 继续 tail log" -ForegroundColor Yellow
    }
}

# ---------- 3. 持久化 PID (供 stop_driver 用) ----------

$pids = @{
    llama = if ($llamaProc) { $llamaProc.Id } else { $null }
    ui    = if ($uiProc)    { $uiProc.Id }    else { $null }
}
$pids | ConvertTo-Json | Set-Content $stateFile
Write-Host "[state] PIDs saved to $stateFile" -ForegroundColor DarkGray

# ---------- 4. Tail logs + 等待 Ctrl+C ----------

Write-Host ""
Write-Host "===== 服务已启动, tail logs (Ctrl+C 退出) =====" -ForegroundColor Green
Write-Host "  llama log: $llamaLog"
Write-Host "  ui log:    $uiLog"
Write-Host ""
Write-Host "  浏览器打开: http://$($UiHost):$UiPort/login" -ForegroundColor Cyan
Write-Host ""

# cleanup 函数
$cleanup = {
    Write-Host ""
    Write-Host "[cleanup] 收到退出信号, 关进程 ..." -ForegroundColor Yellow
    foreach ($key in @("llama", "ui")) {
        $p = (Get-Content $stateFile -Raw | ConvertFrom-Json).$key
        if ($p) {
            $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "[cleanup] Kill $key (PID $p) ..."
                Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Remove-Item $stateFile -ErrorAction SilentlyContinue
    Write-Host "[cleanup] done. 再见" -ForegroundColor Green
    exit 0
}

# 注册 Ctrl+C
$null = Register-EngineEvent -SourceIdentifier "PowerShell.Exiting" -Action $cleanup
[Console]::TreatControlCAsInput = $false

# 用 Get-Content -Wait tail 两个日志 (PS 5.1 不支持 -Tail 多个, 用两个 jobs)
$llamaJob = Start-Job -ScriptBlock {
    param($p) Get-Content $p -Wait -Tail 0
} -ArgumentList $llamaLog

$uiJob = Start-Job -ScriptBlock {
    param($p) Get-Content $p -Wait -Tail 0
} -ArgumentList $uiLog

# 打印时区分
Write-Host "----- [llama-server] -----" -ForegroundColor Magenta
Write-Host "----- [review_ui]   -----" -ForegroundColor Magenta

try {
    while ($true) {
        Start-Sleep -Seconds 1
        # 检查后台 job 有没有新行
        $llamaLines = Receive-Job $llamaJob -Keep 2>$null
        $uiLines    = Receive-Job $uiJob    -Keep 2>$null
        if ($llamaLines) {
            foreach ($l in $llamaLines) { Write-Host "[llama] $l" -ForegroundColor Gray }
        }
        if ($uiLines) {
            foreach ($l in $uiLines) { Write-Host "[ui]    $l" -ForegroundColor Gray }
        }
    }
} finally {
    & $cleanup
}
