#!/bin/bash
# 启动后端（从 WSL 内部执行，避免 Git Bash 路径编码问题）
cd /home/user/projects/AgentProject/Agent
pkill -f "uvicorn.*app.main" 2>/dev/null
sleep 1
export PYTHONPATH=/home/user/projects/AgentProject/Agent/backend
setsid /home/user/projects/AgentProject/Agent/backend/.venv/bin/python3 \
  -m uvicorn app.main:app \
  --app-dir /home/user/projects/AgentProject/Agent/backend \
  --host 0.0.0.0 --port 8000 > /tmp/backend.log 2>&1 &
echo "Backend started (PID: $!)"
