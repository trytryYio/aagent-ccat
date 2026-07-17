import os
import csv
import logging
import uuid
import re
from typing import List, Dict
from qdrant_client.models import PointStruct
from rag.db_client import get_qdrant_client
from rag.embedding import embed_text_batch

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 配置路径（本文件在 rag/scripts/pipeline/，需上溯两层到 rag/）
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RAG_DIR = os.path.dirname(os.path.dirname(_SCRIPT_DIR))  # rag/
DATA_DIR = os.path.join(_RAG_DIR, "data")
PRODUCTS_CSV = os.path.join(DATA_DIR, "products.csv")

def generate_deterministic_uuid(input_str: str) -> str:
    """根据输入字符串生成确定性的 UUID"""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, input_str))

def chunk_knowledge(name: str, introduction: str, basic_info: str, description: str) -> List[Dict]:
    """
    切分知识片段（语义聚合方案，避免无意义碎片）。
    1. 【标签】格式（旧结构化描述兼容）
    2. 基础信息整体 1 段（保留完整属性语义，避免 2026Q1/EVA 等字段碎片）
    3. 商品介绍：√ 规格项合并成「规格」段 + 剩余按句子切成「介绍」段
    4. 兜底：description 按分隔符
    过滤：<8 字的碎片丢弃。
    """
    chunks: List[Dict] = []
    seen = set()

    def add(tag: str, content: str):
        content = (content or "").strip().rstrip("。；").strip()
        if len(content) < 8:  # 过滤无语义短碎片
            return
        if content in seen:
            return
        seen.add(content)
        chunks.append({"tag": tag, "content": content})

    # 1. 【标签】格式（旧数据兼容）
    for tag, c in re.findall(r"【(.*?)】(.*?)(?=【|$)", description, re.DOTALL):
        add(tag, c)
    if chunks:
        return chunks

    # 2. 基础信息整体一段（完整属性，有语义，避免字段碎片）
    if basic_info and basic_info.strip():
        add("基础信息", basic_info.strip())

    # 3. 商品介绍：√ 规格项合并 + 句子切分
    if introduction:
        specs = re.findall(r"√[^√\n。！？；]*", introduction)
        if specs:
            add("规格", " ".join(s.strip() for s in specs))
        intro_no_spec = re.sub(r"√[^√\n。！？；]*", "", introduction)
        for sent in re.split(r"[。！？!?；;\n]+", intro_no_spec):
            add("介绍", sent)

    # 4. 兜底
    if not chunks:
        for feat in re.split(r"[、,，/;；]+", description or ""):
            add("特性", feat)

    return chunks

def refine_knowledge_base():
    """
    读取 products.csv，对描述进行切分并存入 citations 集合
    """
    client = get_qdrant_client()
    collection_name = "citations"

    if not os.path.exists(PRODUCTS_CSV):
        logger.error(f"找不到数据文件: {PRODUCTS_CSV}")
        return

    # 第一遍：切分并收集所有片段（先不向量化）
    records = []
    with open(PRODUCTS_CSV, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            product_id = row["product_id"]
            name = row["name"]
            chunks = chunk_knowledge(name, row.get("introduction", ""), row.get("basic_info", ""), row.get("description", ""))
            logger.info(f"商品 {product_id} 切分为 {len(chunks)} 个知识片段")
            for chunk in chunks:
                text_to_embed = f"{name} {chunk['tag']}: {chunk['content']}"
                records.append({
                    "product_id": product_id,
                    "name": name,
                    "tag": chunk["tag"],
                    "content": chunk["content"],
                    "full_text": text_to_embed,
                    "point_id": generate_deterministic_uuid(f"{product_id}_{chunk['tag']}"),
                })

    if not records:
        logger.warning("无知识片段可入库")
        return

    # 第二遍：批量向量化（阿里云 text-embedding-v3，比逐条快很多）
    logger.info(f"批量向量化 {len(records)} 条知识片段...")
    vectors = embed_text_batch([r["full_text"] for r in records])

    points = []
    for r, vec in zip(records, vectors):
        if not vec:
            continue
        points.append(PointStruct(
            id=r["point_id"],
            vector={"text": vec},
            payload={k: r[k] for k in ("product_id", "name", "tag", "content", "full_text")},
        ))

    # 批量上传
    if points:
        try:
            # 分批上传
            batch_size = 50
            for i in range(0, len(points), batch_size):
                batch = points[i:i + batch_size]
                client.upsert(collection_name=collection_name, points=batch)
                logger.info(f"已上传第 {i//batch_size + 1} 批知识引用 ({len(batch)} 条)")
            
            logger.info(f"成功同步 {len(points)} 条知识引用至 citations 集合!")
        except Exception as e:
            logger.error(f"知识引用同步失败: {str(e)}")

if __name__ == "__main__":
    refine_knowledge_base()
