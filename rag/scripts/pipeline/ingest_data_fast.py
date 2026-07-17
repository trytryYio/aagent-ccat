"""快速批量数据摄入 - 使用批量embedding加速。

优化点：
1. 文本向量化使用 batch API（10条/批，减少 API 调用次数）
2. 图片下载并行化（使用 ThreadPoolExecutor）
3. 分阶段处理：先文本，再图片，最后上传
"""
import os
import csv
import requests
import logging
import uuid
import re
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from qdrant_client.models import PointStruct

from rag.db_client import get_qdrant_client, QdrantManager
from rag.embedding import embed_text_batch, embed_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RAG_DIR = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
DEFAULT_IMG_DIR = os.path.join(_RAG_DIR, "data", "images")
DEFAULT_CSV = os.path.join(_RAG_DIR, "data", "products.csv")


def generate_deterministic_uuid(input_str: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, input_str))


def extract_attrs(description: str) -> Dict:
    attrs = {"brand": "李宁", "color": "未知", "techs": []}
    color_match = re.search(r"【颜色信息】(.*?)[。|；|!|?|$]", description)
    if color_match:
        attrs["color"] = color_match.group(1).strip()
    tech_matches = re.findall(r"【(.*?)】", description)
    attrs["techs"] = [t for t in tech_matches if t != "颜色信息"]
    return attrs


def build_rich_text(row: Dict) -> str:
    parts = [f"产品名称: {row.get('name', '')}"]
    desc = row.get("description", "")
    if desc:
        parts.append(f"描述: {desc}")
    if row.get("category"):
        parts.append(f"类别: {row['category']}")
    if row.get("price"):
        parts.append(f"价格: {row['price']}")
    if row.get("basic_info"):
        parts.append(f"基础信息: {row['basic_info']}")
    if row.get("introduction"):
        parts.append(f"介绍: {row['introduction']}")
    if row.get("series"):
        parts.append(f"系列: {row['series']}")
    return " ".join(parts)


def download_and_embed_image(image_url: str) -> List[float]:
    """下载图片并向量化，失败返回空列表"""
    if not image_url or not image_url.startswith("http"):
        return []
    try:
        raw_url = image_url.split("?")[0]
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(raw_url, timeout=15, headers=headers)
        if resp.status_code != 200:
            resp = requests.get(image_url, timeout=15, headers=headers)
        if resp.status_code == 200:
            return embed_image(resp.content)
    except Exception as e:
        logger.warning(f"图片处理失败: {e}")
    return []


def ingest_fast(csv_path: str = DEFAULT_CSV, local_img_dir: str = DEFAULT_IMG_DIR):
    """快速批量入库"""
    logger.info("=" * 60)
    logger.info("开始快速批量入库...")
    logger.info("=" * 60)

    # 1. 读取CSV
    rows = []
    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    logger.info(f"📖 读取到 {len(rows)} 条商品数据")

    # 2. 重建 collection
    client = get_qdrant_client()
    try:
        client.delete_collection("products")
    except Exception:
        pass
    QdrantManager().init_collection("products")
    logger.info("✅ 已重建 products collection")

    # 3. 批量文本向量化
    logger.info("📝 开始批量文本向量化...")
    texts = [build_rich_text(row) for row in rows]
    text_vectors = embed_text_batch(texts, batch_size=10)
    logger.info(f"✅ 文本向量化完成: {len(text_vectors)} 条")

    # 4. 并行图片下载+向量化
    logger.info("🖼️ 开始并行图片下载+向量化...")
    image_urls = [row.get("image_url", "") for row in rows]
    image_vectors = [None] * len(rows)

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_idx = {
            executor.submit(download_and_embed_image, url): idx
            for idx, url in enumerate(image_urls)
            if url and url.startswith("http")
        }
        done_count = 0
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                image_vectors[idx] = future.result()
            except Exception as e:
                logger.warning(f"图片处理失败 [{idx}]: {e}")
                image_vectors[idx] = []
            done_count += 1
            if done_count % 100 == 0:
                logger.info(f"  图片处理进度: {done_count}/{len(future_to_idx)}")

    success_img = sum(1 for v in image_vectors if v)
    logger.info(f"✅ 图片处理完成: {success_img}/{len(rows)} 成功")

    # 5. 构造 points
    logger.info("🔨 构造 Qdrant points...")
    points = []
    for i, row in enumerate(rows):
        product_id_raw = row["product_id"]
        point_id = generate_deterministic_uuid(product_id_raw)

        attrs = extract_attrs(row.get("description", ""))
        row["attrs_techs"] = ",".join(attrs.get("techs", []))
        row["attrs_color"] = attrs.get("color", "未知")
        row.pop("attrs", None)

        payload = {k: (str(v) if v is not None else "") for k, v in row.items()}
        vectors = {"text": text_vectors[i] if text_vectors[i] else []}
        if image_vectors[i]:
            vectors["image"] = image_vectors[i]

        points.append(PointStruct(id=point_id, vector=vectors, payload=payload))

    # 6. 批量上传
    logger.info("📤 开始批量上传至 Qdrant...")
    batch_size = 100
    for i in range(0, len(points), batch_size):
        batch = points[i:i + batch_size]
        client.upsert(collection_name="products", points=batch)
        logger.info(f"  上传进度: {min(i + batch_size, len(points))}/{len(points)}")

    logger.info("=" * 60)
    logger.info(f"🎉 入库完成！共 {len(points)} 条商品数据")
    logger.info("=" * 60)

    # 验证
    info = client.get_collection("products")
    logger.info(f"📊 Qdrant products collection: {info.points_count} 条数据")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="快速批量入库")
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--img-dir", default=DEFAULT_IMG_DIR)
    args = parser.parse_args()
    ingest_fast(args.csv, args.img_dir)
