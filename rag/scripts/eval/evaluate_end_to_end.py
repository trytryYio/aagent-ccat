import argparse
import asyncio
import csv
import json
import logging
import shutil
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import httpx

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DEFAULT_DATASET = ROOT_DIR / "rag" / "eval" / "datasets" / "rag_e2e_dataset.jsonl"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "rag" / "eval" / "reports"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
UPLOAD_DIR = BACKEND_DIR / "data" / "images"


@dataclass
class E2ECase:
    test_id: str
    image_path: str
    query_text: str
    gold_sku_id: str
    should_clarify: bool
    expected_citations: List[str]
    expected_answer_keywords: List[str]
    forbidden_answer_keywords: List[str]
    difficulty: str
    scenario: str = ""
    category: str = ""


@dataclass
class E2EResult:
    test_id: str
    mode: str
    status: str
    latency_ms: float
    answer_text: str
    answer_length: int
    need_clarify: Optional[bool]
    clarify_question: str
    final_event_received: bool
    candidates_count: int
    citations_count: int
    candidate_hit: bool
    citation_hit: bool
    citation_consistency: Optional[bool]
    answer_useful: Optional[bool]
    keyword_hit_rate: float
    should_clarify: bool
    clarify_correct: Optional[bool]
    difficulty: str
    scenario: str
    category: str
    note: str = ""


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _load_cases(path: Path) -> List[E2ECase]:
    cases: List[E2ECase] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            cases.append(
                E2ECase(
                    test_id=payload["test_id"],
                    image_path=payload.get("image_path", ""),
                    query_text=payload.get("query_text", ""),
                    gold_sku_id=payload["gold_sku_id"],
                    should_clarify=bool(payload.get("should_clarify", False)),
                    expected_citations=list(payload.get("expected_citations", [])),
                    expected_answer_keywords=list(payload.get("expected_answer_keywords", [])),
                    forbidden_answer_keywords=list(payload.get("forbidden_answer_keywords", [])),
                    difficulty=payload.get("difficulty", "unknown"),
                    scenario=payload.get("scenario", ""),
                    category=payload.get("category", ""),
                )
            )
    return cases


def _resolve_image_path(image_path: str) -> Optional[Path]:
    if not image_path:
        return None
    path = (ROOT_DIR / image_path).resolve()
    return path if path.exists() else None


def _keyword_hit_rate(answer_text: str, keywords: List[str]) -> float:
    if not keywords:
        return 1.0
    lowered = answer_text.lower()
    hits = sum(1 for keyword in keywords if keyword.lower() in lowered)
    return hits / len(keywords)


def _answer_useful(answer_text: str, case: E2ECase) -> Optional[bool]:
    if not answer_text.strip():
        return False
    lowered = answer_text.lower()
    if any(keyword.lower() in lowered for keyword in case.forbidden_answer_keywords):
        return False
    if case.expected_answer_keywords:
        return _keyword_hit_rate(answer_text, case.expected_answer_keywords) >= 0.5
    return len(answer_text.strip()) >= 20


