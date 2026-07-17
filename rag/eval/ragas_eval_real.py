"""真实 RAG 数据链 RAGAS 评测。

数据链：用户 query → 阿里云 text-embedding-v3 检索商品 → 千问 gte-rerank-v2 重排 citations
       → LLM(deepseek) 生成答案 → RAGAS 四维指标。

与 ragas_eval.py 的区别：
- contexts 不再用内联旧知识，走真实 RAG（检索 + rerank）
- query/ground_truth 从新 products.csv 自动构造（适配新 50 商品）
- 全程用 .env 里的阿里云 key（embedding/rerank）+ deepseek key（生成/judge）

用法:
  PYTHONPATH=$(pwd) python rag/eval/ragas_eval_real.py
  PYTHONPATH=$(pwd) python rag/eval/ragas_eval_real.py --num-cases 10 --top-k 3
"""
import argparse
import csv
import json
import logging
import os
import re
import sys
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # rag/eval/
_RAG_DIR = os.path.dirname(_SCRIPT_DIR)  # rag/
_PROJECT_DIR = os.path.dirname(_RAG_DIR)  # Agent/
sys.path.insert(0, _PROJECT_DIR)
sys.path.insert(0, os.path.join(_PROJECT_DIR, "backend"))

# 手动加载 backend/.env
_ENV_PATH = os.path.join(_PROJECT_DIR, "backend", ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness, context_recall, context_precision
from openai import OpenAI

from rag.embedding import embed_text
from rag.text_retrieval import search_by_text, get_citations

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PRODUCTS_CSV = os.path.join(_RAG_DIR, "data", "products.csv")
OUTPUT_DIR = os.path.join(_RAG_DIR, "eval", "reports")
DATASET_OUT = os.path.join(_RAG_DIR, "eval", "datasets", "rag_eval_real.jsonl")  # 保存构造的数据集


def load_products_map() -> dict:
    """读 products.csv → {product_id: row}"""
    m = {}
    with open(PRODUCTS_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            m[row["product_id"]] = row
    return m


def build_cases(products_map: dict, num: int) -> list:
    """从 products.csv 构造评测用例：query=商品名去型号, ground_truth=介绍"""
    items = list(products_map.values())[:num]
    cases = []
    for i, row in enumerate(items, 1):
        name = row.get("name", "")
        # query: 去掉尾部型号，模拟用户按品名/特性搜
        query = re.sub(r"[A-Z]{2,4}\d{3,}[-\d]*$", "", name).strip() or name
        intro = (row.get("introduction") or row.get("description") or "").strip()
        cases.append({
            "test_id": f"case-{i:03d}",
            "query_text": query,
            "gold_sku_id": row["product_id"],
            "gold_name": name,
            "ground_truth": intro[:500] if intro else name,
            "category": row.get("category", ""),
        })
    return cases


def real_rag_contexts(query: str, top_k: int = 3) -> tuple:
    """真实 RAG：阿里云检索商品 + 千问 rerank citations"""
    emb = embed_text(query)
    if not emb:
        return [], []
    products = search_by_text(emb, top_k=top_k)
    sku_ids = [p.product_id for p in products]
    citations = get_citations(emb, sku_ids, top_k=5, query=query)  # ← 千问 rerank 在这里
    ctx = [str(c.get("content", "")) for c in citations if c.get("content")]
    return ctx, products


def get_llm() -> tuple:
    key = os.environ.get("LLM_API_KEY", "")
    base = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    if not key:
        raise ValueError("LLM_API_KEY 未配置")
    return OpenAI(api_key=key, base_url=base), model


def generate_answer(client, model, query, contexts):
    ctx_str = "\n".join(f"- {c}" for c in contexts)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是电商导购助手。根据商品信息回答用户需求，基于资料不编造，200字内。"},
            {"role": "user", "content": f"商品信息：\n{ctx_str}\n\n用户需求：{query}"},
        ],
        temperature=0.3, max_tokens=512,
    )
    return resp.choices[0].message.content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-cases", type=int, default=15)
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    products_map = load_products_map()
    logger.info(f"载入 {len(products_map)} 个商品")

    cases = build_cases(products_map, args.num_cases)
    logger.info(f"构造 {len(cases)} 个评测用例")

    # 保存数据集到文件
    os.makedirs(os.path.dirname(DATASET_OUT), exist_ok=True)
    with open(DATASET_OUT, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    logger.info(f"数据集已保存 → {DATASET_OUT}")

    client, model = get_llm()

    # 真实 RAG：检索 + rerank + 生成
    questions, answers, contexts_list, ground_truths = [], [], [], []
    for c in cases:
        ctx, products = real_rag_contexts(c["query_text"], top_k=args.top_k)
        answer = generate_answer(client, model, c["query_text"], ctx) if ctx else ""
        top_names = [p.name[:20] for p in products[:3]]
        logger.info(f"{c['test_id']} query={c['query_text'][:25]!r} 召回={top_names} ctx={len(ctx)} ans={len(answer)}")
        questions.append(c["query_text"])
        answers.append(answer)
        contexts_list.append(ctx)
        ground_truths.append(c["ground_truth"])

    # 保存每个用例的完整数据（query/召回/contexts/answer/ground_truth），供分析
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    details_path = os.path.join(OUTPUT_DIR, f"ragas_real_details_{run_id}.jsonl")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(details_path, "w", encoding="utf-8") as f:
        for c, ans, ctx in zip(cases, answers, contexts_list):
            rec = dict(c)
            rec["retrieved_contexts"] = ctx
            rec["answer"] = ans
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info(f"用例明细已保存 → {details_path}")

    # RAGAS 评测
    dataset = Dataset.from_dict({
        "question": questions,
        "answer": answers,
        "contexts": contexts_list,
        "ground_truth": ground_truths,
    })
    from langchain_openai import ChatOpenAI
    evaluator_llm = ChatOpenAI(
        model=model, api_key=client.api_key,
        base_url=str(client.base_url) if client.base_url else None,
        max_tokens=8192, temperature=0.0, timeout=120, max_retries=3,
    )
    result = evaluate(dataset, metrics=[faithfulness, context_recall, context_precision],
                      llm=evaluator_llm, batch_size=1)

    def avg(k):
        v = result[k]
        return float(sum(v) / len(v)) if len(v) else 0.0

    report = {
        "run_id": run_id, "pipeline": "real-rag (阿里云embedding + 千问rerank)",
        "num_cases": len(cases), "top_k": args.top_k,
        "metrics": {
            "faithfulness": avg("faithfulness"),
            "context_recall": avg("context_recall"),
            "context_precision": avg("context_precision"),
        },
        "dataset": DATASET_OUT, "details": details_path,
    }
    report_path = os.path.join(OUTPUT_DIR, f"ragas_real_report_{run_id}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    md = [
        f"# 真实 RAG RAGAS 报告 ({run_id})", "",
        f"数据链：阿里云 text-embedding-v3 检索 + 千问 gte-rerank-v2 重排 + deepseek 生成", "",
        "| 指标 | 得分 | 说明 |", "|------|------|------|",
        f"| **Faithfulness** | {report['metrics']['faithfulness']:.3f} | 答案忠于检索内容 |",
        f"| **Context Recall** | {report['metrics']['context_recall']:.3f} | 检索覆盖率 |",
        f"| **Context Precision** | {report['metrics']['context_precision']:.3f} | 排序质量(rerank核心指标) |",
        "", f"用例数: {len(cases)} | top_k商品: {args.top_k}", "",
        f"数据集: `{DATASET_OUT}`", f"用例明细: `{details_path}`",
    ]
    with open(os.path.join(OUTPUT_DIR, f"ragas_real_report_{run_id}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    logger.info("=== RAGAS 真实RAG 结果 ===")
    for k, v in report["metrics"].items():
        logger.info(f"  {k}: {v:.3f}")
    logger.info(f"报告 → {report_path}")


if __name__ == "__main__":
    main()
