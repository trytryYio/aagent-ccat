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
# 延迟导入并添加模块路径处理，解决导入错误问题
def refine_knowledge_base():
    from rag.scripts.refine_knowledge_base import refine_knowledge_base as _refine
    return _refine()

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 路径常量（绝对路径，避免 cwd 依赖）
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RAG_DIR = os.path.dirname(_SCRIPT_DIR)
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
            
    # 如果核心科技标签里有内容，也加入 techs
    core_tech_match = re.search(r"【核心科技】(.*?)[。|；|!|?|$]", description)
    if core_tech_match:
        techs_str = core_tech_match.group(1)
        # 拆分多个科技
        for t in re.split(r"、|，|,", techs_str):
            t = t.strip()
            if t and t not in attrs["techs"]:
                attrs["techs"].append(t)
                
    return attrs

def ingest_data(csv_path: str = DEFAULT_CSV, local_img_dir: str = DEFAULT_IMG_DIR, recreate: bool = False):
    """从 CSV 读取数据，向量化并导入 Qdrant"""
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
            description = row["description"]
            image_url = row["image_url"]

            logger.info(f"正在向量化商品: {name} ({product_id_raw})")

            point_id = generate_deterministic_uuid(product_id_raw)

            # 1. 文本向量化
            text_to_embed = f"{name}: {description}"
            text_vector = embed_text(text_to_embed)

            # 2. 图片向量化
            image_vector = []
            local_img_path = os.path.join(local_img_dir, f"{product_id_raw}.jpg")

            try:
                if os.path.exists(local_img_path):
                    with open(local_img_path, "rb") as img_file:
                        image_vector = embed_image(img_file.read())
                elif image_url.startswith("http"):
                    response = requests.get(image_url, timeout=10)
                    if response.status_code == 200:
                        image_vector = embed_image(response.content)
            except Exception as e:
                logger.warning(f"图片向量化失败: {product_id_raw}, 错误: {str(e)}")

            # 3. 提取属性并扁平化
            attrs = extract_attrs(description)
            row["attrs_techs"] = ",".join(attrs.get("techs", []))
            row["attrs_color"] = attrs.get("color", "未知")
            # 移除 attrs 嵌套（PointStruct payload 不支持嵌套 dict）
            row.pop("attrs", None)

            # 4. 构造 Qdrant Point（payload 必须是扁平的字符串字典）
            payload = {k: (str(v) if v is not None else "") for k, v in row.items()}
            vectors = {"text": text_vector}
            if image_vector:
                vectors["image"] = image_vector

            points.append(PointStruct(id=point_id, vector=vectors, payload=payload))

    # 批量上传
    if points:
        try:
            # 分批上传以防 OOM 或超时 (每批 20 条)
            batch_size = 20
            for i in range(0, len(points), batch_size):
                batch = points[i:i + batch_size]
                client.upsert(collection_name=collection_name, points=batch)
                logger.info(f"已上传第 {i//batch_size + 1} 批数据 ({len(batch)} 条)")
            
            logger.info(f"成功同步 {len(points)} 条商品数据至 Qdrant!")
            
            # 5. 自动触发知识库精细化处理 (Citations)
            logger.info("开始执行知识库精细化处理...")
            refine_knowledge_base()
            
        except Exception as e:
            logger.error(f"同步失败: {str(e)}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="商品数据向量化入库 Qdrant")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="products.csv 路径")
    parser.add_argument("--img-dir", default=DEFAULT_IMG_DIR, help="本地图片目录")
    parser.add_argument("--recreate", action="store_true", help="删除并重建 products 集合（含双向量）")
    args = parser.parse_args()
    ingest_data(args.csv, args.img_dir, recreate=args.recreate)
