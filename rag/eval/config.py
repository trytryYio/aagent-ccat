"""RAGAS 评估配置"""

from pydantic_settings import BaseSettings


class RagasSettings(BaseSettings):
    """RAGAS 评估设置"""
    # 评测 LLM（用于 LLM-as-Judge）
    eval_llm_api_key: str = ""
    eval_llm_base_url: str = "https://api.deepseek.com/v1"
    eval_llm_model: str = "deepseek-chat"

    # 数据集路径
    eval_dataset: str = "rag/eval/datasets/rag_eval_dataset.jsonl"
    e2e_dataset: str = "rag/eval/datasets/rag_e2e_dataset.jsonl"

    # 报告输出
    output_dir: str = "rag/eval/reports"

    model_config = {"env_file": "backend/.env", "extra": "ignore"}


ragas_settings = RagasSettings()