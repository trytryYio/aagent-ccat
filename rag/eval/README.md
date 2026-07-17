# RAGAS 评估模块使用说明

## 安装

```bash
source backend/.venv/bin/activate
pip install ragas datasets
```

## 运行

```bash
# 从项目根目录
PYTHONPATH=$(pwd) python rag/eval/ragas_eval.py

# 指定数据集
PYTHONPATH=$(pwd) python rag/eval/ragas_eval.py --dataset rag/eval/datasets/rag_eval_dataset.jsonl

# 指定输出目录
PYTHONPATH=$(pwd) python rag/eval/ragas_eval.py --output-dir rag/eval/reports
```

## 指标说明

| 指标 | 范围 | 说明 |
|------|------|------|
| Faithfulness | 0~1 | 答案忠于检索内容的程度 |
| Answer Relevancy | 0~1 | 答案直接回答问题的程度 |
| Context Recall | 0~1 | 检索覆盖关键信息的程度 |
| Context Precision | 0~1 | 有用信息排在检索前列的程度 |

## 输出

- `ragas_report_{run_id}.json` — 结构化指标数据
- `ragas_report_{run_id}.md` — 可读报告