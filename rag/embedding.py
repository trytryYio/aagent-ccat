import os

# 解决国内下载 Hugging Face 模型慢或失败的问题
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# 本地 CLIP 模型目录（已离线下载到 D:\project\Agent\models\clip-ViT-B-32）
# 优先用本地路径加载，避免联网拉取模型时失败导致 embed_image 返回空
_LOCAL_CLIP_DIR = os.environ.get(
    "CLIP_MODEL_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models", "clip-ViT-B-32"),
)

import io
import logging
import requests
from typing import List
from PIL import Image

# 兜底加载 backend/.env（rag 脚本独立运行时也能读到 DASHSCOPE_API_KEY）
try:
    from dotenv import load_dotenv
    _ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", ".env")
    if os.path.exists(_ENV):
        load_dotenv(_ENV, override=False)
except Exception:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-v3")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))


class EmbeddingEngine:
    """
    文本向量化：阿里云 DashScope text-embedding-v3（云端，用 DASHSCOPE_API_KEY）
    图片向量化：本地 CLIP clip-ViT-B-32（512d，延迟加载）
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.device = None  # 延迟到 _ensure_clip 时检测
        self._api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        self.clip_model = None  # CLIP 延迟加载（仅图片向量化时）

    # ---- 阿里云文本向量化 ----
    def embed_text(self, text: str) -> List[float]:
        if not self._api_key:
            logger.error("DASHSCOPE_API_KEY 未配置，无法向量化")
            return []
        out = self.embed_text_batch([text])
        return out[0] if out else []

    def embed_text_batch(self, texts: List[str], batch_size: int = 10) -> List[List[float]]:
        """批量文本向量化（阿里云 text-embedding-v3，1024d）。"""
        if not texts:
            return []
        if not self._api_key:
            logger.error("DASHSCOPE_API_KEY 未配置")
            return [[] for _ in texts]

        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        results = [None] * len(texts)
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            payload = {"model": EMBED_MODEL, "input": batch, "dimensions": EMBED_DIM}
            try:
                resp = requests.post(f"{DASHSCOPE_BASE}/embeddings", headers=headers, json=payload, timeout=30)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                for item in data:
                    idx = item.get("index", 0)
                    if 0 <= idx < len(batch):
                        results[i + idx] = item.get("embedding", [])
            except Exception as e:
                logger.error(f"阿里云向量化失败(批 {i}): {e}")
        return [r if r is not None else [] for r in results]

    # ---- 本地 CLIP 图片向量化 ----
    def _ensure_clip(self):
        if self.clip_model is None:
            import torch
            from sentence_transformers import SentenceTransformer
            # 延迟检测设备
            if self.device is None:
                self.device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
            logger.info(f"加载 CLIP 模型至设备: {self.device}（仅图片向量化需要）")
            # 优先用本地离线模型目录；若不存在则回退到 HF 仓库名（走镜像在线拉取）
            clip_src = _LOCAL_CLIP_DIR if os.path.exists(_LOCAL_CLIP_DIR) else "clip-ViT-B-32"
            logger.info(f"CLIP 模型来源: {clip_src}")
            self.clip_model = SentenceTransformer(clip_src, device=self.device)
            logger.info("CLIP 模型加载成功 (512d)")

    def embed_image(self, image_bytes: bytes) -> List[float]:
        try:
            self._ensure_clip()
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            return self.clip_model.encode(image).tolist()
        except Exception as e:
            logger.error(f"图片向量化失败: {str(e)}")
            return []


# --- 对外暴露的接口 (单例) ---
_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = EmbeddingEngine()
    return _engine


def embed_text(text: str) -> List[float]:
    return get_engine().embed_text(text)


def embed_text_batch(texts: List[str], batch_size: int = 10) -> List[List[float]]:
    return get_engine().embed_text_batch(texts, batch_size=batch_size)


def embed_image(image_bytes: bytes) -> List[float]:
    return get_engine().embed_image(image_bytes)


if __name__ == "__main__":
    v = embed_text("推荐一款高性价比的羽毛球鞋")
    print(f"文本向量维度: {len(v)}")
