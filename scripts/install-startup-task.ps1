# install-startup-task.ps1
#
# 注册 Windows 任务计划程序任务，开机时自动以最高权限运行 wsl-port-proxy.ps1
# 必须以管理员身份运行一次。
#
# 用法:
#   1. 打开 管理员 PowerShell
#   2. cd C:\Users\<你>\scripts
#   3. powershell -NoProfile -ExecutionPolicy Bypass -File install-startup-task.ps1
#   4. 重启电脑验证 (1 分钟内 Test-NetConnection localhost -Port 3306 应为 True)
#
# 卸载:
#   Unregister-ScheduledTask -TaskName "WSL PortProxy AutoReset" -Confirm:$false

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

$TaskName    = "WSL PortProxy AutoReset"
$PS1Path     = $PSCommandPath | Split-Path -Parent | Join-Path -ChildPath "wsl-port-proxy.ps1"
$PS1Path     = (Resolve-Path $PS1Path).Path
$Description = "开机时自动重设 WSL 内的 docker 端口映射到 Windows localhost (3306/6379)"

# 自提权
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Host "[!] 需要管理员权限" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $PS1Path)) {
    Write-Host "[X] 找不到 $PS1Path" -ForegroundColor Red
    exit 1
}

# 清理旧的同名任务
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[-] 清理旧任务" -ForegroundColor DarkGray
}

# 触发器: 开机时
$trigger = New-ScheduledTaskTrigger -AtStartup

# 操作: 用 SYSTEM 账户跑 pwsh 风格的 PowerShell
# 注: 用 -WindowStyle Hidden 让任务在后台跑，不弹黑窗
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PS1Path`""

# 设置
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew `
    -Compatibility Win8

# 主体: SYSTEM 账户，最高权限
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest -LogonType ServiceAccount

# 注册
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description $Description | Out-Null

Write-Host "[OK] 已注册任务: $TaskName" -ForegroundColor Green
Write-Host "     触发器: 开机时 (AtStartup)" -ForegroundColor Cyan
Write-Host "     脚本:   $PS1Path" -ForegroundColor Cyan
Write-Host "     账户:   SYSTEM (最高权限)" -ForegroundColor Cyan
Write-Host ""
Write-Host "重启电脑后会自动跑，1 分钟内应该能 localhost:3306 / 6379 直连" -ForegroundColor Green
Write-Host "立即测试: 重启 Windows 资源管理器不需要，net stop/start Winmgmt 也行；或直接重启电脑" -ForegroundColor DarkGray

Write-Host ""
Write-Host "查看任务: Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor DarkGray
Write-Host "立即触发: Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor DarkGray
Write-Host "删除任务: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor DarkGray
