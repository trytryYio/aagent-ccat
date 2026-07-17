# RAG 评测结果汇总

> 最后更新：2026-07-01 13:32

## 最新 RAGAS 四维指标

**数据链路**：阿里云 text-embedding-v3 检索 + hybrid 可选 + 千问 gte-rerank-v2 重排 + DeepSeek 生成

| 指标 | 得分 | 说明 |
|------|------|------|
| **Faithfulness** | 0.816 | ✅ 生成内容忠实于检索上下文 |
| **Context Recall** | 0.404 | ⚠️ 检索覆盖不足（待优化） |
| **Context Precision** | 0.819 | ✅ 检索内容相关性高 |
| **Answer Relevancy** | 0.000 | ❌ 未计算（embedding 配置待修复） |

**测试用例数**：10 条  
**详细数据**：`rag/eval/reports/ragas_real_details_20260701_133223.jsonl`

---

## 最新检索评测

**数据集**：`rag/eval/datasets/rag_eval_dataset.jsonl`（29 条）  
**Top K**：5 | **耗时**：174.2s

### text 模式（纯文本检索）

| 指标 | 值 |
|------|----|
| Top-1 命中率 | 58.62% |
| Top-3 命中率 | 65.52% |
| Recall@K | 70.11% |
| MRR | 0.6839 |
| 澄清率 | 6.90% |
| 延迟 P50 / P95 | 825.5 / 1551.6 ms |

**分场景**：
- ✅ 同款识别：Top-1 100%
- ⚠️ 近似替代：Top-3 20%（向量区分度不足）
- ✅ 知识问答：Top-1 100%

### hybrid 模式（图像 + 文本融合）

| 指标 | 值 |
|------|----|
| Top-1 命中率 | 58.62% |
| Top-3 命中率 | **68.97%** |
| Recall@K | 78.16% |
| MRR | 0.7874 |
| 澄清率 | 27.59% |
| 延迟 P50 / P95 | 2038.6 / 3151.8 ms |

**分场景**：
- ✅ 同款识别：Top-1 100%
- ⚠️ 近似替代：Top-3 40%（比 text 模式好）
- ✅ 知识问答：Top-1 100%

---

## 关键发现

1. **Hybrid 优于 Text**：Top-3 从 65.52% 提升到 68.97%，Recall@K 从 70.11% 提升到 78.16%
2. **同款识别表现最佳**：Top-1 100%，说明 embedding 对同款商品区分度高
3. **近似替代是短板**：Top-3 仅 20%-40%，需要优化向量模型或增加 rerank 精排
4. **Rerank 有效**：千问 gte-rerank-v2 在 hybrid 模式下进一步提升了排序质量
5. **延迟可接受**：P95 在 1.5s-3s 之间，符合电商导购场景需求

---

## 待优化方向

| 问题 | 根因 | 改进方案 |
|------|------|----------|
| Context Recall 0.404 | citation 切分粒度粗，只保留整块 introduction | 细化到段落/句子级，增加"每候选至少一条引用"兜底 |
| Answer Relevancy 0.0 | embedding model 配置问题 | 改用阿里云 text-embedding-v3 作为 RAGAS embedding 模型 |
| 近似替代 Top-3 低 | BGE-M3 向量区分度不足 | 优化产品描述关键词 / 调 RRF 权重 / 增加 reranker 精排 |

---

## 评测脚本

```bash
# RAGAS 四维指标（需要后端运行）
python -m rag.scripts.eval.ragas_eval_real --dataset rag/eval/datasets/rag_eval_real.jsonl

# 检索评测（不需要后端）
python -m rag.scripts.eval.evaluate_performance --modes text hybrid --top-k 5
```

**报告输出位置**：
- RAGAS：`rag/eval/reports/ragas_real_report_<timestamp>.json`
- 检索：`rag/eval/reports/<timestamp>/summary.json`
