"""从已有的details文件运行RAGAS评估

用法:
    python rag/eval/ragas_eval_from_details.py <details_file>
"""
import json
import sys
from pathlib import Path
from datetime import datetime

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness, context_recall, context_precision, answer_relevancy
from langchain_openai import ChatOpenAI

from rag.eval.eval_config import EvalConfig
from rag.eval.ragas_eval_200 import get_embedding_judge, safe_float


def run_evaluation_from_details(details_file: str):
    """从details文件加载数据并运行RAGAS评估"""
    print(f"加载数据: {details_file}")

    # 读取details文件
    questions = []
    answers = []
    contexts = []
    ground_truths = []

    with open(details_file, "r", encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            questions.append(record["query_text"])
            answers.append(record["answer"])
            contexts.append(record["retrieved_contexts"])
            ground_truths.append(record["ground_truth"])

    print(f"加载了 {len(questions)} 个案例")

    # 创建数据集
    dataset = Dataset.from_dict({
        "question": questions,
        "answer": answers,
        "contexts": contexts,
        "ground_truth": ground_truths,
    })

    # 创建evaluator LLM（使用配置）
    config = EvalConfig.get_config()
    evaluator_llm = ChatOpenAI(
        model=config["model"],
        api_key=config["api_key"],
        base_url=config["base_url"],
        max_tokens=8192,
        temperature=0.0,
        timeout=180,
        max_retries=5,
        model_kwargs={"n": 1},  # 千问支持n=1
    )

    # 获取embedding judge
    embedding_judge = get_embedding_judge()

    # 选择指标
    if embedding_judge:
        metrics = [faithfulness, context_recall, context_precision, answer_relevancy]
        print("使用4个指标: faithfulness, context_recall, context_precision, answer_relevancy")
    else:
        metrics = [faithfulness, context_recall, context_precision]
        print("使用3个指标: faithfulness, context_recall, context_precision")

    # 运行评估
    print("开始RAGAS评估...")
    result = evaluate(
        dataset,
        metrics=metrics,
        llm=evaluator_llm,
        embeddings=embedding_judge,
        batch_size=2,  # 降低batch_size避免超时
    )

    # 计算平均值
    def avg(k):
        v = result[k]
        return sum(safe_float(x) for x in v) / len(v) if len(v) else 0.0

    metrics_result = {
        "faithfulness": avg("faithfulness"),
        "context_recall": avg("context_recall"),
        "context_precision": avg("context_precision"),
        "answer_relevancy": avg("answer_relevancy") if embedding_judge else None,
    }

    # 保存报告
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    details_path = Path(details_file)
    report = {
        "details_file": str(details_file),
        "num_cases": len(questions),
        "timestamp": timestamp,
        "metrics": metrics_result,
    }

    report_file = details_path.parent / f"ragas_eval_from_details_{timestamp}.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n评估完成!")
    print(f"报告保存到: {report_file}")
    print(f"\n指标结果:")
    for k, v in metrics_result.items():
        if v is not None:
            print(f"  {k}: {v:.3f}")
        else:
            print(f"  {k}: skipped")

    return report


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python ragas_eval_from_details.py <details_file>")
        sys.exit(1)

    details_file = sys.argv[1]
    if not Path(details_file).exists():
        print(f"错误: 文件不存在: {details_file}")
        sys.exit(1)

    run_evaluation_from_details(details_file)
