# 05 - RAGAS 质量评估

## 本节目标

学完本节你能够：理解 RAGAS 四维评估指标的含义，掌握如何对 RAG 系统进行自动化质量评估。

---

## RAGAS 评估原理

RAGAS（Retrieval Augmented Generation Assessment）的核心思想是 **LLM-as-Judge**——让大语言模型作为评卷老师，对 RAG 系统的输出进行打分。

```mermaid
flowchart LR
    subgraph Input["评估输入"]
        A["question\n(用户问题)"]
        B["answer\n(系统回答)"]
        C["contexts\n(检索文档)"]
        D["ground_truth\n(可选参考答案)"]
    end

    subgraph LLM_as_Judge["LLM-as-Judge 评估"]
        E["Faithfulness\n忠实度评估"]
        F["Answer Relevancy\n相关性评估"]
        G["Context Recall\n召回率评估"]
        H["Context Precision\n精确度评估"]
    end

    subgraph Output["评估输出"]
        I["各项指标得分\n(0~1)"]
        J["综合报告"]
    end

    Input --> LLM_as_Judge
    LLM_as_Judge --> Output
```

## 四维指标详解

```mermaid
mindmap
  root((RAGAS 四维指标))
    忠实度 Faithfulness
      答案是否忠于检索内容
      拆解答案为声明
      判断每个声明是否有文档支撑
      得分 = 支撑数 / 总数
    答案相关性 Answer Relevancy
      是否直接回答问题
      从答案反推问题
      与原问题算语义相似度
    上下文召回 Context Recall
      检索是否漏掉关键信息
      需要参考答案
      得分 = 命中数 / 总要点数
    上下文精确 Context Precision
      有用信息是否排在前列
      按位置加权
      噪音越靠后越好
```

## 评估流水线

```python
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_recall,
    context_precision,
)
from ragas.llms import LangchainLLMWrapper
from langchain_openai import ChatOpenAI

# 1. 准备数据
data = {
    "question": ["量子纠缠是什么？"],
    "answer": ["量子纠缠是两个粒子间的关联现象..."],
    "contexts": [["量子纠缠是量子力学中的特殊关联..."]],
    "ground_truth": ["量子纠缠是一种量子力学现象"],
}
dataset = Dataset.from_dict(data)

# 2. 配置评估 LLM
eval_llm = ChatOpenAI(
    model="deepseek-chat",
    api_key="...",
    base_url="https://api.deepseek.com/v1",
    temperature=0.1,
)

# 3. 执行评估
metrics = [faithfulness, answer_relevancy, context_recall, context_precision]
result = evaluate(dataset, metrics=metrics, llm=LangchainLLMWrapper(eval_llm))

# 4. 输出结果
print(result)
# {'faithfulness': 0.95, 'answer_relevancy': 0.88, ...}
```

## 评估报告格式

```json
{
  "run_id": "20260614_120000",
  "metrics": {
    "faithfulness": 0.92,
    "answer_relevancy": 0.85,
    "context_recall": 0.78,
    "context_precision": 0.81
  },
  "num_cases": 10
}
```

## 运行评估

```bash
# 安装依赖
source backend/.venv/bin/activate
pip install ragas datasets

# 运行评估
PYTHONPATH=$(pwd) python rag/eval/ragas_eval.py

# 自定义数据集
PYTHONPATH=$(pwd) python rag/eval/ragas_eval.py \\
  --dataset rag/eval/datasets/rag_eval_dataset.jsonl \\
  --output-dir rag/eval/reports
```

## 小结

- RAGAS 用 Faithfulness、Answer Relevancy、Context Recall、Context Precision 四个维度量化评估
- LLM-as-Judge 模式无需人工标注
- 评估结果输出 JSON + Markdown 双格式报告
- 可集成到 CI 流程中做回归测试

