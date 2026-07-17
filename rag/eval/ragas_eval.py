"""RAGAS 评估流水线：三阶段（准备数据 → 生成回答 → 四维指标计算）

用法：
  # 自包含模式：自动用 LLM 生成答案 + 上下文
  python rag/eval/ragas_eval.py

  # 指定数据集和输出目录
  python rag/eval/ragas_eval.py --dataset path/to/dataset.jsonl --output-dir path/to/reports
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime

# 添加项目根目录和 backend/ 到路径
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # rag/eval/
_RAG_DIR = os.path.dirname(_SCRIPT_DIR)  # rag/
_PROJECT_DIR = os.path.dirname(_RAG_DIR)  # Agent/
sys.path.insert(0, _PROJECT_DIR)
_BACKEND_DIR = os.path.join(_PROJECT_DIR, "backend")
sys.path.insert(0, _BACKEND_DIR)

from datasets import Dataset
from ragas import evaluate
# 旧版 import 路径直接提供 metric 实例对象（兼容 ragas 0.4.x）
from ragas.metrics import faithfulness, context_recall, context_precision
from openai import OpenAI

# 手动加载 .env（pydantic_settings 的 env_file 路径是相对 cwd 的）
_ENV_PATH = os.path.join(_PROJECT_DIR, "backend", ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from rag.eval.config import ragas_settings
from app.config import settings as app_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 内联产品知识库（与 eval 数据集中的 gold_sku_id 对应） ──────────────
# 评估时作为检索上下文使用，模拟真实 RAG 的 retrieved_contexts
_PRODUCT_KNOWLEDGE: dict[str, list[str]] = {
    "lining_001": [
        "李宁 雷霆80 羽毛球鞋，全掌碳板设计，回弹强劲，适合专业比赛",
        "鞋面采用 MONO 纱材质，透气耐磨，中底李宁䨻科技提供出色能量回馈",
        "鞋底搭载 GCU 地面控制系统，止滑性能提升 40%，适合快速变向",
        "适合高弓足、正常足弓选手，建议偏小半码购买",
        "定位：高端专业比赛鞋，适合单打/双打全能型选手",
    ],
    "lining_002": [
        "李宁 贴地飞行 2 SE 羽毛球鞋，后跟环绕 TPU 稳定结构",
        "鞋底采用多向纹理 + 橡胶贴片，抓地力出色，急停启动不打滑",
        "中底李宁云科技提供均衡缓震，前后掌过渡流畅",
        "鞋楦偏宽适合宽脚选手，包裹感良好",
        "定位：中高端训练/比赛鞋，适合追求稳定性的选手",
    ],
    "lining_003": [
        "李宁 变色龙 羽毛球鞋，高性价比入门训练鞋",
        "鞋底耐磨橡胶 + 多向纹路设计，防滑耐用适合高强度训练",
        "鞋面网布材质透气舒适，中底 EVA 泡棉提供基础缓震",
        "适合低足弓、正常足弓选手，尺码标准",
        "定位：入门级训练鞋，价格亲民适合学生/业余爱好者",
    ],
    "lining_004": [
        "李宁 闪击 羽毛球鞋，耐磨防滑训练鞋，性价比高",
        "鞋头加厚防撞设计，鞋面织物 + PU 贴合提升耐用性",
        "中底轻质 EVA 缓震，后跟隐形 TPU 稳定支撑",
        "外底橡胶厚度达 4mm，适合频繁训练的业余选手",
        "定位：中端训练鞋，耐磨耐穿适合长期使用",
    ],
    "lining_005": [
        "李宁 锋影 羽毛球鞋，启动快、场地感清晰的比赛鞋",
        "鞋面轻薄贴合，前掌李宁䨻科技提升启动响应速度",
        "鞋底分离式大底设计减轻重量，弯折灵活",
        "适合窄脚、正常足弓选手，包裹感极强建议大半码",
        "定位：中高端速度型比赛鞋，适合双打前场快速连接",
    ],
}


def load_dataset(path: str) -> list[dict]:
    """加载 JSONL 评测数据集"""
    cases = []
    if not os.path.exists(path):
        logger.warning("Dataset not found: %s", path)
        return cases
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def enrich_cases_with_context(cases: list[dict]) -> list[dict]:
    """为没有 retrieved_contexts 的用例补充内联知识上下文"""
    enriched = []
    for c in cases:
        case = dict(c)
        if not case.get("retrieved_contexts"):
            sku = case.get("gold_sku_id", "")
            contexts = []
            # 如果 gold_sku_id 是单个 SKU
            if sku in _PRODUCT_KNOWLEDGE:
                contexts = _PRODUCT_KNOWLEDGE[sku]
            # 尝试 gold_topk 列表
            for topk_sku in case.get("gold_topk", []):
                if topk_sku in _PRODUCT_KNOWLEDGE and topk_sku != sku:
                    contexts.extend(_PRODUCT_KNOWLEDGE[topk_sku])
            if not contexts:
                contexts = [f"商品 {sku} 的相关产品信息"]
            case["retrieved_contexts"] = contexts
        enriched.append(case)
    return enriched


def generate_answers(cases: list[dict], llm_client: OpenAI, llm_model: str) -> list[dict]:
    """为没有 answer 的用例调用 LLM 生成回答"""
    enriched = []
    for c in cases:
        case = dict(c)
        if case.get("answer"):
            enriched.append(case)
            continue

        query = case.get("query_text", "")
        contexts = case.get("retrieved_contexts", [])

        context_str = "\n".join(f"- {ctx}" for ctx in contexts)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个电商导购助手。请根据以下商品信息和用户需求，生成推荐回答。\n"
                    "要求：\n"
                    "1. 回答必须基于提供的商品信息，不要编造\n"
                    "2. 如果信息不足，如实告知\n"
                    "3. 回答简洁自然，200字以内"
                ),
            },
            {
                "role": "user",
                "content": f"商品信息：\n{context_str}\n\n用户需求：{query}",
            },
        ]

        resp = llm_client.chat.completions.create(
            model=llm_model,
            messages=messages,
            temperature=0.3,
            max_tokens=512,
        )
        answer = resp.choices[0].message.content
        case["answer"] = answer
        logger.info("Generated answer for %s (%d tokens)", case.get("test_id", "?"), resp.usage.total_tokens)
        enriched.append(case)
    return enriched


def _sku_to_text(sku: str) -> str:
    """将 SKU ID 转为产品知识文本（RAGAS 的 ground_truth 需要文本）"""
    chunks = _PRODUCT_KNOWLEDGE.get(sku, [])
    return "；".join(chunks) if chunks else sku


def convert_to_ragas_format(cases: list[dict]) -> dict:
    """将评测用例转换为 RAGAS 需要的格式"""
    questions = []
    answers = []
    contexts = []
    ground_truths = []

    for c in cases:
        questions.append(c.get("query_text", ""))
        answers.append(c.get("answer", ""))
        contexts.append(c.get("retrieved_contexts", []))
        # ground_truth 需要文本而非 SKU ID
        sku = c.get("gold_sku_id", "")
        ground_truths.append(_sku_to_text(sku))

    return {
        "question": questions,
        "answer": answers,
        "contexts": contexts,
        "ground_truth": ground_truths,
    }


def get_llm_client() -> tuple[OpenAI, str]:
    """获取 LLM 客户端和模型名"""
    api_key = ragas_settings.eval_llm_api_key or app_settings.llm_api_key
    base_url = ragas_settings.eval_llm_base_url or app_settings.llm_base_url
    model = ragas_settings.eval_llm_model or app_settings.llm_model

    # 尝试从环境变量读取（覆盖 pydantic_settings 未加载的情况）
    if not api_key:
        api_key = os.environ.get("LLM_API_KEY", "")
    if not base_url:
        base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")

    if not api_key:
        logger.error("No API key found. Set LLM_API_KEY in .env or environment.")
        raise ValueError("Missing LLM_API_KEY")

    client = OpenAI(api_key=api_key, base_url=base_url)
    return client, model


def _make_evaluator_llm(llm_client: OpenAI, llm_model: str):
    """创建 RAGAS 评估 LLM，设置充足 token 上限"""
    from langchain_openai import ChatOpenAI

    lc_chat = ChatOpenAI(
        model=llm_model,
        api_key=llm_client.api_key,
        base_url=str(llm_client.base_url) if llm_client.base_url else None,
        max_tokens=8192,
        temperature=0.0,
        timeout=120,
        max_retries=3,
    )
    return lc_chat


def run_ragas_eval(dataset_path: str, output_dir: str, auto_generate: bool = True) -> dict:
    """执行 RAGAS 评估

    Args:
        dataset_path: JSONL 数据集路径
        output_dir: 报告输出目录
        auto_generate: 是否自动生成缺失的 answer 和 retrieved_contexts
    """
    # 1. 加载数据集
    cases = load_dataset(dataset_path)
    if not cases:
        logger.error("No cases loaded, aborting")
        return {"error": "empty dataset"}

    logger.info("Loaded %d evaluation cases", len(cases))

    # 2. 补充上下文
    cases = enrich_cases_with_context(cases)

    # 3. 生成回答（如需要）
    if auto_generate:
        llm_client, llm_model = get_llm_client()
        cases = generate_answers(cases, llm_client, llm_model)

    # 4. 检查数据完整性
    empty_answers = [c for c in cases if not c.get("answer")]
    empty_contexts = [c for c in cases if not c.get("retrieved_contexts")]
    if empty_answers:
        logger.warning("%d cases have empty answers", len(empty_answers))
    if empty_contexts:
        logger.warning("%d cases have empty contexts", len(empty_contexts))
    if empty_answers and not auto_generate:
        logger.error("Answers required but missing. Use --generate or add 'answer' fields.")
        return {"error": "missing answers"}

    # 5. 转换格式
    data = convert_to_ragas_format(cases)
    dataset = Dataset.from_dict(data)

    # 6. 配置 LLM-as-Judge（使用 LangChain ChatOpenAI 确保 max_tokens 充足）
    llm_client, llm_model = get_llm_client()
    evaluator_llm = _make_evaluator_llm(llm_client, llm_model)

    # 7. 计算指标（AnswerRelevancy 需要 embedding 模型，暂跳過）
    metrics = [faithfulness, context_recall, context_precision]
    result = evaluate(dataset, metrics=metrics, llm=evaluator_llm, batch_size=1)

    # 8. 输出报告
    os.makedirs(output_dir, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    def avg(key: str) -> float:
        vals = result[key]
        return float(sum(vals) / len(vals)) if len(vals) > 0 else 0.0

    report = {
        "run_id": run_id,
        "dataset": dataset_path,
        "metrics": {
            "faithfulness": avg("faithfulness"),
            "context_recall": avg("context_recall"),
            "context_precision": avg("context_precision"),
        },
        "num_cases": len(cases),
    }

    report_path = os.path.join(output_dir, f"ragas_report_{run_id}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 可读报告
    md_lines = [
        f"# RAGAS 评估报告 ({run_id})",
        "",
        f"| 指标 | 得分 | 说明 |",
        f"|------|------|------|",
        f"| **Faithfulness** | {report['metrics']['faithfulness']:.3f} | 答案忠于检索内容的程度 |",
        f"| **Context Recall** | {report['metrics']['context_recall']:.3f} | 检索覆盖率 |",
        f"| **Context Precision** | {report['metrics']['context_precision']:.3f} | 排序质量 |",
        "",
        f"测试用例数: {report['num_cases']}",
        "",
        "> 注：Answer Relevancy 指标需要 embedding 模型支持，暂未启用。",
    ]
    md_path = os.path.join(output_dir, f"ragas_report_{run_id}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    logger.info("Report saved to %s", report_path)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAGAS Evaluation Runner")
    parser.add_argument("--dataset", default=ragas_settings.eval_dataset)
    parser.add_argument("--output-dir", default=ragas_settings.output_dir)
    parser.add_argument("--no-generate", action="store_true", help="跳过自动生成 answer，仅评估已有数据")
    args = parser.parse_args()
    run_ragas_eval(args.dataset, args.output_dir, auto_generate=not args.no_generate)