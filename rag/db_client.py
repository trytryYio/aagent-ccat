import os
import logging
from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, Distance,
    HnswConfigDiff, PayloadSchemaType,
)
from dotenv import load_dotenv

# 加载环境变量：cwd/.env 优先，回退到 backend/.env（rag 脚本从项目根独立运行时）
load_dotenv(override=False)
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", ".env"), override=False)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class QdrantManager:
    """
    Qdrant 向量数据库管理类
    支持本地连接和云端连接，并负责 Collection 的初始化
    """
    def __init__(self):
        # 优先从环境变量读取云端配置
        self.url = os.getenv("QDRANT_URL")
        self.api_key = os.getenv("QDRANT_API_KEY")
        self.host = os.getenv("QDRANT_HOST", "localhost")
        self.port = int(os.getenv("QDRANT_PORT", 6333))

        try:
            if self.url and self.api_key:
                # 连接云端 Qdrant
                self.client = QdrantClient(
                    url=self.url,
                    api_key=self.api_key,
                    timeout=60,
                )
                logger.info(f"成功连接至 Qdrant Cloud: {self.url}")
            else:
                # 连接本地 Qdrant
                self.client = QdrantClient(host=self.host, port=self.port)
                logger.info(f"成功连接至本地 Qdrant: {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Qdrant 连接失败: {str(e)}")
            raise e

    def init_collection(self, collection_name="products"):
        """
        初始化 Collection，按集合名配置命名向量：
        - products: text(1024) + image(512) 双向量
        - citations: text(1024) 单向量
        HNSW 参数：m=32, ef_construct=200（优于默认 m=16, ef_construct=100）
        """
        try:
            # 检查 Collection 是否已存在
            collections = self.client.get_collections().collections
            exists = any(c.name == collection_name for c in collections)

            if exists:
                logger.info(f"Collection '{collection_name}' 已存在，跳过创建。")
                return

            logger.info(f"正在创建 Collection: {collection_name}...")

            # HNSW 参数优化：m=32 提升召回率，ef_construct=200 提升建图质量
            hnsw_config = HnswConfigDiff(m=32, ef_construct=200)

            # 按集合名区分向量配置
            if collection_name == "products":
                vectors_config = {
                    "text": VectorParams(size=1024, distance=Distance.COSINE),
                    "image": VectorParams(size=512, distance=Distance.COSINE),
                }
            elif collection_name == "documents":
                vectors_config = {
                    "text": VectorParams(size=1024, distance=Distance.COSINE),
                }
            else:  # citations 只需文本向量
                vectors_config = {
                    "text": VectorParams(size=1024, distance=Distance.COSINE),
                }

            self.client.recreate_collection(
                collection_name=collection_name,
                vectors_config=vectors_config,
                hnsw_config=hnsw_config,
            )

            # 创建 Payload 索引（加速结构化过滤）
            if collection_name in ("products", "documents"):
                self._create_payload_indexes(collection_name)

            logger.info(f"Collection '{collection_name}' 初始化成功。")
        except Exception as e:
            logger.error(f"Collection 初始化失败: {str(e)}")

    def _create_payload_indexes(self, collection_name: str):
        """为高频过滤字段创建 Payload 索引，过滤从 O(n) 降为 O(log n)。"""
        index_fields = {
            "price": PayloadSchemaType.FLOAT,
            "category": PayloadSchemaType.KEYWORD,
            "gender": PayloadSchemaType.KEYWORD,
            "series": PayloadSchemaType.KEYWORD,
            "tenant_id": PayloadSchemaType.KEYWORD,
        }
        for field_name, field_type in index_fields.items():
            try:
                self.client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=field_type,
                )
                logger.info(f"Payload 索引已创建: {field_name} ({field_type})")
            except Exception as e:
                # 索引已存在时不报错
                if "already exists" not in str(e).lower():
                    logger.warning(f"Payload 索引创建失败 [{field_name}]: {e}")

# --- 对外暴露的接口 (单例模式) ---

_qdrant_manager = None

def get_qdrant_client():
    """
    获取 QdrantClient 实例
    """
    global _qdrant_manager
    if _qdrant_manager is None:
        _qdrant_manager = QdrantManager()
    return _qdrant_manager.client


def init_db():
    """
    初始化数据库及集合（products + citations）
    对已存在的 collection 补建 Payload 索引
    """
    manager = QdrantManager()
    manager.init_collection("products")
    manager.init_collection("citations")
    manager.init_collection("documents")
    # 对已存在的 collection 也尝试补建索引（幂等操作）
    for col in ["products", "documents"]:
        try:
            manager._create_payload_indexes(col)
        except Exception as e:
            logger.warning(f"补建 {col} 索引失败: {e}")
    # citations 集合需要 product_id 索引用于引用检索
    try:
        manager.client.create_payload_index(
            collection_name="citations",
            field_name="product_id",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        logger.info("Payload 索引已创建: citations.product_id (keyword)")
    except Exception as e:
        if "already exists" not in str(e).lower():
            logger.warning(f"补建 citations 索引失败: {e}")

if __name__ == "__main__":
    # 作为脚本运行进行初始化
    init_db()
