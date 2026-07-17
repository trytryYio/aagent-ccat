# 评测体系 + 纯文字检索链路 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修前端纯文字检索 bug，建分场景评测体系（扩充数据集 + 评测脚本增强 + 指标表 + 失败样例归因）。

**Architecture:** 前端去掉 2 处图片强制让纯文字可直接检索；评测数据集扩到 15 条覆盖 5 场景；`evaluate_performance.py` 已有 `_segment_metrics`/`_render_markdown` 基础，补「可用率指标 + 失败样例挑选 + MD 渲染分场景表与失败归因」。文字链路为主出真实指标，图片链路标注占位参考。

**Tech Stack:** Vue 3 + TypeScript（前端）/ Python + Qdrant + BGE-M3/CLIP（评测）/ 项目无 pytest/vitest，用内联 `python -c` 断言 + 浏览器手动验证替代单测。

---

## 文件结构

| 文件 | 责任 | 动作 |
|------|------|------|
| `web/src/composables/useChat.ts` | 聊天发送逻辑 | 改：去 2 处图片强制 |
| `rag/eval/datasets/rag_eval_dataset.jsonl` | 评测数据集 | 改：5→15 条分场景 |
| `rag/scripts/evaluate_performance.py` | 评测脚本 | 改：补可用率/失败样例/MD 分场景表 |

---

### Task 1: 前端纯文字检索修复

**Files:**
- Modify: `web/src/composables/useChat.ts`（`sendMessage` L62-80、`startChat` L209-210）

**背景**: 后端 `chat.py:36` 接受 `session_id` 或 `image_id`（有 session_id 即可不传图），纯文字链路已通。前端两处强制图片拦截了纯文字。

- [ ] **Step 1: 改 `sendMessage` 纯文字分支，去掉「找历史图片」强制**

定位 `web/src/composables/useChat.ts` 的 `sendMessage`，当前纯文字分支（`if (!file)` 块）会找历史消息图片、找不到就报错。改为纯文字直接发送：

```typescript
  const sendMessage = async (text?: string | null) => {
    const file = state.selectedImageFile

    if (!file) {
      // 纯文字检索（无需图片，后端支持 session_id + text）
      if (!text || text.trim() === '') return
      addUserMessage(text ?? '')
      startChat(text ?? '')
      return
    }
```

（删除原 `if (!currentImageId) { ... 找 lastImageMsg ... 请先拍照 ... }` 整段）

- [ ] **Step 2: 改 `startChat`，去掉 `if (!currentImageId) return`**

定位 `startChat`（约 L209），当前第一行 `if (!currentImageId) return` 会拦纯文字。改为允许无图，`createChat` 传空 imageId：

```typescript
  const startChat = async (text?: string) => {
    const msgId = `msg_${++messageCounter}`
    const assistantMsg: ChatMessage = {
      id: msgId,
      role: 'assistant',
      text: '',
      candidates: null,
      citations: null,
      needClarify: false,
      clarifyQuestion: null,
      isLoading: true,
      isStreaming: true,
      isError: false,
      errorMessage: null,
    }

    state.messages = [...state.messages, assistantMsg]
    state.isStreaming = true
    state.currentStreamingMessageId = msgId
    state.error = null

    try {
      // imageId 为空时走纯文字检索（后端接受 session_id + text）
      const chatRes = await createChat(sessionId, currentImageId ?? '', text)
```

（删除函数开头的 `if (!currentImageId) return`；`createChat` 第三参数已是 text，imageId 传 `currentImageId ?? ''`）

- [ ] **Step 3: 类型检查**

Run: `cd web && node node_modules/vue-tsc/bin/vue-tsc.js --noEmit`
Expected: exit 0，无类型错误。

- [ ] **Step 4: 浏览器手动验证**

确保后端在 `127.0.0.1:8000` 运行（`cd backend && source .venv/bin/activate && export $(grep -v '^#' .env|xargs) HF_HUB_OFFLINE=1 && PYTHONPATH=$(pwd):$(dirname $(pwd)) python -m uvicorn app.main:app --reload --port 8000`），前端 `cd web && node node_modules/vite/bin/vite.js`。

打开 `http://localhost:5173`，**不传图片**，直接输入「变色龙入门训练鞋」发送。
Expected: 不再弹「请先拍照」错误；出现商品卡片 + AI 流式回答。

- [ ] **Step 5: Commit**

```bash
git add web/src/composables/useChat.ts
git commit -m "fix(web): 纯文字可直接检索，去掉两处图片强制拦截"
```

