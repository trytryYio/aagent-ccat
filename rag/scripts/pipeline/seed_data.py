"""种子数据入库：读取 products.csv → 向量化 → 写入 Qdrant → 生成 citations"""

import csv
import logging
import os
import sys
import uuid

# 项目根目录
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # rag/scripts/
_RAG_DIR = os.path.dirname(_SCRIPT_DIR)  # rag/
_PROJECT_DIR = os.path.dirname(_RAG_DIR)  # Agent/
BASE_DIR = _PROJECT_DIR
sys.path.insert(0, BASE_DIR)

from rag.embedding import embed_image, embed_text
from rag.db_client import get_qdrant_client
from qdrant_client.models import PointStruct

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CSV_PATH = os.path.join(BASE_DIR, "rag", "data", "products.csv")
IMAGES_DIR = os.path.join(BASE_DIR, "rag", "data", "images")
COLLECTION = "products"


def load_products() -> list[dict]:
    """加载 products.csv"""
    products = []
    if not os.path.exists(CSV_PATH):
        logger.warning("products.csv not found at %s", CSV_PATH)
        return products
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            products.append(row)
    return products


def seed():
    """主流程：读取 CSV → 向量化 → 写入 Qdrant"""
    products = load_products()
    if not products:
        logger.error("No products to seed, aborting")
        return

    logger.info("Loaded %d products from %s", len(products), CSV_PATH)
    client = get_qdrant_client()
    points = []

    for i, product in enumerate(products):
        pid = product.get("product_id", f"product_{i:03d}")
        name = product.get("name", "")
        desc = product.get("description", "")
        price = float(product.get("price", 0))
        category = product.get("category", "")
        image_url = product.get("image_url", "")

        # 文本向量
        text_content = f"{name} {desc} {category}"
        text_emb = embed_text(text_content)

        # 图片向量（如果有本地图片）
        image_emb = None
        img_path = os.path.join(IMAGES_DIR, f"{pid}.jpg")
        if os.path.exists(img_path):
            with open(img_path, "rb") as f:
                image_emb = embed_image(f.read())

        point_id = uuid.uuid5(uuid.NAMESPACE_DNS, pid)
        vector = {"text": text_emb}
        if image_emb:
            vector["image"] = image_emb
        else:
            vector["image"] = text_emb[:512]  # fallback

        points.append(PointStruct(
            id=str(point_id),
            vector=vector,
            payload={
                "product_id": pid,
                "name": name,
                "price": price,
                "description": desc,
                "category": category,
                "image_url": image_url,
            },
        ))

        if (i + 1) % 20 == 0:
            client.upsert(collection_name=COLLECTION, points=points)
            logger.info("Upserted %d/%d", i + 1, len(products))
            points = []

    if points:
        client.upsert(collection_name=COLLECTION, points=points)

    logger.info("Seed complete: %d products inserted", len(products))

    # 自动调用 citations 精细化
    try:
        from rag.scripts.refine_knowledge_base import refine
        refine()
        logger.info("Citations refined")
    except ImportError:
        logger.warning("refine_knowledge_base not found, skipping citations refinement")


if __name__ == "__main__":
    seed()