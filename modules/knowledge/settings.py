from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class KnowledgeSettings(BaseSettings):
    """知识库模块配置。

    沿用 ``rag-service`` 的环境变量名（DEEPSEEK_* / EMBEDDING_*），方便迁移。
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 检索与切块
    collection_name: str = "uma_documents"
    keep_uploads: bool = True
    max_upload_mb: int = 50
    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 6
    min_score: float = 0.0

    # 向量化
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 32
    embedding_query_prefix: str = "为这个句子生成表示以用于检索相关文章："
    embedding_dim_hint: int = 0

    # 大模型（OpenAI 兼容）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1024
    request_timeout: float = 120.0

    # 结构化数据优先：问赛道 / 大赛 / 技能 / 卡池 / 图鉴时先用本地模块的接口查库。
    # 留空则用「内核 settings 的 host:port」，模块之间只走 HTTP，不互相 import。
    structured_base_url: str = ""
    structured_timeout: float = 6.0
    structured_enabled: bool = True
