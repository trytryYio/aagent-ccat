from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_max_tokens: int = 2048
    llm_temperature: float = 0.7
    llm_timeout: int = 60

    # LLM fallback（主模型失败时切备用）
    llm_fallback_api_key: str = ""       # 空则复用 llm_api_key
    llm_fallback_base_url: str = ""      # 空则复用 llm_base_url
    llm_fallback_model: str = ""         # 空则不启用 fallback

    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection_image: str = "product_images"
    qdrant_collection_text: str = "product_knowledge"

    # Redis 配置（用于会话 + LangGraph 状态持久化）
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    redis_enabled: bool = True
    redis_key_prefix: str = "agent"

    max_image_size: int = 10485760
    allowed_extensions: str = "jpg,jpeg,png,webp"

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
