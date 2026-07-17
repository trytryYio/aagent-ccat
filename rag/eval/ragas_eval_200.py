"""200 商品数据集 RAGAS 评测。

使用 200.csv 作为商品库，构造评测用例并运行 RAGAS 四维指标。

用法:
  PYTHONPATH=$(pwd) backend/.venv/bin/python3 rag/eval/ragas_eval_200.py --num-cases 20 --top-k 3
"""
import argparse
import csv
import json
import logging
import os
import re
import sys
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RAG_DIR = os.path.dirname(_SCRIPT_DIR)
_PROJECT_DIR = os.path.dirname(_RAG_DIR)
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
from ragas.metrics import faithfulness, context_recall, context_precision, answer_relevancy
from openai import OpenAI

from rag.embedding import embed_text
from rag.text_retrieval import search_by_text, get_citations

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# 使用 200.csv
PRODUCTS_CSV = os.path.join(_PROJECT_DIR, "200.csv")
OUTPUT_DIR = os.path.join(_RAG_DIR, "eval", "reports")
DATASET_OUT = os.path.join(_RAG_DIR, "eval", "datasets", "rag_eval_200.jsonl")


def load_products_map() -> dict:
    m = {}
    with open(PRODUCTS_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            m[row["product_id"]] = row
    return m


def split_ground_truth(intro: str) -> list:
    """把介绍拆成短句列表。"""
    if not intro:
        return []
    parts = re.split(r"[。！；\n]", intro)
    out = []
    for p in parts:
        p = p.strip()
        if len(p) >= 3:
            out.append(p)
    return out[:20]


def build_cases(products_map: dict, num: int = 20) -> list:
    """从 200.csv 构造评测用例。"""
    items = list(products_map.values())[:num]
    cases = []
    for i, row in enumerate(items, 1):
        name = row.get("name", "")
        # query: 去掉尾部型号
        query = re.sub(r"[A-Z]{2,4}\d{3,}[-\d]*$", "", name).strip() or name
        intro = (row.get("introduction") or row.get("description") or "").strip()
        cases.append({
            "test_id": f"case-{i:03d}",
            "query_text": query,
            "gold_sku_id": row["product_id"],
            "gold_name": name,
            "ground_truth": "\n".join(split_ground_truth(intro)),
            "category": row.get("category", ""),
        })
    return cases


class DashScopeEmbeddingWrapper:
    """用阿里云 DashScope text-embedding-v3 给 RAGAS 提供 embedding。"""

    def __init__(self, api_key: str = "", model: str = "text-embedding-v3"):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.model = model
        self.base = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def _request(self, texts: list) -> list:
        import requests
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "input": texts}
        resp = requests.post(f"{self.base}/embeddings", headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        data.sort(key=lambda x: x.get("index", 0))
        return [item.get("embedding", []) for item in data]

    def embed_documents(self, texts: list) -> list:
        return self._request(texts)

    def embed_query(self, text: str) -> list:
        return self._request([text])[0]


def get_embedding_judge():
    """构建 RAGAS 可用的 embedding judge。"""
    try:
        from langchain_core.embeddings import Embeddings

        class _CustomEmbeddings(Embeddings):
            def __init__(self):
                self._impl = DashScopeEmbeddingWrapper()

            def embed_documents(self, texts):
                return self._impl.embed_documents(texts)

            def embed_query(self, text):
                return self._impl.embed_query(text)

        from ragas.embeddings import LangchainEmbeddingsWrapper
        return LangchainEmbeddingsWrapper(_CustomEmbeddings())
    except Exception as e:
        logger.warning(f"embedding judge 初始化失败: {e}")
        return None


def real_rag_contexts(query: str, top_k: int = 3) -> tuple:
    """真实 RAG：阿里云检索商品 + 千问 rerank citations。"""
    emb = embed_text(query)
    if not emb:
        return [], []
    products = search_by_text(emb, top_k=top_k)
    sku_ids = [p.product_id for p in products]
    citations = get_citations(emb, sku_ids, top_k=5, query=query)
    ctx = [str(c.get("content", "")) for c in citations if c.get("content")]
    return ctx, products


def get_llm() -> tuple:
    """获取LLM客户端（使用统一配置）"""
    from rag.eval.eval_config import EvalConfig
    config = EvalConfig.get_config()
    if not config["api_key"]:
        raise ValueError("API Key 未配置")
    return OpenAI(api_key=config["api_key"], base_url=config["base_url"]), config["model"]


def generate_answer(client, model, query, contexts):
    ctx_str = "\n".join(f"- {c}" for c in contexts)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": """你是电商导购助手。你必须依据候选商品信息回答，不要编造。

输出要求（严格遵守）：
1. 禁止寒暄：不要说"您好"、"您关注的"等，直接回答商品信息
2. 禁止套话：不要说"如需更多细节，欢迎继续提问"等
3. 显式引用：提到商品名称并说明推荐理由
4. 不确定表达：信息不足时说"这部分信息我未能确认"
5. 不要编造：不要推荐候选列表之外的商品
6. 使用中文回答，简洁直接，200字以内"""},
            {"role": "user", "content": f"商品信息：\n{ctx_str}\n\n用户需求：{query}"},
        ],
        temperature=0.1, max_tokens=512,  # 降低 temperature 提升一致性
    )
    return resp.choices[0].message.content


def safe_float(v):
    import math
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 0.0
    try:
        return float(v)
    except Exception:
        return 0.0


def process_cases_batch(cases: list, client, model, top_k: int = 3) -> dict:
    """处理一批案例的 RAG 检索和生成"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def process_case(c):
        """处理单个案例"""
        ctx, products = real_rag_contexts(c["query_text"], top_k=top_k)
        answer = generate_answer(client, model, c["query_text"], ctx) if ctx else ""
        top_names = [p.name[:20] for p in products[:3]]
        logger.info(f"{c['test_id']} query={c['query_text'][:25]!r} 召回={top_names} ctx={len(ctx)} ans={len(answer)}")
        return c, answer, ctx

    questions, answers, contexts_list, ground_truths = [], [], [], []

    logger.info(f"开始多线程 RAG 处理（{len(cases)} 个案例，2 个并发）...")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(process_case, c): i for i, c in enumerate(cases)}
        results = [None] * len(cases)
        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()

    # 按原始顺序整理结果
    for c, answer, ctx in results:
        questions.append(c["query_text"])
        answers.append(answer)
        contexts_list.append(ctx)
        ground_truths.append(c["ground_truth"])

    return {
        "questions": questions,
        "answers": answers,
        "contexts": contexts_list,
        "ground_truths": ground_truths
    }


