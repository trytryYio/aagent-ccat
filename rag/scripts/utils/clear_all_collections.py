"""清空 Qdrant 中所有 collection（products + citations），为全量重建做准备。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))

from rag.db_client import get_qdrant_client
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def clear_all():
    client = get_qdrant_client()
    collections = ["products", "citations"]

    for name in collections:
        try:
            # 检查是否存在
            existing = [c.name for c in client.get_collections().collections]
            if name in existing:
                client.delete_collection(collection_name=name)
                logger.info(f"✅ 已删除 collection: {name}")
            else:
                logger.info(f"⏭️ collection {name} 不存在，跳过")
        except Exception as e:
            logger.error(f"❌ 删除 {name} 失败: {e}")

    # 重新初始化空 collections
    from rag.db_client import init_db
    init_db()
    logger.info("✅ 已重新初始化空的 collections (products + citations)")

    # 验证
    for name in collections:
        info = client.get_collection(collection_name=name)
        logger.info(f"📊 {name}: {info.points_count} 条数据")

if __name__ == "__main__":
    clear_all()