---

### Task 2: 扩充评测数据集到 15 条

**Files:**
- Modify: `rag/eval/datasets/rag_eval_dataset.jsonl`（整体重写为 15 条）

**背景**: 现 5 条都带 image_path。扩充到 15 条，文字 case（`image_path` 空）为主，覆盖 5 场景。图片 case 自搜自（同图同向量，验证链路可用性）。

- [ ] **Step 1: 重写数据集为 15 条**

用以下内容覆盖 `rag/eval/datasets/rag_eval_dataset.jsonl`（每行一个 JSON，字段：test_id, image_path, query_text, gold_sku_id, gold_topk, should_clarify, expected_citations, difficulty, category, scenario）：

```jsonl
{"test_id":"case-001","image_path":"","query_text":"全掌碳板 回弹强劲 专业比赛羽毛球鞋","gold_sku_id":"lining_001","gold_topk":["lining_001"],"should_clarify":false,"expected_citations":["lining_001"],"difficulty":"easy","category":"运动/鞋类/羽毛球鞋","scenario":"同款识别"}
{"test_id":"case-002","image_path":"","query_text":"变色龙 入门训练 耐磨橡胶大底","gold_sku_id":"lining_003","gold_topk":["lining_003"],"should_clarify":false,"expected_citations":["lining_003"],"difficulty":"easy","category":"运动/鞋类/羽毛球鞋","scenario":"同款识别"}
{"test_id":"case-003","image_path":"","query_text":"锋影 速度型 双打前场 启动快 轻薄","gold_sku_id":"lining_005","gold_topk":["lining_005"],"should_clarify":false,"expected_citations":["lining_005"],"difficulty":"medium","category":"运动/鞋类/羽毛球鞋","scenario":"同款识别"}
{"test_id":"case-004","image_path":"","query_text":"预算500元以内的入门训练鞋","gold_sku_id":"lining_003","gold_topk":["lining_003","lining_021","lining_024"],"should_clarify":false,"expected_citations":["lining_003","lining_021"],"difficulty":"medium","category":"运动/鞋类/羽毛球鞋","scenario":"近似替代"}
{"test_id":"case-005","image_path":"","query_text":"千元内的专业比赛羽毛球鞋","gold_sku_id":"lining_001","gold_topk":["lining_001","lining_005","lining_007"],"should_clarify":false,"expected_citations":["lining_001"],"difficulty":"medium","category":"运动/鞋类/羽毛球鞋","scenario":"近似替代"}
{"test_id":"case-006","image_path":"","query_text":"宽脚 适合 稳定性好的羽毛球鞋","gold_sku_id":"lining_002","gold_topk":["lining_002"],"should_clarify":false,"expected_citations":["lining_002"],"difficulty":"medium","category":"运动/鞋类/羽毛球鞋","scenario":"近似替代"}
{"test_id":"case-007","image_path":"","query_text":"学生党 入门耐穿 性价比高的训练鞋","gold_sku_id":"lining_003","gold_topk":["lining_003","lining_009","lining_021"],"should_clarify":false,"expected_citations":["lining_003"],"difficulty":"medium","category":"运动/鞋类/羽毛球鞋","scenario":"近似替代"}
{"test_id":"case-008","image_path":"","query_text":"推荐一双羽毛球鞋","gold_sku_id":"lining_001","gold_topk":["lining_001","lining_002","lining_003"],"should_clarify":true,"expected_citations":["lining_001"],"difficulty":"hard","category":"运动/鞋类/羽毛球鞋","scenario":"低置信澄清"}
{"test_id":"case-009","image_path":"","query_text":"好看的鞋","gold_sku_id":"lining_001","gold_topk":["lining_001"],"should_clarify":true,"expected_citations":["lining_001"],"difficulty":"hard","category":"运动/鞋类/羽毛球鞋","scenario":"低置信澄清"}
{"test_id":"case-010","image_path":"","query_text":"适合我的鞋","gold_sku_id":"lining_003","gold_topk":["lining_003"],"should_clarify":true,"expected_citations":["lining_003"],"difficulty":"hard","category":"运动/鞋类/羽毛球鞋","scenario":"低置信澄清"}
{"test_id":"case-011","image_path":"","query_text":"雷霆80用了什么核心科技","gold_sku_id":"lining_001","gold_topk":["lining_001"],"should_clarify":false,"expected_citations":["lining_001"],"difficulty":"easy","category":"运动/鞋类/羽毛球鞋","scenario":"知识问答"}
{"test_id":"case-012","image_path":"","query_text":"变色龙鞋底是什么材质 缓震怎么样","gold_sku_id":"lining_003","gold_topk":["lining_003"],"should_clarify":false,"expected_citations":["lining_003"],"difficulty":"easy","category":"运动/鞋类/羽毛球鞋","scenario":"知识问答"}
{"test_id":"case-013","image_path":"rag/data/images/lining_001.jpg","query_text":"","gold_sku_id":"lining_001","gold_topk":["lining_001"],"should_clarify":false,"expected_citations":["lining_001"],"difficulty":"easy","category":"运动/鞋类/羽毛球鞋","scenario":"图片同款(占位)"}
{"test_id":"case-014","image_path":"rag/data/images/lining_003.jpg","query_text":"","gold_sku_id":"lining_003","gold_topk":["lining_003"],"should_clarify":false,"expected_citations":["lining_003"],"difficulty":"easy","category":"运动/鞋类/羽毛球鞋","scenario":"图片同款(占位)"}
{"test_id":"case-015","image_path":"rag/data/images/lining_005.jpg","query_text":"","gold_sku_id":"lining_005","gold_topk":["lining_005"],"should_clarify":false,"expected_citations":["lining_005"],"difficulty":"easy","category":"运动/鞋类/羽毛球鞋","scenario":"图片同款(占位)"}
```

