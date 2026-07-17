"""增强版数据摄入 - 支持详细信息向量化。

从 CSV 读取商品数据，包括详细信息（basic_info, introduction, detail_images），
向量化并导入 Qdrant。文本向量化时会包含丰富的详情信息。

用法:
  PYTHONPATH=$(pwd) python -m rag.scripts.pipeline.ingest_data_enhanced
  PYTHONPATH=$(pwd) python -m rag.scripts.pipeline.ingest_data_enhanced --csv rag/data/products.csv --recreate
"""

import os
import csv
import requests
import logging
import uuid
import re
from typing import Dict, List
from qdrant_client.models import PointStruct

from rag.db_client import get_qdrant_client
from rag.embedding import embed_text, embed_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 路径常量（本文件在 rag/scripts/pipeline/，需上溯两层到 rag/）
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RAG_DIR = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
DEFAULT_IMG_DIR = os.path.join(_RAG_DIR, "data", "images")
DEFAULT_CSV = os.path.join(_RAG_DIR, "data", "products.csv")


def generate_deterministic_uuid(input_str: str) -> str:
    """根据输入字符串生成确定性的 UUID"""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, input_str))


def extract_attrs(description: str) -> Dict:
    """从描述中提取结构化属性字典"""
    attrs = {
        "brand": "李宁",
        "color": "未知",
        "techs": []
    }
    
    # 提取颜色
    color_match = re.search(r"【颜色信息】(.*?)[。|；|!|?|$]", description)
    if color_match:
        attrs["color"] = color_match.group(1).strip()
    
    # 提取科技标签
    tech_matches = re.findall(r"【(.*?)】", description)
    for tech in tech_matches:
        if tech not in ["颜色信息", "性能卖点", "核心科技", "商品详情"]:
            attrs["techs"].append(tech)
            
    # 核心科技
    core_tech_match = re.search(r"【核心科技】(.*?)[。|；|!|?|$]", description)
    if core_tech_match:
        techs_str = core_tech_match.group(1)
        for t in re.split(r"、|，|,", techs_str):
            t = t.strip()
            if t and t not in attrs["techs"]:
                attrs["techs"].append(t)
                
    return attrs


def build_rich_text(row: Dict[str, str]) -> str:
    """构建丰富的文本用于向量化。
    
    包含：名称、描述、基础信息、介绍、类别等
    """
    parts = []
    
    # 名称
    if row.get("name"):
        parts.append(row["name"])
    
    # 描述
    if row.get("description"):
        parts.append(row["description"])
    
    # 基础信息 (basic_info)
    if row.get("basic_info"):
        parts.append(f"基础信息: {row['basic_info']}")
    
    # 介绍 (introduction)
    if row.get("introduction"):
        parts.append(f"产品介绍: {row['introduction']}")
    
    # 类别
    if row.get("category"):
        parts.append(f"类别: {row['category']}")
    
    # 性别
    if row.get("gender"):
        parts.append(f"性别: {row['gender']}")
    
    # 系列
    if row.get("series"):
        parts.append(f"系列: {row['series']}")
    
    return " ".join(parts)


def ingest_data_enhanced(
    csv_path: str = DEFAULT_CSV,
    local_img_dir: str = DEFAULT_IMG_DIR,
    recreate: bool = False,
):
    """增强版数据摄入 - 支持详细信息向量化"""
    client = get_qdrant_client()
    collection_name = "products"

    if recreate:
        try:
            client.delete_collection(collection_name)
            logger.info(f"已删除旧集合 {collection_name}")
        except Exception:
            pass
        from rag.db_client import QdrantManager
        QdrantManager().init_collection(collection_name)
        logger.info(f"已重建集合 {collection_name}（双向量 text+image）")

    if not os.path.exists(csv_path):
        logger.error(f"找不到数据文件: {csv_path}")
        return

    points = []
    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            product_id_raw = row["product_id"]
            name = row["name"]
            description = row.get("description", "")
            image_url = row.get("image_url", "")

            logger.info(f"正在向量化商品: {name[:40]}... ({product_id_raw})")

            point_id = generate_deterministic_uuid(product_id_raw)

            # 1. 构建丰富的文本用于向量化
            rich_text = build_rich_text(row)
            text_vector = embed_text(rich_text)

            # 2. 图片向量化
            image_vector = []
            local_img_path = os.path.join(local_img_dir, f"{product_id_raw}.jpg")

            try:
                if os.path.exists(local_img_path):
                    with open(local_img_path, "rb") as img_file:
                        image_vector = embed_image(img_file.read())
                elif image_url and image_url.startswith("http"):
                    # 去 imageMogr2 缩略图参数拿原图（CLIP 向量质量更好），失败再回退带参数 URL
                    raw_url = image_url.split("?")[0]
                    headers = {"User-Agent": "Mozilla/5.0"}
                    resp = None
                    try:
                        resp = requests.get(raw_url, timeout=15, headers=headers)
                    except Exception:
                        resp = None
                    if resp is None or resp.status_code != 200:
                        resp = requests.get(image_url, timeout=15, headers=headers)
                    if resp is not None and resp.status_code == 200:
                        image_vector = embed_image(resp.content)
            except Exception as e:
                logger.warning(f"图片向量化失败: {product_id_raw}, 错误: {str(e)}")

            # 3. 提取属性并扁平化
            attrs = extract_attrs(description)
            row["attrs_techs"] = ",".join(attrs.get("techs", []))
            row["attrs_color"] = attrs.get("color", "未知")
            row.pop("attrs", None)

            # 4. 构造 Qdrant Point
            payload = {k: (str(v) if v is not None else "") for k, v in row.items()}
            vectors = {"text": text_vector}
            if image_vector:
                vectors["image"] = image_vector

            points.append(PointStruct(id=point_id, vector=vectors, payload=payload))

    # 批量上传
    if points:
        try:
            batch_size = 20
            for i in range(0, len(points), batch_size):
                batch = points[i:i + batch_size]
                client.upsert(collection_name=collection_name, points=batch)
                logger.info(f"已上传第 {i//batch_size + 1} 批数据 ({len(batch)} 条)")
            
            logger.info(f"成功同步 {len(points)} 条商品数据至 Qdrant!")
            
            # 5. 自动触发知识库精细化处理
            logger.info("开始执行知识库精细化处理...")
            try:
                from rag.scripts.pipeline.refine_knowledge_base import refine_knowledge_base
                refine_knowledge_base()
            except Exception as e:
                logger.warning(f"知识库精细化处理失败: {e}")
            
        except Exception as e:
            logger.error(f"同步失败: {str(e)}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="增强版商品数据向量化入库（支持详细信息）")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="products.csv 路径")
    parser.add_argument("--img-dir", default=DEFAULT_IMG_DIR, help="本地图片目录")
    parser.add_argument("--recreate", action="store_true", help="删除并重建 products 集合")
    args = parser.parse_args()
    ingest_data_enhanced(args.csv, args.img_dir, recreate=args.recreate)
