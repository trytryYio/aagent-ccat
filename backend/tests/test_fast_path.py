"""快速路径优化 单元测试"""
import os
import pytest
from unittest.mock import patch, MagicMock

from rag.image_search import SearchResult
from rag.hybrid_search import (
    hybrid_search,
    get_fast_path_stats,
    FAST_PATH_ENABLED,
    FAST_PATH_IMAGE_THRESHOLD,
    FAST_PATH_TEXT_THRESHOLD,
    FAST_PATH_HYBRID_IMAGE_THRESHOLD,
)


def _make_results(count, base_score, step=0.05, source="image"):
    """生成模拟检索结果"""
    return [
        SearchResult(
            product_id=f"lining_{i:06d}",
            name=f"Shoe {i}",
            price=599.0 + i * 100,
            description=f"Shoe desc {i}",
            category="运动鞋/男鞋/跑步鞋",
            image_url=f"http://example.com/{i}.jpg",
            score=base_score - i * step,
            source=source,
            need_clarify=(base_score - i * step < 0.6),
        )
        for i in range(count)
    ]


class TestFastPathImage:
    @pytest.mark.usefixtures("reset_fast_path_stats")
    def test_high_confidence_image_skips_rerank(self, mock_image_search, mock_rerank):
        """CLIP > 0.85 -> 跳过 Rerank"""
        mock_image_search.return_value = _make_results(10, base_score=0.92)
        results = hybrid_search(image_embedding=[0.1] * 512, top_k=5)
        mock_rerank.assert_not_called()
        assert len(results) == 5

    @pytest.mark.usefixtures("reset_fast_path_stats")
    def test_low_confidence_image_triggers_rerank(self, mock_image_search, mock_rerank):
        """CLIP <= 0.85 -> 走完整路径"""
        mock_image_search.return_value = _make_results(10, base_score=0.72)
        mock_rerank.return_value = [{"index": i, "score": 0.9 - i * 0.05} for i in range(5)]
        results = hybrid_search(image_embedding=[0.1] * 512, top_k=5, query="test")
        mock_rerank.assert_called_once()

    @pytest.mark.usefixtures("reset_fast_path_stats")
    def test_boundary_threshold_image(self, mock_image_search, mock_rerank):
        """边界值测试"""
        mock_image_search.return_value = _make_results(10, base_score=0.85)
        hybrid_search(image_embedding=[0.1] * 512, top_k=5)
        mock_rerank.assert_not_called()

        mock_rerank.reset_mock()
        mock_image_search.return_value = _make_results(10, base_score=0.849)
        mock_rerank.return_value = [{"index": 0, "score": 0.8}]
        hybrid_search(image_embedding=[0.1] * 512, top_k=5, query="test")
        mock_rerank.assert_called_once()


class TestFastPathText:
    @pytest.mark.usefixtures("reset_fast_path_stats")
    def test_high_confidence_text_skips_rerank(self, mock_text_search, mock_rerank):
        """BGE-M3 > 0.75 -> 跳过 Rerank"""
        mock_text_search.return_value = _make_results(10, base_score=0.80, source="text")
        results = hybrid_search(text_embedding=[0.1] * 1024, top_k=5)
        mock_rerank.assert_not_called()

    @pytest.mark.usefixtures("reset_fast_path_stats")
    def test_low_confidence_text_triggers_rerank(self, mock_text_search, mock_rerank):
        """BGE-M3 <= 0.75 -> 走完整路径"""
        mock_text_search.return_value = _make_results(10, base_score=0.70, source="text")
        mock_rerank.return_value = [{"index": i, "score": 0.85} for i in range(5)]
        results = hybrid_search(text_embedding=[0.1] * 1024, top_k=5, query="test")
        mock_rerank.assert_called_once()