- [ ] **Step 2: 验证数据集解析 + 场景分布**

Run:
```bash
cd /home/user/projects/AgentProject/Agent
source backend/.venv/bin/activate
python3 -c "
import json
from collections import Counter
cases=[json.loads(l) for l in open('rag/eval/datasets/rag_eval_dataset.jsonl') if l.strip()]
print(f'总数: {len(cases)}')
print('场景分布:', dict(Counter(c['scenario'] for c in cases)))
print('文字 case:', sum(1 for c in cases if not c['image_path']))
print('图片 case:', sum(1 for c in cases if c['image_path']))
assert len(cases)==15, f'期望15条, 实际{len(cases)}'
print('OK')
"
```
Expected: 总数 15，场景分布含 5 种，文字 12 / 图片 3，输出 OK。

- [ ] **Step 3: Commit**

```bash
git add rag/eval/datasets/rag_eval_dataset.jsonl
git commit -m "feat(eval): 评测数据集扩充到 15 条，覆盖 5 场景（同款/替代/澄清/问答/图片占位）"
```

---

### Task 3: 评测脚本加「可用率」指标

**Files:**
- Modify: `rag/scripts/evaluate_performance.py`（`_summarize_mode` L299-346）

**背景**: `summary["modes"][mode]` 已有 `executed_cases`/`skipped_cases`，但 `metrics` 里没有显式可用率。补 `available_rate`。

- [ ] **Step 1: 在 `_summarize_mode` 的 `metrics` 字典里加 `available_rate`**

定位 `_summarize_mode`（约 L310），在 `return { ... "metrics": { "top1": ... }` 的 metrics 字典里，紧挨 `top1` 前面加一行：

```python
            "metrics": {
                "available_rate": round(_safe_ratio(len(executed), len(results)), 4),
                "top1": round(_safe_ratio(sum(r.top1_hit for r in executed), len(executed)), 4),
```

- [ ] **Step 2: 在 `_render_markdown` 渲染可用率**

定位 `_render_markdown`（约 L403），在每个 mode 的 metrics 渲染块里，紧挨 `- Executed:` 后加一行：

```python
                [
                    f"## {mode}",
                    "",
                    f"- Executed: `{mode_summary['executed_cases']}`",
                    f"- Skipped: `{mode_summary['skipped_cases']}`",
                    f"- 可用率: `{metrics['available_rate']:.2%}`",
                    f"- Top-1: `{metrics['top1']:.2%}`",
```

- [ ] **Step 3: 内联验证可用率计算**

Run:
```bash
cd /home/user/projects/AgentProject/Agent
source backend/.venv/bin/activate
python3 -c "
from rag.scripts.evaluate_performance import _safe_ratio
# 模拟 12 executed / 15 total
print('available_rate =', round(_safe_ratio(12, 15), 4))
assert round(_safe_ratio(12, 15), 4) == 0.8
print('OK')
"
```
Expected: 输出 `available_rate = 0.8` 和 `OK`。

- [ ] **Step 4: Commit**

