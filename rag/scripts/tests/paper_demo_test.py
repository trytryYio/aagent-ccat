"""论文演示测试：对运行中的后端跑一批覆盖各场景的案例，输出结果表。

用法（需后端在 127.0.0.1:8000 运行）：
  python -m rag.scripts.paper_demo_test [--base-url http://127.0.0.1:8000]

输出：rag/eval/reports/paper_demo_<时间戳>.md
"""

import argparse
import json
import os
import time
import urllib.request
import urllib.error

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RAG_DIR = os.path.dirname(_SCRIPT_DIR)
REPORT_DIR = os.path.join(_RAG_DIR, "eval", "reports")

# 案例集：覆盖 同款识别 / 近似替代 / 低置信澄清 / 知识问答
# 预期 SKU 基于 products.csv 的 description（BGE-M3 按描述检索）
CASES = [
    {"id": "T1", "scenario": "同款识别", "query": "全掌碳板 GCU止滑 专业比赛羽毛球鞋",
     "expect": "lining_001", "expect_any_of": ["lining_001", "lining_025"], "note": "全掌碳板+GCU"},
    {"id": "T2", "scenario": "同款识别", "query": "入门级训练鞋 耐磨橡胶大底 EVA泡棉缓震",
     "expect": "lining_003", "expect_any_of": ["lining_003", "lining_009"], "note": "入门训练+EVA"},
    {"id": "T3", "scenario": "近似替代", "query": "双打前场 启动快 轻薄 场地感清晰 速度型",
     "expect": "lining_005", "expect_any_of": ["lining_005", "lining_014", "lining_022"], "note": "速度/前场"},
    {"id": "T4", "scenario": "近似替代", "query": "鞋楦偏宽 适合宽脚 稳定性好的羽毛球鞋",
     "expect": "lining_002", "expect_any_of": ["lining_002"], "note": "宽脚稳定"},
    {"id": "T5", "scenario": "近似替代", "query": "预算500元以内的入门训练鞋",
     "expect": "<=500元", "expect_any_of": None, "note": "低价位: 024/006/018/022"},
    {"id": "T6", "scenario": "近似替代", "query": "速度型顶级比赛鞋 镂空大底 碳纤维板",
     "expect": "lining_014", "expect_any_of": ["lining_014"], "note": "顶级速度"},
    {"id": "T7", "scenario": "低置信澄清", "query": "推荐一双羽毛球鞋",
     "expect": "触发澄清", "expect_any_of": None, "note": "意图模糊应追问"},
    {"id": "T8", "scenario": "知识问答", "query": "无敌号这款羽毛球鞋用了什么核心科技",
     "expect": "引用知识", "expect_any_of": ["lining_001"], "note": "LLM 引用 citations"},
]


