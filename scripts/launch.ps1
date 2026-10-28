<#
一键启动脚本：定位 uv（找不到就回退到项目内虚拟环境），必要时安装依赖，然后启动桌面窗口。

用法（一般直接双击根目录的 start.cmd 即可）：
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launch.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launch.ps1 -NoWindow
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\launch.ps1 -Reinstall

退出码：0 正常；2 缺少 uv 且虚拟环境不可用；3 依赖安装失败；其余为应用自身退出码。
#>

[CmdletBinding()]
param(
    # 只启动本地服务，不弹窗口（用浏览器访问）
    [switch]$NoWindow,
    # 强制重新安装依赖
    [switch]$Reinstall,
    # 不使用 uv，直接用项目内虚拟环境启动（调试回退路径用）
    [switch]$SkipUv
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Host "!!  $Message" -ForegroundColor Yellow }
function Write-Fail([string]$Message) { Write-Host "xx  $Message" -ForegroundColor Red }

# uv 可能只装在 WinGet 目录里而不在 PATH 上，逐个位置找
function Find-Uv {
    $command = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $candidates = @(
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\uv.exe'),
        (Join-Path $env:ProgramFiles 'uv\uv.exe')
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) { return $path }
    }

    $packages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $packages) {
        return Get-ChildItem -LiteralPath $packages -Filter 'astral-sh.uv*' -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName 'uv.exe' } |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1
    }
    return $null
}

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$stampFile = Join-Path $projectRoot '.venv\.uma-sync-stamp'

# 关键依赖能导入，说明虚拟环境可用（比只看目录是否存在可靠）
function Test-VenvReady {
    if (-not (Test-Path -LiteralPath $venvPython)) { return $false }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $venvPython -c 'import fastapi, uvicorn, webview' 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}

# pyproject.toml / uv.lock 比上次安装时间新，说明依赖清单变过
function Test-ManifestChanged {
    if (-not (Test-Path -LiteralPath $stampFile)) { return $true }
    $stamp = (Get-Item -LiteralPath $stampFile).LastWriteTimeUtc
    $manifests = @('pyproject.toml', 'uv.lock') |
        ForEach-Object { Join-Path $projectRoot $_ } |
        Where-Object { Test-Path -LiteralPath $_ }
    foreach ($manifest in $manifests) {
        if ((Get-Item -LiteralPath $manifest).LastWriteTimeUtc -gt $stamp) { return $true }
    }
    return $false
}

Write-Step "项目目录: $projectRoot"

$uv = if ($SkipUv) { $null } else { Find-Uv }
if ($uv) {
    Write-Step "uv: $uv"
} else {
    Write-Warn '未在 PATH 或常用目录找到 uv，将直接使用项目内虚拟环境'
}

$envFile = Join-Path $projectRoot '.env'
$envExample = Join-Path $projectRoot '.env.example'
if (-not (Test-Path -LiteralPath $envFile) -and (Test-Path -LiteralPath $envExample)) {
    Copy-Item -LiteralPath $envExample -Destination $envFile
    Write-Warn '已按 .env.example 生成 .env；要用 AI 问答需在其中填入 DEEPSEEK_API_KEY'
}

$needsSync = $Reinstall -or (Test-ManifestChanged) -or (-not (Test-VenvReady))
if ($needsSync) {
    if (-not $uv) {
        Write-Fail '虚拟环境不可用，且未找到 uv。'
        Write-Host '请先安装 uv（任选其一），然后重新运行：'
        Write-Host '    winget install astral-sh.uv'
        Write-Host '    powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
        exit 2
    }

    Write-Step '准备依赖（首次运行需要下载，可能要几分钟）...'
    & $uv sync
    if ($LASTEXITCODE -ne 0) {
        Write-Fail '依赖安装失败，请检查网络或上面 uv 的输出'
        exit 3
    }
    if (-not (Test-VenvReady)) {
        Write-Fail '依赖安装完成，但关键包仍无法导入；可尝试删除 .venv 后重跑本脚本'
        exit 3
    }
    New-Item -ItemType File -Path $stampFile -Force | Out-Null
}

$appArgs = @('-m', 'app.desktop')
if ($NoWindow) { $appArgs += '--no-window' }

Write-Step '启动优俊少女助手（关闭窗口或按 Ctrl+C 停止）'
if ($uv) {
    & $uv run python @appArgs
} else {
    & $venvPython @appArgs
}
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Fail "应用异常退出，退出码 $exitCode"
}
exit $exitCode