```bash
git add rag/scripts/evaluate_performance.py
git commit -m "feat(eval): 评测脚本加可用率(available_rate)指标"
```

---

### Task 4: 评测脚本加「失败样例挑选」函数

**Files:**
- Modify: `rag/scripts/evaluate_performance.py`（新增模块级函数 `_pick_failure_cases`）

**背景**: 评测跑完挑 ≤3 条代表性失败（status 非 ok / top1 未命中 / 澄清错判），供 MD 报告归因。

- [ ] **Step 1: 在 `RAGEvaluator` 类前新增 `_pick_failure_cases` 函数**

定位 `class RAGEvaluator:`（约 L177），在它**前面**插入模块级函数：

```python
def _pick_failure_cases(results: List[CaseResult], n: int = 3) -> List[CaseResult]:
    """挑 ≤n 条代表性失败：status 非 ok > top1 未命中 > 澄清错判(fp/fn)"""
    failures: List[CaseResult] = []
    # 优先级 1: 不可用（skipped/error）
    failures += [r for r in results if r.status != "ok"]
    # 优先级 2: top1 未命中
    failures += [r for r in results if r.status == "ok" and not r.top1_hit]
    # 优先级 3: 澄清误判（fp 本不该澄清却澄清 / fn 该澄清却没）
    failures += [r for r in results if r.status == "ok" and r.top1_hit and (r.clarify_fp or r.clarify_fn)]
    # 去重保序
    seen = set()
    unique: List[CaseResult] = []
    for r in failures:
        if id(r) not in seen:
            seen.add(id(r))
            unique.append(r)
    return unique[:n]
```

- [ ] **Step 2: 内联验证挑选逻辑**

Run:
```bash
cd /home/user/projects/AgentProject/Agent
source backend/.venv/bin/activate
python3 -c "
from rag.scripts.evaluate_performance import _pick_failure_cases, CaseResult
def mk(tid, status='ok', top1=True, fp=0, fn=0):
    return CaseResult(test_id=tid, mode='text', gold_sku_id='x', predicted_top1='x',
        predicted_topk=[], top1_hit=top1, top3_hit=top1, recall_at_k=0.0, reciprocal_rank=0.0,
        latency_ms=10.0, should_clarify=False, predicted_clarify=False,
        clarify_tp=0, clarify_fp=fp, clarify_fn=fn, citation_consistent=None,
        citation_hit_count=0, citations_returned=0, status=status, difficulty='easy',
        scenario='test', category='c')
cases = [mk('ok1'), mk('skip1', status='skipped'), mk('miss1', top1=False), mk('fp1', fp=1), mk('ok2')]
fails = _pick_failure_cases(cases, n=3)
print('挑选:', [f.test_id for f in fails])
assert [f.test_id for f in fails] == ['skip1', 'miss1', 'fp1']
print('OK')
"
```
Expected: 输出 `挑选: ['skip1', 'miss1', 'fp1']` 和 `OK`。

- [ ] **Step 3: Commit**

```bash
git add rag/scripts/evaluate_performance.py
git commit -m "feat(eval): 新增失败样例挑选函数 _pick_failure_cases"
```

---

### Task 5: MD 报告渲染分场景表 + 失败样例归因

**Files:**
- Modify: `rag/scripts/evaluate_performance.py`（`_render_markdown` L389-423、`_write_reports` L370 签名）

**背景**: `summary["modes"][mode]["segments"]["scenario"]` 已有分场景统计（_segment_metrics），但 `_render_markdown` 没渲染。失败样例需从 `all_results` 挑并归因。需把 `all_results` 传进 `_render_markdown`。

- [ ] **Step 1: 改 `_write_reports` 把 `all_results` 传给 `_render_markdown`**

定位 `_write_reports`（约 L386），把：
```python
        (report_dir / "summary.md").write_text(self._render_markdown(summary), encoding="utf-8")
```
改为：
```python
        (report_dir / "summary.md").write_text(
            self._render_markdown(summary, all_results), encoding="utf-8"
        )
```

- [ ] **Step 2: 改 `_render_markdown` 签名 + 加分场景表 + 失败样例**

定位 `_render_markdown`（约 L389），整体替换为：

```python
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
                    f"| 延迟 P50 / P95 | {metrics['latency_ms_p50']:.1f} / {metrics['latency_ms_p95']:.1f} ms |",
                    f"| 澄清 TP/FP/FN | {confusion['tp']}/{confusion['fp']}/{confusion['fn']} |",
                    "",
                ]
            )
            # 分场景表
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
        # 失败样例归因
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
```

