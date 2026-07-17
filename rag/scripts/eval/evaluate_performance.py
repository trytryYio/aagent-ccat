import argparse
import csv
import json
import logging
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.embedding import embed_image, embed_text
from rag.hybrid_search import hybrid_search
from rag.image_search import SearchResult, search_by_image
from rag.text_retrieval import get_citations, get_citations_by_sku, search_by_text

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DEFAULT_DATASET = ROOT_DIR / "rag" / "eval" / "datasets" / "rag_eval_dataset.jsonl"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "rag" / "eval" / "reports"


@dataclass
class EvalCase:
    test_id: str
    image_path: str
    query_text: str
    gold_sku_id: str
    gold_topk: List[str]
    should_clarify: bool
    expected_citations: List[str]
    difficulty: str
    category: str = ""
    scenario: str = ""


@dataclass
class CaseResult:
    test_id: str
    mode: str
    gold_sku_id: str
    predicted_top1: Optional[str]
    predicted_topk: List[str]
    top1_hit: bool
    top3_hit: bool
    recall_at_k: float
    reciprocal_rank: float
    latency_ms: float
    should_clarify: bool
    predicted_clarify: Optional[bool]
    clarify_tp: int
    clarify_fp: int
    clarify_fn: int
    citation_consistent: Optional[bool]
    citation_hit_count: int
    citations_returned: int
    status: str
    difficulty: str
    scenario: str
    category: str
    note: str = ""


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _parse_jsonl(path: Path) -> List[EvalCase]:
    cases: List[EvalCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            cases.append(
                EvalCase(
                    test_id=payload["test_id"],
                    image_path=payload.get("image_path", ""),
                    query_text=payload.get("query_text", ""),
                    gold_sku_id=payload["gold_sku_id"],
                    gold_topk=list(payload.get("gold_topk", [])),
                    should_clarify=bool(payload.get("should_clarify", False)),
                    expected_citations=list(payload.get("expected_citations", [])),
                    difficulty=payload.get("difficulty", "unknown"),
                    category=payload.get("category", ""),
                    scenario=payload.get("scenario", ""),
                )
            )
    return cases


def _load_image_bytes(case: EvalCase) -> Optional[bytes]:
    if not case.image_path:
        return None
    image_path = (ROOT_DIR / case.image_path).resolve()
    if not image_path.exists():
        return None
    return image_path.read_bytes()


def _build_text_embedding(case: EvalCase) -> List[float]:
    if not case.query_text.strip():
        return []
    return embed_text(case.query_text.strip())


def _build_image_embedding(case: EvalCase) -> List[float]:
    image_bytes = _load_image_bytes(case)
    if not image_bytes:
        return []
    return embed_image(image_bytes)


def _reciprocal_rank(predicted_ids: List[str], gold_ids: Iterable[str]) -> float:
    gold_set = set(gold_ids)
    for index, product_id in enumerate(predicted_ids, start=1):
        if product_id in gold_set:
            return 1.0 / index
    return 0.0


def _recall_at_k(predicted_ids: List[str], gold_ids: Iterable[str]) -> float:
    gold_set = set(gold_ids)
    if not gold_set:
        return 0.0
    return sum(1 for product_id in predicted_ids if product_id in gold_set) / len(gold_set)


def _prediction_clarify(results: List[SearchResult]) -> Optional[bool]:
    if not results:
        return True
    return bool(results[0].need_clarify)


def _evaluate_citations(
    case: EvalCase, text_embedding: List[float], predicted_ids: List[str]
) -> Tuple[Optional[bool], int, int]:
    expected = set(case.expected_citations or [case.gold_sku_id])
    if not predicted_ids:
        return False, 0, 0

    citations = []
    if text_embedding:
        citations = get_citations(text_embedding, predicted_ids[:3], top_k=5)
    if not citations:
        citations = get_citations_by_sku(predicted_ids[0])

    citation_ids = [str(item.get("product_id", "")) for item in citations]
    hit_count = sum(1 for product_id in citation_ids if product_id in expected)
    return bool(hit_count), hit_count, len(citations)


def _pick_failure_cases(results: List[CaseResult], n: int = 3) -> List[CaseResult]:
    """挑 ≤n 条代表性失败：status 非 ok > top1 未命中 > 澄清错判(fp/fn)"""
    failures: List[CaseResult] = []
    # 优先级 1: top1 未命中（真实检索问题，归因价值最高）
    failures += [r for r in results if r.status == "ok" and not r.top1_hit]
    # 优先级 2: 澄清误判（阈值问题）
    failures += [r for r in results if r.status == "ok" and r.top1_hit and (r.clarify_fp or r.clarify_fn)]
    # 优先级 3: 不可用（skipped/error，mode 不匹配或环境问题）
    failures += [r for r in results if r.status != "ok"]
    seen = set()
    unique: List[CaseResult] = []
    for r in failures:
        if id(r) not in seen:
            seen.add(id(r))
            unique.append(r)
    return unique[:n]


class RAGEvaluator:
    def __init__(self, dataset_path: Path, output_dir: Path, top_k: int):
        self.dataset_path = dataset_path
        self.output_dir = output_dir
        self.top_k = top_k
        self.cases = _parse_jsonl(dataset_path)

    def run(self, modes: List[str]) -> Dict[str, Any]:
        started_at = datetime.now().astimezone()
        run_id = started_at.strftime("%Y%m%d-%H%M%S")
        all_results: List[CaseResult] = []
        summary: Dict[str, Any] = {
            "run_id": run_id,
            "dataset_path": str(self.dataset_path),
            "dataset_size": len(self.cases),
            "top_k": self.top_k,
            "started_at": started_at.isoformat(),
            "modes": {},
        }

        for mode in modes:
            logger.info(f"\n=== Running {mode} evaluation ===")
            mode_results = [self._evaluate_case(case, mode) for case in self.cases]
            for result in mode_results:
                logger.info(
                    f"[{mode}] {result.test_id}: status={result.status}, "
                    f"top1={result.predicted_top1}, latency={result.latency_ms:.1f}ms"
                )
            summary["modes"][mode] = self._summarize_mode(mode_results)
            all_results.extend(mode_results)

        finished_at = datetime.now().astimezone()
        summary["finished_at"] = finished_at.isoformat()
        summary["duration_seconds"] = round((finished_at - started_at).total_seconds(), 3)
        self._write_reports(run_id, summary, all_results)
        return summary

    def _evaluate_case(self, case: EvalCase, mode: str) -> CaseResult:
        text_embedding: List[float] = []
        image_embedding: List[float] = []
        note = ""
        status = "ok"

        if mode in {"text", "hybrid"}:
            text_embedding = _build_text_embedding(case)
            if mode == "text" and not text_embedding:
                status = "skipped"
                note = "missing query_text or embedding generation failed"

        if mode in {"image", "hybrid"}:
            image_embedding = _build_image_embedding(case)
            if mode == "image" and not image_embedding:
                status = "skipped"
                note = "missing image_path or image file not found"

        if mode == "hybrid" and not text_embedding and not image_embedding:
            status = "skipped"
            note = "both text and image inputs are unavailable"

        search_results: List[SearchResult] = []
        latency_ms = 0.0
        if status == "ok":
            # 多维度过滤：先 slot_filling 提取结构化信息，再构建 Qdrant filter
            qdrant_filter = None
            if case.query_text.strip():
                try:
                    from backend.app.graph.slot_filling import extract_slots_by_rules, _enhance_slots, get_qdrant_filter
                    slots = extract_slots_by_rules(case.query_text)
                    slots = _enhance_slots(slots)
                    qdrant_filter = get_qdrant_filter(slots)
                except Exception:
                    qdrant_filter = None

            started = time.perf_counter()
            if mode == "text":
                # 先尝试带完整 filter 搜索，失败则回退到仅价格过滤
                try:
                    search_results = search_by_text(
                        text_embedding, top_k=self.top_k, qdrant_filter=qdrant_filter,
                    )
                except Exception:
                    # filter 导致 400（如缺少 text 索引），回退到无 filter
                    search_results = search_by_text(text_embedding, top_k=self.top_k)
            elif mode == "image":
                search_results = search_by_image(image_embedding, top_k=self.top_k)
            elif mode == "hybrid":
                try:
                    search_results = hybrid_search(
                        image_embedding=image_embedding or None,
                        text_embedding=text_embedding or None,
                        query=case.query_text if mode == "hybrid" else None,
                        top_k=self.top_k,
                        qdrant_filter=qdrant_filter,
                    )
                except Exception:
                    search_results = hybrid_search(
                        image_embedding=image_embedding or None,
                        text_embedding=text_embedding or None,
                        query=case.query_text if mode == "hybrid" else None,
                        top_k=self.top_k,
                    )
            latency_ms = (time.perf_counter() - started) * 1000

            # 后置过滤：价格范围 + 品类匹配（云端 price 存为字符串，无法用 Range 过滤）
            if search_results and case.query_text.strip():
                try:
                    from backend.app.graph.slot_filling import extract_slots_by_rules, _enhance_slots
                    from rag.category_mapping import expand_category, match_category_in_text
                    slots = extract_slots_by_rules(case.query_text)
                    slots = _enhance_slots(slots)

                    filtered = []
                    for r in search_results:
                        # 价格过滤
                        price = r.price
                        if price is not None:
                            if slots.get("budget_max") and price > slots["budget_max"]:
                                continue
                            if slots.get("budget_min") and price < slots["budget_min"]:
                                continue
                        # 品类过滤
                        aliases = slots.get("category_aliases", [])
                        if aliases and r.category:
                            if not match_category_in_text(r.category, aliases):
                                continue
                        filtered.append(r)

                    # 安全回退：过滤后结果太少时保留原始列表
                    if len(filtered) >= 1:
                        search_results = filtered
                except Exception:
                    pass

        predicted_ids = [result.product_id for result in search_results]
        gold_ids = case.gold_topk or [case.gold_sku_id]
        predicted_top1 = predicted_ids[0] if predicted_ids else None
        predicted_clarify = _prediction_clarify(search_results) if status == "ok" else None
        citation_consistent: Optional[bool] = None
        citation_hit_count = 0
        citations_returned = 0

        if status == "ok" and mode in {"text", "hybrid"}:
            citation_consistent, citation_hit_count, citations_returned = _evaluate_citations(
                case, text_embedding, predicted_ids
            )

        clarify_tp = int(predicted_clarify is True and case.should_clarify is True)
        clarify_fp = int(predicted_clarify is True and case.should_clarify is False)
        clarify_fn = int(predicted_clarify is False and case.should_clarify is True)

        return CaseResult(
            test_id=case.test_id,
            mode=mode,
            gold_sku_id=case.gold_sku_id,
            predicted_top1=predicted_top1,
            predicted_topk=predicted_ids,
            top1_hit=bool(predicted_top1 == case.gold_sku_id),
            top3_hit=case.gold_sku_id in predicted_ids[:3],
            recall_at_k=_recall_at_k(predicted_ids[: self.top_k], gold_ids)
            if status == "ok"
            else 0.0,
            reciprocal_rank=_reciprocal_rank(predicted_ids, gold_ids)
            if status == "ok"
            else 0.0,
            latency_ms=latency_ms,
            should_clarify=case.should_clarify,
            predicted_clarify=predicted_clarify,
            clarify_tp=clarify_tp,
            clarify_fp=clarify_fp,
            clarify_fn=clarify_fn,
            citation_consistent=citation_consistent,
            citation_hit_count=citation_hit_count,
            citations_returned=citations_returned,
            status=status,
            difficulty=case.difficulty,
            scenario=case.scenario,
            category=case.category,
            note=note,
        )

    def _summarize_mode(self, results: List[CaseResult]) -> Dict[str, Any]:
        executed = [result for result in results if result.status == "ok"]
        skipped = [result for result in results if result.status != "ok"]
        latencies = [result.latency_ms for result in executed]
        citation_values = [r for r in executed if r.citation_consistent is not None]
        clarify_predictions = [r for r in executed if r.predicted_clarify is not None]
        clarify_positive = sum(1 for r in clarify_predictions if r.predicted_clarify)
        tp = sum(result.clarify_tp for result in executed)
        fp = sum(result.clarify_fp for result in executed)
        fn = sum(result.clarify_fn for result in executed)

        return {
            "total_cases": len(results),
            "executed_cases": len(executed),
            "skipped_cases": len(skipped),
            "status_breakdown": dict(Counter(result.status for result in results)),
            "metrics": {
                "available_rate": round(_safe_ratio(len(executed), len(results)), 4),
                "top1": round(_safe_ratio(sum(r.top1_hit for r in executed), len(executed)), 4),
                "top3": round(_safe_ratio(sum(r.top3_hit for r in executed), len(executed)), 4),
                "recall_at_k": round(
                    _safe_ratio(sum(r.recall_at_k for r in executed), len(executed)), 4
                ),
                "mrr": round(
                    _safe_ratio(sum(r.reciprocal_rank for r in executed), len(executed)), 4
                ),
                "clarify_rate": round(
                    _safe_ratio(clarify_positive, len(clarify_predictions)), 4
                ),
                "citation_consistency": round(
                    _safe_ratio(
                        sum(r.citation_consistent is True for r in citation_values),
                        len(citation_values),
                    ),
                    4,
                ),
                "latency_ms_p50": round(_percentile(latencies, 0.50), 3),
                "latency_ms_p95": round(_percentile(latencies, 0.95), 3),
                "latency_ms_avg": round(statistics.mean(latencies), 3) if latencies else 0.0,
            },
            "clarify_confusion": {
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "precision": round(_safe_ratio(tp, tp + fp), 4),
                "recall": round(_safe_ratio(tp, tp + fn), 4),
            },
            "segments": self._segment_metrics(executed),
        }

    def _segment_metrics(self, results: List[CaseResult]) -> Dict[str, Dict[str, Dict[str, Any]]]:
        output: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for field in ("scenario", "difficulty", "category"):
            buckets: Dict[str, List[CaseResult]] = defaultdict(list)
            for result in results:
                buckets[getattr(result, field) or "unspecified"].append(result)
            output[field] = {}
            for key, bucket in buckets.items():
                output[field][key] = {
                    "cases": len(bucket),
                    "top1": round(_safe_ratio(sum(r.top1_hit for r in bucket), len(bucket)), 4),
                    "top3": round(_safe_ratio(sum(r.top3_hit for r in bucket), len(bucket)), 4),
                    "clarify_rate": round(
                        _safe_ratio(sum(r.predicted_clarify is True for r in bucket), len(bucket)),
                        4,
                    ),
                    "latency_ms_p95": round(
                        _percentile([r.latency_ms for r in bucket], 0.95), 3
                    ),
                }
        return output

    def _write_reports(
        self, run_id: str, summary: Dict[str, Any], all_results: List[CaseResult]
    ) -> None:
        report_dir = self.output_dir / run_id
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if all_results:
            with (report_dir / "cases.csv").open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(asdict(all_results[0]).keys()))
                writer.writeheader()
                for result in all_results:
                    row = asdict(result)
                    row["predicted_topk"] = json.dumps(row["predicted_topk"], ensure_ascii=False)
                    writer.writerow(row)
        (report_dir / "summary.md").write_text(
            self._render_markdown(summary, all_results), encoding="utf-8"
        )
        logger.info(f"\nReports written to: {report_dir}")

    def _render_markdown(
        self, summary: Dict[str, Any], all_results: List[CaseResult]
    ) -> str:
        lines = [
            "# RAG 检索评测报告",
            "",
            f"- Run ID: `{summary['run_id']}`",
            f"- Dataset: `{summary['dataset_path']}`（{summary['dataset_size']} 条）",
            f"- Top K: `{summary['top_k']}` | 耗时: `{summary['duration_seconds']}s`",
            "",
        ]
        for mode, mode_summary in summary["modes"].items():
            metrics = mode_summary["metrics"]
            confusion = mode_summary["clarify_confusion"]
            lines.extend(
                [
                    f"## {mode} 模式",
                    "",
                    "| 指标 | 值 |",
                    "|------|----|",
                    f"| 用例数 / 可用数 | {mode_summary['total_cases']} / {mode_summary['executed_cases']} |",
                    f"| 可用率 | {metrics['available_rate']:.2%} |",
                    f"| Top-1 命中率 | {metrics['top1']:.2%} |",
                    f"| Top-3 命中率 | {metrics['top3']:.2%} |",
                    f"| Recall@K | {metrics['recall_at_k']:.2%} |",
                    f"| MRR | {metrics['mrr']:.4f} |",
                    f"| 澄清率 | {metrics['clarify_rate']:.2%} |",
                    f"| Citation 一致性 | {metrics['citation_consistency']:.2%} |",
                                        f"| 延迟 P50 / P95 | {metrics["latency_ms_p50"]:.1f} / {metrics["latency_ms_p95"]:.1f} ms |",
                    f"| 澄清 TP/FP/FN | {confusion['tp']}/{confusion['fp']}/{confusion['fn']} |",
                    "",
                ]
            )
            seg = mode_summary.get("segments", {}).get("scenario", {})
            if seg:
                lines.extend(
                    [
                        f"### {mode} 分场景指标",
                        "",
                        "| 场景 | 用例数 | Top-1 | Top-3 | 澄清率 | 延迟P95(ms) |",
                        "|------|--------|-------|-------|--------|-------------|",
                    ]
                )
                for scenario_name, bucket in seg.items():
                    note = " *(占位参考)*" if "占位" in scenario_name else ""
                    lines.append(
                        f"| {scenario_name}{note} | {bucket['cases']} | "
                        f"{bucket['top1']:.2%} | {bucket['top3']:.2%} | "
                        f"{bucket['clarify_rate']:.2%} | {bucket['latency_ms_p95']:.1f} |"
                    )
                lines.append("")
        mode_results = [r for r in all_results if r.mode == "text"] or all_results
        failures = _pick_failure_cases(mode_results, n=3)
        if failures:
            lines.extend(["## 失败样例归因（Top 3）", ""])
            for r in failures:
                lines.extend(
                    [
                        f"### {r.test_id} [{r.scenario}]",
                        f"- 输入: gold=`{r.gold_sku_id}`",
                        f"- 实际: top1=`{r.predicted_top1}`, top3=`{r.predicted_topk[:3]}`, "
                        f"status=`{r.status}`",
                    ]
                )
                if r.status != "ok":
                    lines.append(f"- 归因: [数据/环境] {r.note}")
                    lines.append("- 改进: 补齐输入数据（图片/文本）或检查 embedding 加载")
                elif not r.top1_hit:
                    lines.append("- 归因: [检索] BGE-M3 向量区分度不足或 query 与 gold 描述差异大")
                    lines.append("- 改进: 优化产品描述关键词 / 调 RRF 权重 / 加 reranker 精排")
                elif r.clarify_fp:
                    lines.append("- 归因: [阈值] 候选分数接近误触发澄清（gap<0.05）")
                    lines.append("- 改进: 调宽 clarify gap 阈值或加意图置信度")
                elif r.clarify_fn:
                    lines.append("- 归因: [阈值] 该澄清却未澄清（top_score>0.6 漏判）")
                    lines.append("- 改进: 提高澄清触发阈值或加多义性检测")
                lines.append("")
        return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG evaluation runner")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=["text", "image", "hybrid"],
        default=["text", "image", "hybrid"],
    )
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    evaluator = RAGEvaluator(
        dataset_path=args.dataset.resolve(),
        output_dir=args.output_dir.resolve(),
        top_k=args.top_k,
    )
    summary = evaluator.run(args.modes)
    logger.info(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


