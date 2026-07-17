"""端到端 RAG 流程测试：文本检索商品 → get_citations(rerank) → 打印。

验证：新数据(products+citations)召回 + 千问 rerank 是否工作。

用法:
  PYTHONPATH=$(pwd) python -m rag.scripts.eval.test_rag_flow
"""
import sys
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # rag/scripts/eval/
_RAG_DIR = os.path.dirname(os.path.dirname(_SCRIPT_DIR))  # rag/
_PROJECT_DIR = os.path.dirname(_RAG_DIR)
sys.path.insert(0, _PROJECT_DIR)

from rag.embedding import embed_text
from rag.text_retrieval import search_by_text, get_citations


def test_query(query: str, top_k: int = 5):
    print(f"\n{'='*60}\n查询: {query}\n{'='*60}")
    text_emb = embed_text(query)
    if not text_emb:
        print("  [错误] 文本向量化失败")
        return

    # 1. 商品召回
    products = search_by_text(text_emb, top_k=top_k)
    print(f"\n[商品召回 top{top_k}]")
    for p in products:
        print(f"  [{p.score:.3f}] {p.name[:35]} | {p.category or '-'} | {p.product_id}")

    if not products:
        print("  [警告] 无召回，确认 products 集合已入库")
        return

    # 2. citations + rerank（带 query 触发千问 rerank）
    sku_ids = [p.product_id for p in products[:3]]
    citations = get_citations(text_emb, sku_ids, top_k=5, query=query)
    print(f"\n[知识片段 citations（千问 rerank 后）共 {len(citations)} 条]")
    for c in citations:
        score = c.get("score", 0)
        tag = c.get("tag", "")
        content = str(c.get("content", ""))[:55]
        print(f"  [{score:.3f}] [{tag}] {content}")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    queries = [
        "专业羽毛球鞋减震回弹",
        "轻量透气的跑步鞋",
        "保护脚踝的篮球鞋",
        "日常穿搭舒适的休闲板鞋",
    ]
    for q in queries:
        test_query(q)
    print(f"\n{'='*60}\n测试完成\n{'='*60}")