def run_ragas_evaluation(batch_results: dict, model: str) -> dict:
    """运行 RAGAS 评估并返回指标"""
    dataset = Dataset.from_dict({
        "question": batch_results["questions"],
        "answer": batch_results["answers"],
        "contexts": batch_results["contexts"],
        "ground_truth": batch_results["ground_truths"],
    })

    from langchain_openai import ChatOpenAI
    client, _ = get_llm()
    evaluator_llm = ChatOpenAI(
        model=model, api_key=client.api_key,
        base_url=str(client.base_url) if client.base_url else None,
        max_tokens=8192, temperature=0.0, timeout=180, max_retries=5,
        model_kwargs={"n": 1},
    )

    embedding_judge = get_embedding_judge()
    metrics = [faithfulness, context_recall, context_precision, answer_relevancy] if embedding_judge else [faithfulness, context_recall, context_precision]

    logger.info(f"开始 RAGAS 评测（{len(metrics)} 个指标，{len(batch_results['questions'])} 个用例，batch_size=2）...")
    result = evaluate(dataset, metrics=metrics, llm=evaluator_llm, embeddings=embedding_judge, batch_size=2)

    def avg(k):
        v = result[k]
        return sum(safe_float(x) for x in v) / len(v) if len(v) else 0.0

    return {
        "faithfulness": avg("faithfulness"),
        "context_recall": avg("context_recall"),
        "context_precision": avg("context_precision"),
        "answer_relevancy": avg("answer_relevancy") if embedding_judge else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-cases", type=int, default=200, help="评测用例数（默认 200，覆盖全部商品）")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    products_map = load_products_map()
    logger.info(f"载入 {len(products_map)} 个商品（200.csv）")

    cases = build_cases(products_map, args.num_cases)
    logger.info(f"构造 {len(cases)} 个评测用例")

    # 保存数据集
    os.makedirs(os.path.dirname(DATASET_OUT), exist_ok=True)
    with open(DATASET_OUT, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    logger.info(f"数据集已保存 → {DATASET_OUT}")

    client, model = get_llm()

    # 真实 RAG：检索 + rerank + 生成（多线程加速）
    def process_case(c):
        """处理单个案例"""
        ctx, products = real_rag_contexts(c["query_text"], top_k=args.top_k)
        answer = generate_answer(client, model, c["query_text"], ctx) if ctx else ""
        top_names = [p.name[:20] for p in products[:3]]
        logger.info(f"{c['test_id']} query={c['query_text'][:25]!r} 召回={top_names} ctx={len(ctx)} ans={len(answer)}")
        return c, answer, ctx

    # 使用多线程并行处理（降低并发避免超时）
    from concurrent.futures import ThreadPoolExecutor, as_completed
    questions, answers, contexts_list, ground_truths = [], [], [], []

    logger.info(f"开始多线程 RAG 处理（{len(cases)} 个案例，2 个并发）...")
    with ThreadPoolExecutor(max_workers=2) as executor:  # 降低到 2 并发
        futures = {executor.submit(process_case, c): i for i, c in enumerate(cases)}
        results = [None] * len(cases)
        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()

    # 按原始顺序整理结果
    for c, answer, ctx in results:
        questions.append(c["query_text"])
        answers.append(answer)
        contexts_list.append(ctx)
        ground_truths.append(c["ground_truth"])

    # 保存明细
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    details_path = os.path.join(OUTPUT_DIR, f"ragas_200_details_{run_id}.jsonl")
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
        max_tokens=8192, temperature=0.0, timeout=180, max_retries=5,  # 增加 timeout 和 retries
        model_kwargs={"n": 1},  # DeepSeek 只支持 n=1
    )

    embedding_judge = get_embedding_judge()
    metrics = [faithfulness, context_recall, context_precision, answer_relevancy] if embedding_judge else [faithfulness, context_recall, context_precision]

    logger.info(f"开始 RAGAS 评测（{len(metrics)} 个指标，{len(cases)} 个用例，batch_size=2）...")
    result = evaluate(dataset, metrics=metrics, llm=evaluator_llm, embeddings=embedding_judge, batch_size=2)  # 降低到 2

    def avg(k):
        v = result[k]
        return sum(safe_float(x) for x in v) / len(v) if len(v) else 0.0

    report = {
        "run_id": run_id,
        "pipeline": "real-rag (200.csv, 阿里云embedding + 千问rerank + deepseek生成)",
        "num_cases": len(cases), "top_k": args.top_k,
        "metrics": {
            "faithfulness": avg("faithfulness"),
            "context_recall": avg("context_recall"),
            "context_precision": avg("context_precision"),
            "answer_relevancy": avg("answer_relevancy") if embedding_judge else None,
        },
        "dataset": DATASET_OUT, "details": details_path,
    }
    report_path = os.path.join(OUTPUT_DIR, f"ragas_200_report_{run_id}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    md = [
        f"# 200 商品 RAGAS 报告 ({run_id})", "",
        f"数据链：200.csv + 阿里云 text-embedding-v3 检索 + 千问 gte-rerank-v2 重排 + deepseek 生成", "",
        "| 指标 | 得分 |", "|------|------|",
        f"| **Faithfulness** | {report['metrics']['faithfulness']:.3f} |",
        f"| **Context Recall** | {report['metrics']['context_recall']:.3f} |",
        f"| **Context Precision** | {report['metrics']['context_precision']:.3f} |",
    ]
    if report['metrics']['answer_relevancy'] is not None:
        md.append(f"| **Answer Relevancy** | {report['metrics']['answer_relevancy']:.3f} |")
    else:
        md.append("| **Answer Relevancy** | skipped |")
    md += ["", f"测试用例数: {report['num_cases']}", ""]

    md_path = os.path.join(OUTPUT_DIR, f"ragas_200_report_{run_id}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    logger.info("=== RAGAS 200商品 评测结果 ===")
    for k, v in report["metrics"].items():
        if v is not None:
            logger.info(f"  {k}: {v:.3f}")
        else:
            logger.info(f"  {k}: skipped")
    logger.info(f"报告 → {report_path}")


if __name__ == "__main__":
    main()
