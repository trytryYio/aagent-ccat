#!/bin/bash
cd /home/user/projects/AgentProject/Agent
export PYTHONPATH=/home/user/projects/AgentProject/Agent
echo "Starting FAST ingestion at $(date)"
backend/.venv/bin/python3 -m rag.scripts.pipeline.ingest_data_fast 2>&1 | tee /tmp/ingest_fast.log
echo "Ingestion finished at $(date), exit code: $?"
