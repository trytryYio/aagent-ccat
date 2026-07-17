#!/bin/bash
# Vite dev server 启动脚本
# 修复：原来用 /mnt/d/Program Files/nodejs（Windows node），在 WSL 内执行 .bin/vite
# 时 shebang #!/usr/bin/env node 找不到 Linux node → "vite: not found"
# 现在用 WSL 内 nvm 的 node 直接跑 vite.js

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

WEB_DIR="/home/user/projects/AgentProject/Agent/web"
LOG_FILE="/tmp/agent_web_vite.log"

cd "$WEB_DIR" || exit 1

NODE_BIN="$(command -v node)"
if [ -z "$NODE_BIN" ]; then
    echo "ERROR: node 不可用，请检查 nvm 安装" >&2
    exit 1
fi

# 杀掉旧 vite 进程
pkill -f "vite/bin/vite.js" 2>/dev/null || true
sleep 1

# setsid 脱离 shell，避免 Git Bash 退出时 SIGTERM 杀掉 vite
setsid "$NODE_BIN" node_modules/vite/bin/vite.js \
    --host 0.0.0.0 \
    --port 5173 \
    > "$LOG_FILE" 2>&1 < /dev/null &

echo "[frontend] Vite 已启动 (pid=$!)"
echo "[frontend] 日志: $LOG_FILE"
sleep 3
echo "[frontend] === 启动日志 ==="
cat "$LOG_FILE"

WSL_IP=$(hostname -I | awk '{print $1}')
echo ""
echo "[frontend] 浏览器访问: http://${WSL_IP}:5173/"
