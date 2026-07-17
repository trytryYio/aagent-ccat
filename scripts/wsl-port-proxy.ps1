# wsl-port-proxy.ps1
#
# 让 Windows 通过 localhost:3306 (MySQL) / localhost:6379 (Redis) 访问
# WSL 内的 docker 容器 mysql-dev / redis-dev。
#
# 用法: 以管理员身份运行 PowerShell，执行 .\wsl-port-proxy.ps1
#  - 第一次: 手动从管理员 PowerShell/资源管理器"以管理员身份运行"启动
#  - 之后: 通过 "Windows 启动文件夹" 或 "任务计划程序" 在开机时自动跑
#
# 注: 故意不使用 `#Requires -RunAsAdministrator`,
#     否则非管理员调用时脚本无法自提权。
#     下面的 \$isAdmin 检 + Start-Process -Verb RunAs 负责提权。

$ErrorActionPreference = "Stop"
$WSL_DISTRO = "Ubuntu"
$WSL_USER   = "user"

# 要映射的端口 (左侧 Windows，右侧 WSL 容器)
$PORTS = @(3306, 6379)

# ---- 自检: 必须以管理员身份运行 ----
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Host "[!] 需要管理员权限，正在以管理员身份重新启动..." -ForegroundColor Yellow
    $scriptPath = $MyInvocation.MyCommand.Path
    if (-not $scriptPath) {
        Write-Host "[X] 无法自提权: 请右键此脚本 -> 以管理员身份运行" -ForegroundColor Red
        pause
        exit 1
    }
    Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" -Verb RunAs -Wait
    exit $LASTEXITCODE
}

# ---- 1) WSL 内保活 mysql-dev/redis-dev ----
# 把 bash 命令整体塞到 PowerShell 单引号字符串里，避免 PS 解析 || $() 等
# 然后通过 stdin 喂给 wsl.exe 的 bash。
Write-Host "[*] 启动 WSL 容器保活..." -ForegroundColor Cyan
$bashScript = @'
set +e
docker inspect -f '{{.State.Status}}' mysql-dev 2>/dev/null | grep -q '^running$' || docker start mysql-dev >/dev/null 2>&1
docker inspect -f '{{.State.Status}}' redis-dev 2>/dev/null | grep -q '^running$' || docker start redis-dev >/dev/null 2>&1
sleep 2
echo '=== CONTAINER STATUS ==='
for c in mysql-dev redis-dev; do
  s=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)
  echo "$c : $s"
done
echo '=== PORT CHECK ==='
for p in 3306 6379; do
  (echo > /dev/tcp/127.0.0.1/$p) 2>/dev/null && echo "127.0.0.1:$p OK" || echo "127.0.0.1:$p FAIL"
done
echo '=== WSL IP ==='
hostname -I | awk '{print $1}'
'@

# 通过 stdin 喂给 wsl.exe bash。-l 让 bash 用 login shell 环境，PATH 包含 /usr/local/bin 等
$resetOut = $bashScript | wsl.exe -d $WSL_DISTRO -u $WSL_USER -- bash -l 2>&1
$resetOut | ForEach-Object { Write-Host "    $_" }

# ---- 2) 取 WSL 当前 IP ----
Write-Host "[*] 查询 WSL 当前 IP..." -ForegroundColor Cyan
$wslIp = ($resetOut -split "`n" | Select-String -Pattern '^\s*\d{1,3}(\.\d{1,3}){3}$' | Select-Object -First 1).ToString().Trim()
if (-not $wslIp) {
    # fallback: 再单独问一次
    $wslIp = (wsl.exe -d $WSL_DISTRO -u $WSL_USER -- hostname -I).Trim().Split(' ')[0]
}
if (-not $wslIp) {
    Write-Host "[X] 无法获取 WSL IP" -ForegroundColor Red
    pause
    exit 1
}
Write-Host "[+] WSL IP = $wslIp" -ForegroundColor Green

# ---- 3) 清理旧 portproxy ----
Write-Host "[*] 清理旧 portproxy 规则..." -ForegroundColor Cyan
$existing = netsh interface portproxy show v4tov4 2>&1
foreach ($port in $PORTS) {
    if ($existing -match "0\.0\.0\.0\s+$port\s+") {
        netsh interface portproxy delete v4tov4 listenport=$port listenaddress=0.0.0.0 2>$null | Out-Null
        Write-Host "    [-] 删除 0.0.0.0:$port" -ForegroundColor DarkGray
    }
}

# ---- 4) 添加新规则 ----
foreach ($port in $PORTS) {
    Write-Host "[*] 添加 0.0.0.0:$port -> ${wslIp}:${port}" -ForegroundColor Cyan
    netsh interface portproxy add v4tov4 listenport=$port listenaddress=0.0.0.0 connectport=$port connectaddress=$wslIp 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    [X] 失败" -ForegroundColor Red
    }
}

# ---- 5) 防火墙放行 ----
foreach ($port in $PORTS) {
    $ruleName = "WSL PortProxy $port"
    if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -LocalPort $port -Protocol TCP -Action Allow -Profile Any 2>&1 | Out-Null
        Write-Host "    [+] 防火墙规则: $ruleName" -ForegroundColor DarkGray
    }
}

# ---- 6) 验证 ----
Write-Host ""
Write-Host "[*] 当前 portproxy 规则:" -ForegroundColor Cyan
netsh interface portproxy show v4tov4 | Out-String | Write-Host

Write-Host "[*] 连通性自检:" -ForegroundColor Cyan
foreach ($port in $PORTS) {
    $test = Test-NetConnection -ComputerName localhost -Port $port -WarningAction SilentlyContinue -InformationLevel Quiet 2>&1
    if ($test) {
        Write-Host "    [OK]   localhost:$port" -ForegroundColor Green
    } else {
        Write-Host "    [FAIL] localhost:$port" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "[完成] 后续在 Windows 端可直接用 localhost:3306 (mysql) / localhost:6379 (redis) 连接。" -ForegroundColor Green
Write-Host "      注意: WSL 重启后 IP 会变，重新跑本脚本即可更新。" -ForegroundColor DarkGray

if ($MyInvocation.MyCommand.Path -and (Test-Path -Path $MyInvocation.MyCommand.Path)) {
    pause
}
