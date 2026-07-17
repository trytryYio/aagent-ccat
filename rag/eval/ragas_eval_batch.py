"""分批 RAGAS 评估脚本

将 200 个案例分成多个小批次运行，每批完成后保存结果，
避免长时间运行因网络波动而失败。

用法:
    python rag/eval/ragas_eval_batch.py --batch-size 50 --total 200
"""
import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from rag.eval.ragas_eval_200 import (
    load_products_map,
    build_cases,
    get_llm,
    process_cases_batch,
    run_ragas_evaluation
)


def run_batch_evaluation(batch_num: int, start_idx: int, end_idx: int,
                        all_cases: list, args) -> dict:
    """运行单个批次的评估"""
    print(f"\n{'='*60}")
    print(f"批次 {batch_num}: 案例 {start_idx+1} - {end_idx}")
    print(f"{'='*60}\n")

    # 提取当前批次的案例
    batch_cases = all_cases[start_idx:end_idx]

    # 获取 LLM
    client, model = get_llm()

    # 处理 RAG 检索和生成
    batch_results = process_cases_batch(batch_cases, client, model, args.top_k)

    # 保存批次详情
    output_dir = project_root / "rag" / "eval" / "reports"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    details_file = output_dir / f"ragas_200_details_batch{batch_num}_{timestamp}.jsonl"

    with open(details_file, "w", encoding="utf-8") as f:
        for case, answer, contexts in zip(batch_cases, batch_results["answers"], batch_results["contexts"]):
            record = {
                "test_id": case["test_id"],
                "query_text": case["query_text"],
                "gold_sku_id": case["gold_sku_id"],
                "gold_name": case["gold_name"],
                "ground_truth": case["ground_truth"],
                "category": case["category"],
                "retrieved_contexts": contexts,
                "answer": answer
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"✓ 批次 {batch_num} 详情已保存: {details_file}")

    # 运行 RAGAS 评估
    metrics = run_ragas_evaluation(batch_results, model)

    # 保存批次报告
    report = {
        "batch_num": batch_num,
        "start_idx": start_idx,
        "end_idx": end_idx,
        "num_cases": len(batch_cases),
        "top_k": args.top_k,
        "metrics": metrics,
        "details_file": str(details_file),
        "timestamp": timestamp
    }

    report_file = output_dir / f"ragas_200_report_batch{batch_num}_{timestamp}.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"✓ 批次 {batch_num} 报告已保存: {report_file}")
    print(f"  - Faithfulness: {metrics['faithfulness']:.3f}")
    print(f"  - Context Recall: {metrics['context_recall']:.3f}")
    print(f"  - Context Precision: {metrics['context_precision']:.3f}")
    print(f"  - Answer Relevancy: {metrics['answer_relevancy']:.3f}")

    return report


def merge_batch_reports(output_dir: Path) -> dict:
    """合并所有批次的报告"""
    print(f"\n{'='*60}")
    print("合并所有批次报告")
    print(f"{'='*60}\n")

    # 找到所有批次报告
    batch_reports = []
    for report_file in sorted(output_dir.glob("ragas_200_report_batch*.json")):
        with open(report_file, "r", encoding="utf-8") as f:
            batch_reports.append(json.load(f))

    if not batch_reports:
        print("✗ 未找到任何批次报告")
        return None

    # 计算平均指标
    total_cases = sum(r["num_cases"] for r in batch_reports)
    avg_metrics = {
        "faithfulness": sum(r["metrics"]["faithfulness"] * r["num_cases"] for r in batch_reports) / total_cases,
        "context_recall": sum(r["metrics"]["context_recall"] * r["num_cases"] for r in batch_reports) / total_cases,
        "context_precision": sum(r["metrics"]["context_precision"] * r["num_cases"] for r in batch_reports) / total_cases,
        "answer_relevancy": sum(r["metrics"]["answer_relevancy"] * r["num_cases"] for r in batch_reports) / total_cases
    }

    # 合并报告
    merged_report = {
        "total_cases": total_cases,
        "num_batches": len(batch_reports),
        "top_k": batch_reports[0]["top_k"],
        "metrics": avg_metrics,
        "batch_reports": [
            {
                "batch_num": r["batch_num"],
                "num_cases": r["num_cases"],
                "metrics": r["metrics"],
                "details_file": r["details_file"],
                "timestamp": r["timestamp"]
            }
            for r in batch_reports
        ],
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S")
    }

    # 保存合并报告
    merged_file = output_dir / f"ragas_200_merged_report_{merged_report['timestamp']}.json"
    with open(merged_file, "w", encoding="utf-8") as f:
        json.dump(merged_report, f, ensure_ascii=False, indent=2)

    print(f"✓ 合并报告已保存: {merged_file}")
    print(f"\n最终指标 (加权平均):")
    print(f"  - Faithfulness: {avg_metrics['faithfulness']:.3f}")
    print(f"  - Context Recall: {avg_metrics['context_recall']:.3f}")
    print(f"  - Context Precision: {avg_metrics['context_precision']:.3f}")
    print(f"  - Answer Relevancy: {avg_metrics['answer_relevancy']:.3f}")

    return merged_report


def main():
    parser = argparse.ArgumentParser(description="分批 RAGAS 评估")
    parser.add_argument("--batch-size", type=int, default=50, help="每批案例数量")
    parser.add_argument("--total", type=int, default=200, help="总案例数量")
    parser.add_argument("--top-k", type=int, default=3, help="检索 top-k")
    parser.add_argument("--start-batch", type=int, default=1, help="从第几批开始")
    parser.add_argument("--merge-only", action="store_true", help="仅合并已有报告")
    args = parser.parse_args()

    output_dir = project_root / "rag" / "eval" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 如果只是合并报告
    if args.merge_only:
        merge_batch_reports(output_dir)
        return

    # 加载商品数据
    products_map = load_products_map()
    print(f"✓ 加载 {len(products_map)} 个商品")

    # 构建所有案例
    all_cases = build_cases(products_map, args.total)
    print(f"✓ 构建 {len(all_cases)} 个案例")

    # 计算批次数
    num_batches = (args.total + args.batch_size - 1) // args.batch_size
    print(f"✓ 将分为 {num_batches} 个批次，每批 {args.batch_size} 个案例")

    # 运行每个批次
    batch_reports = []
    for batch_num in range(args.start_batch, num_batches + 1):
        start_idx = (batch_num - 1) * args.batch_size
        end_idx = min(start_idx + args.batch_size, args.total)

        if start_idx >= args.total:
            break

        try:
            report = run_batch_evaluation(batch_num, start_idx, end_idx, all_cases, args)
            batch_reports.append(report)
        except Exception as e:
            print(f"✗ 批次 {batch_num} 失败: {e}")
            print(f"  提示: 可以使用 --start-batch {batch_num} 从失败的批次重新开始")
            return

    # 合并所有报告
    if batch_reports:
        merge_batch_reports(output_dir)
        print("\n✓ 所有批次评估完成！")


if __name__ == "__main__":
    main()