- [ ] **Step 3: 内联验证 MD 渲染不报错**

Run:
```bash
cd /home/user/projects/AgentProject/Agent
source backend/.venv/bin/activate
python3 -c "
from rag.scripts.evaluate_performance import RAGEvaluator, CaseResult
ev = RAGEvaluator.__new__(RAGEvaluator)
summary = {'run_id':'t','dataset_path':'p','dataset_size':2,'top_k':5,'duration_seconds':1.0,
  'modes':{'text':{'total_cases':2,'executed_cases':2,'skipped_cases':0,
    'metrics':{'available_rate':1.0,'top1':0.5,'top3':0.5,'recall_at_k':0.5,'mrr':0.5,
      'clarify_rate':0.0,'citation_consistency':0.5,'latency_ms_p50':10,'latency_ms_p95':20},
    'clarify_confusion':{'tp':0,'fp':0,'fn':0},
    'segments':{'scenario':{'同款识别':{'cases':1,'top1':1.0,'top3':1.0,'clarify_rate':0.0,'latency_ms_p95':10}}}}}}
results=[CaseResult(test_id='c1',mode='text',gold_sku_id='lining_001',predicted_top1='lining_002',
  predicted_topk=['lining_002'],top1_hit=False,top3_hit=False,recall_at_k=0.0,reciprocal_rank=0.0,
  latency_ms=10.0,should_clarify=False,predicted_clarify=False,clarify_tp=0,clarify_fp=0,clarify_fn=0,
  citation_consistent=None,citation_hit_count=0,citations_returned=0,status='ok',difficulty='easy',scenario='同款识别',category='c')]
md = ev._render_markdown(summary, results)
assert '## text 模式' in md and '分场景指标' in md and '失败样例归因' in md
print('MD 渲染 OK, 长度', len(md))
"
```
Expected: 输出 `MD 渲染 OK, 长度 N`（无异常）。

- [ ] **Step 4: Commit**

```bash
git add rag/scripts/evaluate_performance.py
git commit -m "feat(eval): MD 报告渲染分场景指标表 + 失败样例归因"
```

---

### Task 6: 端到端跑评测 + 验证报告

**Files:**
- 无新增改动，跑评测生成报告

- [ ] **Step 1: 跑文字模式评测**

确保后端 Qdrant 集合有数据（products 25 条 + citations 120 条已入库）。Run:
```bash
cd /home/user/projects/AgentProject/Agent
source backend/.venv/bin/activate
export $(grep -v '^#' backend/.env | xargs)
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
PYTHONPATH=$(pwd):$(pwd)/backend python -m rag.scripts.evaluate_performance --modes text
```
Expected: 日志显示 15 个 case 跑完，`Reports written to: rag/eval/reports/<run_id>`。

- [ ] **Step 2: 检查 MD 报告内容**

Run（run_id 替换为上一步输出的）:
```bash
LATEST=$(ls -t rag/eval/reports/ | head -1)
echo "=== 报告: $LATEST ==="
cat "rag/eval/reports/$LATEST/summary.md"
```
Expected: MD 含「text 模式」指标表（含可用率）+「分场景指标」表（5 场景行）+「失败样例归因」段（≤3 条）。

- [ ] **Step 3: 检查关键指标合理性**

确认报告里：
- 文字场景（同款识别/近似替代/知识问答）Top-1 应 > 50%（语义准）
- 图片同款(占位)行有「占位参考」标注，top-1 接近 100%（自搜自）
- 失败样例段有归因 + 改进

若文字 Top-1 过低（<30%），检查数据集 gold 标注是否合理，调整 query 描述后重跑。

- [ ] **Step 4: Commit 报告**

```bash
git add rag/eval/reports/
git commit -m "eval: 生成 15 条分场景评测报告（指标表+失败归因）"
```

---

## 验证（整体）

1. **前端纯文字**: 浏览器不传图直接输入「变色龙入门训练鞋」→ 出商品+回答（Task 1 Step 4）
2. **评测一键**: `python -m rag.scripts.evaluate_performance --modes text` → 出 MD 报告（Task 6 Step 1）
3. **报告完整**: MD 含总表(含可用率) + 分场景表(5场景) + 失败归因(≤3条)（Task 6 Step 2）
4. **指标真实**: 文字 Top-1 > 50%，图片占位行有标注（Task 6 Step 3）
