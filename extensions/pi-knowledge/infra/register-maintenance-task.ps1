# 注册 pi-knowledge 每日维护计划任务（需以当前用户身份运行）
# 用法: powershell -ExecutionPolicy Bypass -File register-maintenance-task.ps1 [-Time "03:30"]
param(
    [string]$Time = "03:30",
    [string]$TaskName = "pi-knowledge-maintain"
)

$ErrorActionPreference = "Stop"

# CLI 路径：本脚本位于 extensions/pi-knowledge/infra/
$extRoot = Split-Path -Parent $PSScriptRoot
$cliPath = Join-Path $extRoot "src\cli\maintain.ts"
if (-not (Test-Path $cliPath)) { throw "找不到 maintain CLI: $cliPath" }

$nodePath = (Get-Command node -ErrorAction Stop).Source

$action = New-ScheduledTaskAction -Execute $nodePath `
    -Argument "--experimental-strip-types `"$cliPath`" --quiet" `
    -WorkingDirectory $extRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "pi-knowledge 知识库每日维护（摄取收敛+记忆/图谱维护+审计报告）" -Force | Out-Null

Write-Host "已注册计划任务 '$TaskName'：每日 $Time 运行"
Write-Host "  $nodePath --experimental-strip-types $cliPath --quiet"
Write-Host "注意: 任务在当前用户环境运行，需保证 ARK_API_KEY 为用户级环境变量，且 Docker 服务常驻。"
Write-Host "手动触发: Start-ScheduledTask -TaskName $TaskName"
Write-Host "卸载:     Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