def run_case(base_url: str, query: str) -> dict:
    """跑一个案例：POST chat → 读 SSE → 收集 candidates/answer/clarify/延迟。"""
    t0 = time.time()
    body = json.dumps({"text": query}).encode("utf-8")
    req = urllib.request.Request(f"{base_url}/api/v1/chat", data=body,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        info = json.loads(resp.read())["data"]
    message_id = info["message_id"]

    # 读 SSE 流（最多 60s）
    stream_url = f"{base_url}/api/v1/chat/stream?message_id={message_id}"
    candidates = []
    answer_parts = []
    clarify = False
    clarify_q = None
    t_first_cand = None
    t_done = None
    try:
        with urllib.request.urlopen(stream_url, timeout=60) as s:
            buf = b""
            for _ in range(10000):
                chunk = s.read(512)
                if not chunk:
                    break
                buf += chunk
                while b"\n\n" in buf:
                    block, buf = buf.split(b"\n\n", 1)
                    text = block.decode("utf-8", "ignore")
                    ev = ""
                    data_line = ""
                    for ln in text.split("\n"):
                        if ln.startswith("event:"):
                            ev = ln[6:].strip()
                        elif ln.startswith("data:"):
                            data_line = ln[5:].strip()
                    if not data_line:
                        continue
                    try:
                        data = json.loads(data_line)
                    except json.JSONDecodeError:
                        continue
                    if ev == "candidates":
                        if t_first_cand is None:
                            t_first_cand = round(time.time() - t0, 2)
                        candidates = data.get("candidates", []) or []
                    elif ev == "delta_text":
                        answer_parts.append(data.get("text", ""))
                    elif ev == "final":
                        clarify = bool(data.get("need_clarify"))
                        clarify_q = data.get("clarify_question")
                        t_done = round(time.time() - t0, 2)
                        break
                    elif ev == "error":
                        t_done = round(time.time() - t0, 2)
                        break
    except Exception as e:
        answer_parts.append(f"[stream err: {e}]")
        t_done = round(time.time() - t0, 2)

    return {
        "candidates": candidates,
        "answer": "".join(answer_parts).strip(),
        "clarify": clarify,
        "clarify_q": clarify_q,
        "t_first_cand": t_first_cand,
        "t_done": t_done,
    }


def cand_sku(c: dict) -> str:
    for k in ("product_id", "sku_id", "id", "sku"):
        if c.get(k):
            return str(c[k])
    return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    os.makedirs(REPORT_DIR, exist_ok=True)

    results = []
    for c in CASES:
        print(f"[{c['id']}] {c['scenario']}: {c['query']}")
        r = run_case(args.base_url, c["query"])
        top3 = [cand_sku(x) for x in r["candidates"][:3]]
        top1 = top3[0] if top3 else "-"
        # 判定
        if c["scenario"] == "低置信澄清":
            verdict = "✓ 触发澄清" if r["clarify"] else "✗ 未澄清"
        elif c["scenario"] == "知识问答":
            verdict = "✓ 有引用/回答" if len(r["answer"]) > 20 else "✗ 回答过短"
        elif c["id"] == "T5":  # 预算
            verdict = "(人工核价)"
        else:
            hit = any(s in top3 for s in (c.get("expect_any_of") or []))
            verdict = ("✓ top3命中" if hit else "✗ 未命中") + f" (top1={top1})"
        c_res = {**c, **r, "top3": top3, "top1": top1, "verdict": verdict}
        results.append(c_res)
        print(f"    → top3={top3} clarify={r['clarify']} t_cand={r['t_first_cand']}s t_done={r['t_done']}s | {verdict}")
        time.sleep(2.5)  # 案例间隔，避免 DeepSeek 连发限流

    # 写 markdown 报告
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(REPORT_DIR, f"paper_demo_{ts}.md")
    lines = [
        "# 论文演示测试报告",
        "",
        f"- 后端: `{args.base_url}` | 时间: {ts}",
        f"- 案例数: {len(results)}",
        "",
        "## 结果总表",
        "",
        "| 案例 | 场景 | 查询 | Top-3 SKU | Top-1 | 澄清 | 首 cand(s) | 完成(s) | 判定 |",
        "|------|------|------|-----------|-------|------|-----------|---------|------|",
    ]
    for r in results:
        lines.append(
            f"| {r['id']} | {r['scenario']} | {r['query'][:18]} | "
            f"{' '.join(r['top3'])} | {r['top1']} | "
            f"{'是' if r['clarify'] else '否'} | "
            f"{r['t_first_cand']} | {r['t_done']} | {r['verdict']} |"
        )
    lines += ["", "## 各案例详情（Top-3 候选 + AI 回答）", ""]
    for r in results:
        lines.append(f"### {r['id']} [{r['scenario']}] {r['query']}")
        lines.append(f"- 预期: {r['expect']} | 判定: **{r['verdict']}**")
        lines.append(f"- 延迟: 首候选 {r['t_first_cand']}s / 完成 {r['t_done']}s"
                     + (f" | 触发澄清: {r['clarify_q']}" if r['clarify'] else ""))
        if r["candidates"]:
            lines.append("- Top-3 候选:")
            for x in r["candidates"][:3]:
                lines.append(f"  - `{cand_sku(x)}` {x.get('title','')[:26]} "
                             f"¥{x.get('price','?')} 相似度{x.get('score','?')}")
        if r["answer"]:
            lines.append("- AI 回答:\n```\n" + r["answer"][:400] + "\n```")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n报告已写: {path}")


if __name__ == "__main__":
    main()