"""LangGraph Agent 编排模块"""

import os
import sys

# 确保 backend/ 和项目根目录在路径中
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))  # backend/app/graph/
_APP_DIR = os.path.dirname(_THIS_DIR)  # backend/app/
_BACKEND_DIR = os.path.dirname(_APP_DIR)  # backend/
_PROJECT_DIR = os.path.dirname(_BACKEND_DIR)  # Agent/（项目根目录）

if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)  # 用于 app.* import
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)  # 用于 rag.* import