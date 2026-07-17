"""从详情缓存更新 products.csv。

读取爬虫 v3 的详情缓存 JSON，将 basic_info、introduction、detail_images 等详细信息
合并到 products.csv 中，为 RAG 向量化提供丰富的文本信息。

用法:
  PYTHONPATH=$(pwd) python -m rag.scripts.pipeline.update_csv_from_details
  PYTHONPATH=$(pwd) python -m rag.scripts.pipeline.update_csv_from_details --detail-cache rag/scripts/data/lining_detail_cache.json
"""

import argparse
import csv
import json
import logging
import os
from typing import Dict, List, Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 路径常量
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RAG_DIR = os.path.dirname(_SCRIPT_DIR)
_DATA_DIR = os.path.join(_RAG_DIR, "data")
DEFAULT_CSV = os.path.join(_DATA_DIR, "products.csv")
DEFAULT_DETAIL_CACHE = os.path.join(_DATA_DIR, "lining_detail_cache.json")
DEFAULT_LIST_POOL = os.path.join(_DATA_DIR, "lining_list_pool.json")


def load_detail_cache(cache_path: str) -> Dict[str, Dict[str, Any]]:
    """加载详情缓存 JSON"""
    if not os.path.exists(cache_path):
        logger.warning(f"详情缓存不存在: {cache_path}")
        return {}
    
    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    logger.info(f"加载详情缓存: {len(data)} 条")
    return data


def load_list_pool(pool_path: str) -> List[Dict[str, Any]]:
    """加载列表池 JSON"""
    if not os.path.exists(pool_path):
        logger.warning(f"列表池不存在: {pool_path}")
        return []
    
    with open(pool_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    logger.info(f"加载列表池: {len(data)} 条")
    return data


def enrich_product_from_detail(
    product: Dict[str, str],
    detail: Dict[str, Any],
    list_item: Dict[str, Any] = None,
) -> Dict[str, str]:
    """用详情信息丰富产品数据"""
    enriched = product.copy()
    
    # 基础信息 (basic_info)
    if detail.get("basic_info"):
        enriched["basic_info"] = detail["basic_info"]
    
    # 介绍 (introduction)
    if detail.get("introduction"):
        enriched["introduction"] = detail["introduction"]
    
    # 详情图片 (detail_images) - 转为逗号分隔的字符串
    if detail.get("detail_image_urls"):
        enriched["detail_images"] = ",".join(detail["detail_image_urls"])
    
    # 来源 URL
    if detail.get("source_url"):
        enriched["source_url"] = detail["source_url"]
    
    # 从列表池补充信息
    if list_item:
        enriched["gender"] = list_item.get("gender", "")
        enriched["series"] = list_item.get("series", "")
        enriched["spu_id"] = list_item.get("spuId", "")
    
    return enriched


def update_csv(
    csv_path: str,
    detail_cache: Dict[str, Dict[str, Any]],
    list_pool: List[Dict[str, Any]],
) -> int:
    """更新 CSV 文件，返回更新数量"""
    if not os.path.exists(csv_path):
        logger.error(f"CSV 文件不存在: {csv_path}")
        return 0
    
    # 读取现有 CSV
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames
    
    # 构建 spu_id -> list_item 映射
    spu_map = {item.get("spuId"): item for item in list_pool}
    
    # 更新每行
    updated_count = 0
    new_fieldnames = set(fieldnames) if fieldnames else set()
    
    for row in rows:
        spu_id = row.get("spu_id", "")
        if spu_id and spu_id in detail_cache:
            detail = detail_cache[spu_id]
            list_item = spu_map.get(spu_id)
            enriched = enrich_product_from_detail(row, detail, list_item)
            
            # 更新 row
            for key, value in enriched.items():
                if value:
                    row[key] = str(value)
                    new_fieldnames.add(key)
            
            updated_count += 1
    
    # 写回 CSV
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(new_fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    
    logger.info(f"更新 CSV 完成: {updated_count}/{len(rows)} 条")
    return updated_count


def main():
    parser = argparse.ArgumentParser(description="从详情缓存更新 products.csv")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="products.csv 路径")
    parser.add_argument("--detail-cache", default=DEFAULT_DETAIL_CACHE, help="详情缓存 JSON 路径")
    parser.add_argument("--list-pool", default=DEFAULT_LIST_POOL, help="列表池 JSON 路径")
    args = parser.parse_args()
    
    detail_cache = load_detail_cache(args.detail_cache)
    list_pool = load_list_pool(args.list_pool)
    
    if not detail_cache:
        logger.error("无详情缓存数据，请先运行爬虫 v3")
        return
    
    updated = update_csv(args.csv, detail_cache, list_pool)
    logger.info(f"共更新 {updated} 条商品数据")


if __name__ == "__main__":
    main()