class TestFastPathHybrid:
    @pytest.mark.usefixtures("reset_fast_path_stats")
    def test_hybrid_high_image_skips_rerank(self, mock_image_search, mock_text_search, mock_rerank):
        """图文混合 + CLIP > 0.90 -> 跳过 Rerank"""
        mock_image_search.return_value = _make_results(10, base_score=0.93, source="image")
        mock_text_search.return_value = _make_results(10, base_score=0.70, source="text")
        results = hybrid_search(
            image_embedding=[0.1] * 512, text_embedding=[0.1] * 1024, top_k=5, query="test"
        )
        mock_rerank.assert_not_called()

    @pytest.mark.usefixtures("reset_fast_path_stats")
    def test_hybrid_low_image_full_pipeline(self, mock_image_search, mock_text_search, mock_rerank):
        """图文混合 + CLIP <= 0.90 -> 走完整路径"""
        mock_image_search.return_value = _make_results(10, base_score=0.75, source="image")
        mock_text_search.return_value = _make_results(10, base_score=0.80, source="text")
        mock_rerank.return_value = [{"index": i, "score": 0.88} for i in range(5)]
        results = hybrid_search(
            image_embedding=[0.1] * 512, text_embedding=[0.1] * 1024, top_k=5, query="test"
        )
        mock_rerank.assert_called_once()


class TestFastPathStats:
    @pytest.mark.usefixtures("reset_fast_path_stats")
    def test_fast_path_counter_increments(self, mock_image_search):
        """快速路径计数器递增"""
        stats_before = get_fast_path_stats()
        mock_image_search.return_value = _make_results(10, base_score=0.95)
        hybrid_search(image_embedding=[0.1] * 512, top_k=5)
        stats_after = get_fast_path_stats()
        assert stats_after["total"] == stats_before["total"] + 1
        assert stats_after["fast_path"] == stats_before["fast_path"] + 1

    @pytest.mark.usefixtures("reset_fast_path_stats")
    def test_full_path_counter(self, mock_image_search, mock_rerank):
        """完整路径不增加 fast_path"""
        stats_before = get_fast_path_stats()
        mock_image_search.return_value = _make_results(10, base_score=0.70)
        mock_rerank.return_value = [{"index": i, "score": 0.8} for i in range(5)]
        hybrid_search(image_embedding=[0.1] * 512, top_k=5, query="test")
        stats_after = get_fast_path_stats()
        assert stats_after["total"] == stats_before["total"] + 1
        assert stats_after["fast_path"] == stats_before["fast_path"]

    @pytest.mark.usefixtures("reset_fast_path_stats")
    def test_stats_ratio(self):
        """初始比例为 0"""
        stats = get_fast_path_stats()
        assert stats["ratio"] == 0.0


class TestFastPathEnvConfig:
    @pytest.mark.usefixtures("reset_fast_path_stats")
    def test_disable_fast_path_via_env(self, monkeypatch):
        """FAST_PATH_ENABLED=false 时关闭快速路径"""
        monkeypatch.setenv("FAST_PATH_ENABLED", "false")
        import importlib
        from rag import hybrid_search as hs_mod
        importlib.reload(hs_mod)
        assert hs_mod.FAST_PATH_ENABLED == False

    @pytest.mark.usefixtures("reset_fast_path_stats")
    def test_custom_threshold_via_env(self, monkeypatch):
        """自定义阈值从环境变量读取"""
        monkeypatch.setenv("FAST_PATH_IMAGE_THRESHOLD", "0.95")
        monkeypatch.setenv("FAST_PATH_TEXT_THRESHOLD", "0.85")
        monkeypatch.setenv("FAST_PATH_HYBRID_IMAGE_THRESHOLD", "0.97")
        import importlib
        from rag import hybrid_search as hs_mod
        importlib.reload(hs_mod)
        assert hs_mod.FAST_PATH_IMAGE_THRESHOLD == 0.95
        assert hs_mod.FAST_PATH_TEXT_THRESHOLD == 0.85
        assert hs_mod.FAST_PATH_HYBRID_IMAGE_THRESHOLD == 0.97
