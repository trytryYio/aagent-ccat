"""真实 RAG 数据链 RAGAS 评测（v2）。

数据链：用户 query → 阿里云 text-embedding-v3 检索商品 → 千问 gte-rerank-v2 重排 citations
       → LLM(deepseek) 生成答案 → RAGAS 四维指标。

改进：
- ground_truth 改短句列表，避免 Context Precision NaN。
- 增加 Answer Relevancy 评估（使用阿里云 text-embedding-v3 作为 embedding judge）。
- 支持 image/hybrid 模式，可用 --with-image 开启（需数据集含 image_path）。

用法:
  PYTHONPATH=$(pwd) python rag/eval/ragas_eval_real.py
  PYTHONPATH=$(pwd) python rag/eval/ragas_eval_real.py --num-cases 10 --top-k 3 --with-image
"""
import argparse
import csv
import json
import logging
import os
import re
import sys
from datetime import datetime

from openai import OpenAI
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness, context_recall, context_precision, answer_relevancy
from ragas.embeddings import LangchainEmbeddingsWrapper
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RAG_DIR = os.path.dirname(_SCRIPT_DIR)
_PROJECT_DIR = os.path.dirname(_RAG_DIR)
sys.path.insert(0, _PROJECT_DIR)
sys.path.insert(0, os.path.join(_PROJECT_DIR, "backend"))