async def _consume_sse_stream(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    answer_parts: List[str] = []
    candidates: List[dict] = []
    citations: List[dict] = []
    final_payload: Dict[str, Any] = {}
    final_event_received = False

    async with client.stream("GET", url, timeout=120.0) as response:
        response.raise_for_status()
        current_event = ""
        async for raw_line in response.aiter_lines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
                continue
            if not line.startswith("data:"):
                continue
            data = json.loads(line.split(":", 1)[1].strip())
            if current_event == "candidates":
                candidates = data if isinstance(data, list) else data.get("candidates", [])
            elif current_event == "citations":
                citations = data.get("citations", [])
            elif current_event == "delta":
                answer_parts.append(data.get("text", ""))
            elif current_event == "final":
                final_payload = data
                final_event_received = True
                break
            elif current_event == "error":
                final_payload = {"error": data.get("message", "unknown error")}
                break

    return {
        "answer_text": "".join(answer_parts).strip(),
        "candidates": candidates,
        "citations": citations,
        "final_payload": final_payload,
        "final_event_received": final_event_received,
    }


async def _run_case_via_api(case: E2ECase, base_url: str) -> E2EResult:
    image_path = _resolve_image_path(case.image_path)
    if not image_path:
        return E2EResult(
            test_id=case.test_id,
            mode="api",
            status="skipped",
            latency_ms=0.0,
            answer_text="",
            answer_length=0,
            need_clarify=None,
            clarify_question="",
            final_event_received=False,
            candidates_count=0,
            citations_count=0,
            candidate_hit=False,
            citation_hit=False,
            citation_consistency=None,
            answer_useful=None,
            keyword_hit_rate=0.0,
            should_clarify=case.should_clarify,
            clarify_correct=None,
            difficulty=case.difficulty,
            scenario=case.scenario,
            category=case.category,
            note="image_path not found",
        )

    async with httpx.AsyncClient(base_url=base_url) as client:
        with image_path.open("rb") as handle:
            upload = await client.post(
                "/api/v1/upload/image",
                files={"file": (image_path.name, handle, "image/jpeg")},
                timeout=60.0,
            )
        upload.raise_for_status()
        image_id = upload.json()["data"]["image_id"]

        started = time.perf_counter()
        create_chat = await client.post(
            "/api/v1/chat",
            json={"session_id": None, "image_id": image_id, "text": case.query_text},
            timeout=30.0,
        )
        create_chat.raise_for_status()
        payload = create_chat.json()["data"]
        stream_data = await _consume_sse_stream(client, payload["stream_url"])
        latency_ms = (time.perf_counter() - started) * 1000

    answer_text = stream_data["answer_text"]
    candidates = stream_data["candidates"]
    citations = stream_data["citations"]
    final_payload = stream_data["final_payload"]
    need_clarify = final_payload.get("need_clarify")
    clarify_question = final_payload.get("clarify_question") or ""
    candidate_hit = any(item.get("sku") == case.gold_sku_id for item in candidates)
    citation_ids = [str(item.get("sku") or item.get("product_id") or "") for item in citations]
    citation_hit = any(item in set(case.expected_citations) for item in citation_ids)

    return E2EResult(
        test_id=case.test_id,
        mode="api",
        status="ok" if stream_data["final_event_received"] else "failed",
        latency_ms=latency_ms,
        answer_text=answer_text,
        answer_length=len(answer_text),
        need_clarify=need_clarify,
        clarify_question=clarify_question,
        final_event_received=stream_data["final_event_received"],
        candidates_count=len(candidates),
        citations_count=len(citations),
        candidate_hit=candidate_hit,
        citation_hit=citation_hit,
        citation_consistency=citation_hit if citations else False,
        answer_useful=_answer_useful(answer_text, case),
        keyword_hit_rate=_keyword_hit_rate(answer_text, case.expected_answer_keywords),
        should_clarify=case.should_clarify,
        clarify_correct=(need_clarify == case.should_clarify) if need_clarify is not None else None,
        difficulty=case.difficulty,
        scenario=case.scenario,
        category=case.category,
        note=final_payload.get("error", "") if not answer_text and not need_clarify else "",
    )


async def _run_case_via_orchestrator(case: E2ECase) -> E2EResult:
    try:
        from app.agent.orchestrator import run_flow as orchestrator_run_flow
        from app.agent.tools import init_tools
        from app.core.session import session_mgr
    except ModuleNotFoundError as exc:
        return E2EResult(
            test_id=case.test_id,
            mode="orchestrator",
            status="skipped",
            latency_ms=0.0,
            answer_text="",
            answer_length=0,
            need_clarify=None,
            clarify_question="",
            final_event_received=False,
            candidates_count=0,
            citations_count=0,
            candidate_hit=False,
            citation_hit=False,
            citation_consistency=None,
            answer_useful=None,
            keyword_hit_rate=0.0,
            should_clarify=case.should_clarify,
            clarify_correct=None,
            difficulty=case.difficulty,
            scenario=case.scenario,
            category=case.category,
            note=f"missing dependency: {exc.name}",
        )

    image_path = _resolve_image_path(case.image_path)
    if not image_path:
        return E2EResult(
            test_id=case.test_id,
            mode="orchestrator",
            status="skipped",
            latency_ms=0.0,
            answer_text="",
            answer_length=0,
            need_clarify=None,
            clarify_question="",
            final_event_received=False,
            candidates_count=0,
            citations_count=0,
            candidate_hit=False,
            citation_hit=False,
            citation_consistency=None,
            answer_useful=None,
            keyword_hit_rate=0.0,
            should_clarify=case.should_clarify,
            clarify_correct=None,
            difficulty=case.difficulty,
            scenario=case.scenario,
            category=case.category,
            note="image_path not found",
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    temp_image_id = f"eval_{uuid.uuid4().hex}"
    temp_image_target = UPLOAD_DIR / f"{temp_image_id}{image_path.suffix}"
    shutil.copyfile(image_path, temp_image_target)

    search_results: dict = {"candidates": []}
    init_tools(search_results, str(UPLOAD_DIR))
    session = session_mgr.get_or_create(None)
    queue: asyncio.Queue = asyncio.Queue()

    try:
        started = time.perf_counter()
        flow_task = asyncio.create_task(
            orchestrator_run_flow(temp_image_id, case.query_text, session.session_id, queue)
        )
        answer_text = ""
        candidates: List[dict] = []
        citations: List[dict] = []
        final_event_received = False
        need_clarify: Optional[bool] = None
        clarify_question = ""

        while True:
            try:
                event_type, payload = await asyncio.wait_for(queue.get(), timeout=120.0)
            except asyncio.TimeoutError:
                flow_task.cancel()
                raise

            if event_type == "candidates":
                candidates = payload if isinstance(payload, list) else payload.get("candidates", [])
            elif event_type == "citations":
                citations = payload.get("citations", [])
            elif event_type == "delta":
                answer_text += payload.get("text", "")
            elif event_type == "final":
                need_clarify = payload.get("need_clarify")
                clarify_question = payload.get("clarify_question") or ""
                final_event_received = True
                break
            elif event_type == "error":
                break

        if not flow_task.done():
            await flow_task
        latency_ms = (time.perf_counter() - started) * 1000
    finally:
        temp_image_target.unlink(missing_ok=True)

    candidate_hit = any(item.get("sku") == case.gold_sku_id for item in candidates)
    citation_ids = [str(item.get("sku") or item.get("product_id") or "") for item in citations]
    citation_hit = any(item in set(case.expected_citations) for item in citation_ids)

    return E2EResult(
        test_id=case.test_id,
        mode="orchestrator",
        status="ok" if final_event_received else "failed",
        latency_ms=latency_ms,
        answer_text=answer_text.strip(),
        answer_length=len(answer_text.strip()),
        need_clarify=need_clarify,
        clarify_question=clarify_question,
        final_event_received=final_event_received,
        candidates_count=len(candidates),
        citations_count=len(citations),
        candidate_hit=candidate_hit,
        citation_hit=citation_hit,
        citation_consistency=citation_hit if citations else False,
        answer_useful=_answer_useful(answer_text, case),
        keyword_hit_rate=_keyword_hit_rate(answer_text, case.expected_answer_keywords),
        should_clarify=case.should_clarify,
        clarify_correct=(need_clarify == case.should_clarify) if need_clarify is not None else None,
        difficulty=case.difficulty,
        scenario=case.scenario,
        category=case.category,
    )


def _summarize(results: List[E2EResult]) -> Dict[str, Any]:
    executed = [result for result in results if result.status == "ok"]
    latencies = [result.latency_ms for result in executed]
    latencies_sorted = sorted(latencies)
    p95_index = max(int(len(latencies_sorted) * 0.95) - 1, 0) if latencies_sorted else 0

    return {
        "total_cases": len(results),
        "executed_cases": len(executed),
        "status_breakdown": {
            status: sum(1 for result in results if result.status == status)
            for status in sorted({result.status for result in results})
        },
        "metrics": {
            "candidate_hit_rate": round(_safe_ratio(sum(r.candidate_hit for r in executed), len(executed)), 4),
            "citation_consistency": round(
                _safe_ratio(sum(r.citation_consistency is True for r in executed), len(executed)),
                4,
            ),
            "answer_usefulness": round(
                _safe_ratio(sum(r.answer_useful is True for r in executed), len(executed)),
                4,
            ),
            "clarify_accuracy": round(
                _safe_ratio(sum(r.clarify_correct is True for r in executed), len(executed)),
                4,
            ),
            "keyword_hit_rate": round(
                _safe_ratio(sum(r.keyword_hit_rate for r in executed), len(executed)),
                4,
            ),
            "latency_ms_avg": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
            "latency_ms_p95": round(latencies_sorted[p95_index], 3) if latencies_sorted else 0.0,
        },
    }


def _write_reports(run_id: str, output_dir: Path, summary: Dict[str, Any], results: List[E2EResult]) -> None:
    report_dir = output_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "e2e_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if results:
        with (report_dir / "e2e_cases.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(asdict(results[0]).keys()))
            writer.writeheader()
            for result in results:
                writer.writerow(asdict(result))
    summary_md = "\n".join(
        [
            "# RAG End-to-End Evaluation Report",
            "",
            f"- Run ID: `{run_id}`",
            f"- Mode: `{summary['mode']}`",
            f"- Dataset: `{summary['dataset_path']}`",
            f"- Total Cases: `{summary['total_cases']}`",
            f"- Executed Cases: `{summary['executed_cases']}`",
            f"- Candidate Hit Rate: `{summary['metrics']['candidate_hit_rate']:.2%}`",
            f"- Citation Consistency: `{summary['metrics']['citation_consistency']:.2%}`",
            f"- Answer Usefulness: `{summary['metrics']['answer_usefulness']:.2%}`",
            f"- Clarify Accuracy: `{summary['metrics']['clarify_accuracy']:.2%}`",
            f"- Keyword Hit Rate: `{summary['metrics']['keyword_hit_rate']:.2%}`",
            f"- Latency Avg: `{summary['metrics']['latency_ms_avg']:.3f} ms`",
            f"- Latency P95: `{summary['metrics']['latency_ms_p95']:.3f} ms`",
            "",
        ]
    )
    (report_dir / "e2e_summary.md").write_text(summary_md, encoding="utf-8")


async def _main_async(args: argparse.Namespace) -> None:
    cases = _load_cases(args.dataset.resolve())
    run_id = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-e2e")

    results: List[E2EResult] = []
    for case in cases:
        if args.mode == "api":
            result = await _run_case_via_api(case, args.base_url)
        else:
            result = await _run_case_via_orchestrator(case)
        logger.info(
            f"[{args.mode}] {case.test_id}: status={result.status}, "
            f"useful={result.answer_useful}, clarify={result.need_clarify}, "
            f"latency={result.latency_ms:.1f}ms"
        )
        results.append(result)

    summary = _summarize(results)
    summary["run_id"] = run_id
    summary["mode"] = args.mode
    summary["dataset_path"] = str(args.dataset.resolve())
    summary["started_at"] = datetime.now().astimezone().isoformat()
    summary["finished_at"] = datetime.now().astimezone().isoformat()
    _write_reports(run_id, args.output_dir.resolve(), summary, results)
    logger.info(json.dumps(summary, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG end-to-end evaluation runner")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--mode", choices=["api", "orchestrator"], default="api")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    return parser.parse_args()


def main() -> None:
    asyncio.run(_main_async(parse_args()))


if __name__ == "__main__":
    main()
