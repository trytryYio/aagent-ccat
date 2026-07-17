from rag.db_client import get_qdrant_client
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 清理 Qdrant 向量数据库中的集合并重新初始化
def clear_collection(collection_name="products"):
    client = get_qdrant_client()
    try:
        client.delete_collection(collection_name=collection_name)
        logger.info(f"已删除 Collection: {collection_name}")
        # 重新创建
        from rag.db_client import init_db
        init_db()
        logger.info(f"已重新初始化空的 Collection: {collection_name}")
    except Exception as e:
        logger.error(f"清理 Collection 失败: {e}")

if __name__ == "__main__":
    clear_collection()
