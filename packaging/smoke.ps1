<#
    打包产物冒烟测试：启动 exe（无窗口模式），确认服务就绪并报告 frozen 状态。

    .\packaging\smoke.ps1 -Exe .\dist\uma-assistant\uma-assistant.exe
#>
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [int]$Port = 8791,
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'
$Exe = (Resolve-Path $Exe).Path
$proc = Start-Process -FilePath $Exe -ArgumentList '--no-window', '--port', $Port -PassThru
try {
    $base = "http://127.0.0.1:$Port"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $health = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod "$base/api/health" -TimeoutSec 2
            if ($health.status -eq 'ok') { break }
        } catch {
            if ($proc.HasExited) { throw "打包产物启动即退出（退出码 $($proc.ExitCode)），见 %LOCALAPPDATA%\uma-assistant\data\logs\app.log" }
        }
    }
    if (-not $health -or $health.status -ne 'ok') { throw "打包产物 ${TimeoutSeconds}s 内未就绪" }

    $info = Invoke-RestMethod "$base/api/system/info" -TimeoutSec 5
    "health    : $($health.status)  version=$($health.version)"
    "frozen    : $($info.app.frozen)"
    "data_dir  : $($info.paths.data)"
    "modules   : " + (($info.modules | ForEach-Object { "$($_.id)=$($_.status)" }) -join ', ')

    if (-not $info.app.frozen) { throw '打包产物应报告 frozen=true' }
    if ($info.setup.first_run -ne $true) { throw '全新的数据目录应处于首次使用状态' }
    'smoke: ok'
}
finally {
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
}