_ENV_PATH = os.path.join(_PROJECT_DIR, "backend", ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PRODUCTS_CSV = os.path.join(_RAG_DIR, "data", "products.csv")
OUTPUT_DIR = os.path.join(_RAG_DIR, "eval", "reports")
DATASET_OUT = os.path.join(_RAG_DIR, "eval", "datasets", "rag_eval_real.jsonl")


def load_products_map() -> dict:
    m = {}
    with open(PRODUCTS_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            m[row["product_id"]] = row
    return m


def build_cases(products_map: dict, num: int = 15) -> list:
    items = list(products_map.values())[:num]
    cases = []
    for i, row in enumerate(items, 1):
        name = row.get("name", "")
        query = re.sub(r"[A-Z]{2,4}\d{3,}[-\d]*$", "", name).strip() or name
        intro = (row.get("introduction") or row.get("description") or "").strip()
        cases.append({
            "test_id": f"case-{i:03d}",
            "query_text": query,
            "gold_sku_id": row["product_id"],
            "gold_name": name,
            "ground_truth": split_ground_truth(intro),
            "category": row.get("category", ""),
            "image_path": "",
        })
    return cases


def split_ground_truth(intro: str) -> list:
    """把介绍拆成短句列表，过滤空行和过短碎片。"""
    if not intro:
        return []
    # 按 。！；\n 拆分（注意这里的句号、感叹号是中文标点）
    parts = re.split(r"[。！；\n]", intro)
    out = []
    for p in parts:
        p = p.strip()
        if len(p) >= 3:
            out.append(p)
    return out[:20]


class DashScopeEmbeddingWrapper:
    """用阿里云 DashScope text-embedding-v3 给 RAGAS AnswerRelevancy 提供 embedding。
    兼容 langchain_core.embeddings.Embeddings 接口，以便直接给 RAGAS 使用。
    """

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
    """构建 RAGAS 可用的 embedding judge（AnswerRelevancy 指标需要）。"""
    try:
        # 优先用 langchain_community 的 DashScope 集成
        from langchain_community.embeddings import DashScopeEmbeddings
        wrapper = DashScopeEmbeddings(
            model="text-embedding-v3",
            dashscope_api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        )
        from ragas.embeddings import LangchainEmbeddingsWrapper
        return LangchainEmbeddingsWrapper(wrapper)
    except ImportError:
        logger.info("langchain_community DashScope 不可用，使用自定义 wrapper")
        # 回退：自定义 wrapper（兼容 langchain_core Embeddings 接口）
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


def real_rag_contexts(query: str, image_path: str = "", top_k: int = 3) -> tuple:
    """真实 RAG：可选 image + text hybrid，否则 text only。"""
    from rag.embedding import embed_text, embed_image
    from rag.hybrid_search import hybrid_search
    from rag.text_retrieval import search_by_text, get_citations

    text_emb = embed_text(query)
    image_emb = None
    if image_path:
        image_path_full = os.path.join(_PROJECT_DIR, image_path)
        if os.path.exists(image_path_full):
            image_emb = embed_image(open(image_path_full, "rb").read())

    if image_emb is not None:
        products = hybrid_search(text_embedding=text_emb, image_embedding=image_emb, query=query, top_k=top_k)
    else:
        products = search_by_text(text_emb, top_k=top_k)

    sku_ids = [p.product_id for p in products]
    citations = get_citations(text_emb, sku_ids, top_k=5, query=query)
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


def safe_float(v):
    import math
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 0.0
    try:
        return float(v)
    except Exception:
        return 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-cases", type=int, default=15)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--with-image", action="store_true", help="如果 dataset 有 image_path，走 hybrid 检索")
    args = parser.parse_args()

    products_map = load_products_map()
    logger.info(f"载入 {len(products_map)} 个商品")

    cases = build_cases(products_map, args.num_cases)
    logger.info(f"构造 {len(cases)} 个评测用例")

    os.makedirs(os.path.dirname(DATASET_OUT), exist_ok=True)
    with open(DATASET_OUT, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    logger.info(f"数据集已保存 → {DATASET_OUT}")

    client, model = get_llm()

    questions, answers, contexts_list, ground_truths = [], [], [], []
    for c in cases:
        ctx, products = real_rag_contexts(c["query_text"], image_path=c.get("image_path", "") if args.with_image else "", top_k=args.top_k)
        answer = generate_answer(client, model, c["query_text"], ctx) if ctx else ""
        top_names = [p.name[:20] for p in products[:3]]
        logger.info(f"{c['test_id']} query={c['query_text'][:25]!r} 召回={top_names} ctx={len(ctx)} ans={len(answer)}")
        questions.append(c["query_text"])
        answers.append(answer)
        contexts_list.append(ctx)
        ground_truths.append("\n".join(c["ground_truth"]))

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

    dataset = Dataset.from_dict({
        "question": questions,
        "answer": answers,
        "contexts": contexts_list,
        "ground_truth": ground_truths,
    })

    from langchain_openai import ChatOpenAI
    from ragas import evaluate
    from ragas.metrics import faithfulness, context_recall, context_precision, answer_relevancy

    evaluator_llm = ChatOpenAI(
        model=model, api_key=client.api_key,
        base_url=str(client.base_url) if client.base_url else None,
        max_tokens=8192, temperature=0.0, timeout=120, max_retries=3,
        model_kwargs={"n": 1},  # DeepSeek 只支持 n=1
    )

    embedding_judge = None
    try:
        embedding_judge = get_embedding_judge()
        answer_relevancy.embeddings = embedding_judge
    except Exception as e:
        logger.warning(f"embedding judge 未就绪，Answer Relevancy 将跳过: {e}")

    metrics = [faithfulness, context_recall, context_precision, answer_relevancy] if embedding_judge else [faithfulness, context_recall, context_precision]
    result = evaluate(dataset, metrics=metrics, llm=evaluator_llm, batch_size=1)

    def avg(k):
        v = result[k]
        return sum(safe_float(x) for x in v) / len(v) if len(v) else 0.0

    report = {
        "run_id": run_id,
        "pipeline": "real-rag (阿里云embedding + hybrid可选 + 千问rerank + deepseek生成)",
        "num_cases": len(cases), "top_k": args.top_k,
        "metrics": {
            "faithfulness": avg("faithfulness"),
            "context_recall": avg("context_recall"),
            "context_precision": avg("context_precision"),
            "answer_relevancy": avg("answer_relevancy") if embedding_judge else None,
        },
        "dataset": DATASET_OUT, "details": details_path,
    }
    report_path = os.path.join(OUTPUT_DIR, f"ragas_real_report_{run_id}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    md = [
        f"# 真实 RAG RAGAS 报告 ({run_id})", "",
        f"数据链：阿里云 text-embedding-v3 检索 + hybrid可选 + 千问 gte-rerank-v2 重排 + deepseek 生成", "",
        f"| 指标 | 得分 |", "|------|------|",
        f"| **Faithfulness** | {report['metrics']['faithfulness']:.3f} |",
        f"| **Context Recall** | {report['metrics']['context_recall']:.3f} |",
        f"| **Context Precision** | {report['metrics']['context_precision']:.3f} |",
    ]
    if report['metrics']['answer_relevancy'] is not None:
        md.append(f"| **Answer Relevancy** | {report['metrics']['answer_relevancy']:.3f} |")
    else:
        md.append("| **Answer Relevancy** | skipped |")
    md += ["", f"测试用例数: {report['num_cases']}", ""]

    md_path = os.path.join(OUTPUT_DIR, f"ragas_real_report_{run_id}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    logger.info("=== RAGAS 真实RAG v2 结果 ===")
    for k, v in report["metrics"].items():
        if v is not None:
            logger.info(f"  {k}: {v:.3f}")
        else:
            logger.info(f"  {k}: skipped")
    logger.info(f"报告 → {report_path}")


if __name__ == "__main__":
    main()
