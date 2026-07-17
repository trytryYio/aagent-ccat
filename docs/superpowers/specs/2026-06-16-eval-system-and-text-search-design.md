# 评测体系 + 纯文字检索链路 设计文档

**日期**: 2026-06-16
**分支**: refactor/langgraph-agent
**范围**: 修前端纯文字检索 bug + 建分场景评测体系（指标表 + 失败样例归因）

---

## Context（为什么做）

两个问题驱动：

1. **前端不能纯文字检索**：`useChat.ts` 有两处强制图片——`sendMessage` 找历史消息图片（无则报"请先拍照"）、`startChat` 的 `if (!currentImageId) return`。但后端纯文字链路（plan→embed_text→search→generate）是通的，变色龙查询已验证。这是前端人为拦截，需修。

2. **缺工程闭环的评测**：三条核心链路（找同款/找替代/澄清）后端逻辑都有，但无分场景指标、无失败归因。毕设要体现工程闭环，需：分链路指标表 + 真实失败样例 + 归因改进。

**关键现实**：商品图是 picsum 占位（李宁官网 SPA 爬不到），图片链路 CLIP 检索语义随机。故评测**主走文字链路**（语义准、指标真实），图片链路诚实标注「占位参考」。

---

## 目标 / 非目标

**目标**：
- 前端输入框纯文字可直接检索（无需先传图）
- 评测数据集扩到 ~15 条，覆盖三条链路场景
- 一键跑出 MD 报告：分场景指标表 + 总表 + 3 条失败样例归因
- 指标含 top-1 / top-3 / 可用率 / 澄清准确率

**非目标（YAGNI）**：
- 不做前端评测可视化页面（脚本 + MD 够毕设）
- 不重新爬真实商品图（picsum 占位，标注说明）
- 不做多轮对话评测（单轮检索质量为主）

---

## 设计

### 3.1 前端纯文字检索修复

**文件**: `web/src/composables/useChat.ts`

改动 2 处：
- `sendMessage`（L62-80）：纯文字分支去掉"找历史图片"逻辑。文字非空 → 直接 `addUserMessage(text)` + `startChat(text)`，不强制图片。
- `startChat`（L210）：去掉 `if (!currentImageId) return`。改为根据有无 image_id 走不同路径（有图传 image_id，无图纯文字）。

**API 层**: `web/src/api/chat.ts` 的 `createChat` 已支持 `image_id` 可选（后端 `chat.py:36` 接受 `session_id` 或 `image_id`）。确认 `createChat(sessionId, imageId, text)` 在 imageId 为空时传空字符串即可。

**效果**: 文字框输入"变色龙入门训练鞋"→ 直接检索，无需图片。

### 3.2 评测数据集扩充

**文件**: `rag/eval/datasets/rag_eval_dataset.jsonl`（现 5 条 → 扩到 ~15 条）

场景分布（文字 case 为主）：
| scenario | 条数 | 说明 |
|----------|------|------|
| 同款识别 | 3 | 文字描述某鞋特征，gold=该鞋 SKU |
| 近似替代 | 4 | 按预算/用途查，gold_topk=2-3 个候选 |
| 低置信澄清 | 3 | query 模糊/多义，should_clarify=true |
| 知识问答 | 2 | 问某鞋详情，验证 citation |
| 图片同款（占位） | 3 | image_path 指向 picsum，**标注 scenario="图片同款(占位)"**，指标单独统计 |

每条字段沿用现有结构：`test_id, image_path, query_text, gold_sku_id, gold_topk, should_clarify, expected_citations, difficulty, category, scenario`。

文字 case 的 `image_path` 留空（evaluate_performance 已支持，text 模式不读图）。

### 3.3 评测脚本增强

**文件**: `rag/scripts/evaluate_performance.py`

新增/改动：
- **分场景聚合**: 新增 `_scenario_summary(results) -> dict`，按 `result.scenario` 分组，每组算 top-1/top-3/recall/MRR/可用率/澄清准确率。
- **可用率指标**: `available_rate = executed / total`（现脚本有 executed/skipped，补一个比率）。
- **失败样例挑选**: 新增 `_pick_failure_cases(results, n=3)`，挑 `status != "ok"` 或 `top1_hit=False` 或澄清错判的 case，按代表性排序取 3 条。
- **MD 报告输出**: `_write_reports` 增加 MD 输出，含：总表 + 分场景表 + 失败样例归因。

### 3.4 指标表结构（MD 报告）

**总表**：
| 指标 | 值 |
|------|----|
| 用例数 / 可用数 | 15 / N |
| Top-1 命中率 | x% |
| Top-3 命中率 | x% |
| Recall@3 | x% |
| MRR | x.xx |
| 可用率 | x% |
| 澄清准确率（澄清 case） | x% |
| 平均延迟 | xxx ms |

**分场景表**（每场景一行，同上指标）：
| 场景 | 用例数 | Top-1 | Top-3 | 可用率 | 澄清率 |
|------|--------|-------|-------|--------|--------|
| 同款识别 | 3 | ... | ... | ... | - |
| 近似替代 | 4 | ... | ... | ... | - |
| 低置信澄清 | 3 | ... | ... | ... | ... |
| 知识问答 | 2 | ... | ... | ... | - |
| 图片同款(占位) | 3 | ... | ... | ... | - |

图片同款行加注释：`* 占位图，指标仅参考链路可用性，非语义准确性`。

### 3.5 失败样例归因机制

报告末尾输出 3 条失败样例，每条模板：
```
### 失败 case: <test_id> [<scenario>]
- 输入: <query_text>
- 期望: gold=<sku>, topk=<...>, should_clarify=<bool>
- 实际: predicted_top1=<sku>, top3=<...>, predicted_clarify=<bool>
- 归因: [数据/检索/阈值/LLM] <具体原因>
- 改进: <具体措施>
```

归因维度预设：
- **数据层**: 图片占位语义无关 / 描述太短向量区分度低
- **检索层**: BGE-M3 向量质量 / RRF 融合权重 / top_k 截断
- **阈值层**: clarify 的 score<0.6 或 gap<0.05 阈值不当
- **LLM 层**: 生成未引用候选 / 编造

---

## 验证

1. **前端纯文字**: `cd web && npm run dev`，浏览器输入"变色龙入门训练鞋"（不传图）→ 应直接出商品+回答。
2. **评测一键跑**: `PYTHONPATH=. python -m rag.scripts.evaluate_performance --mode text` → 输出 `rag/eval/reports/perf_*.md` + `.json`。
3. **报告检查**: MD 含总表 + 分场景表 + 3 失败样例；文字场景 top-1 应 > 60%（语义准）。
4. **图片占位诚实**: 图片同款行有「占位参考」注释，top-1 可能低但可用率 100%。

---

## 风险

- **picsum 图片检索结果随机**: 已通过「文字为主 + 图片标注」规避，答辩话术诚实。
- **失败样例不足 3 条**: 若文字链路表现太好失败不足 3，从「澄清误判」或「top-3 漏召回」补充。
- **数据集 gold 标注主观**: 近似替代的 gold_topk 由人工定，报告注明标注依据。
