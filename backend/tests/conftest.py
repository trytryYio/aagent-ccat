"""共享 pytest fixtures"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
import pytest_asyncio

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "backend"))

# 确保环境变量
os.environ.setdefault("FAST_PATH_ENABLED", "true")
os.environ.setdefault("FAST_PATH_IMAGE_THRESHOLD", "0.85")
os.environ.setdefault("FAST_PATH_TEXT_THRESHOLD", "0.75")
os.environ.setdefault("FAST_PATH_HYBRID_IMAGE_THRESHOLD", "0.90")


@pytest.fixture
def reset_fast_path_stats():
    """每个测试前重置快速路径计数器"""
    from rag.hybrid_search import _fast_path_stats
    _fast_path_stats["total"] = 0
    _fast_path_stats["fast_path"] = 0
    yield
    _fast_path_stats["total"] = 0
    _fast_path_stats["fast_path"] = 0


@pytest.fixture
def mock_image_search():
    """Mock CLIP 图像检索"""
    with patch("rag.hybrid_search.search_by_image") as mock:
        yield mock


@pytest.fixture
def mock_text_search():
    """Mock BGE-M3 文本检索"""
    with patch("rag.hybrid_search.search_by_text") as mock:
        yield mock


@pytest.fixture
def mock_rerank():
    """Mock Rerank API"""
    with patch("rag.hybrid_search.rerank") as mock:
        yield mock
