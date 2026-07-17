@echo off
REM run-as-admin.bat
REM 双击或以管理员身份运行此 bat，会调用同目录的 wsl-port-proxy.ps1
REM 需要把两个文件都放到同一目录:
REM   - run-as-admin.bat  (本文件)
REM   - wsl-port-proxy.ps1
REM
REM 推荐放置位置: C:\Users\<你的用户名>\scripts\
REM (项目内的 scripts/wsl-port-proxy.ps1 同样可用)

setlocal
set SCRIPT_DIR=%~dp0
set PS1=%SCRIPT_DIR%wsl-port-proxy.ps1

if not exist "%PS1%" (
    echo [X] 找不到 %PS1%
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
endlocal
