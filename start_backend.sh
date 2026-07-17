#!/bin/bash
cd /home/user/projects/AgentProject/Agent
export PYTHONPATH=/home/user/projects/AgentProject/Agent
exec python3 -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
