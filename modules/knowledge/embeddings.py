from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Protocol

import numpy as np

logger = logging.getLogger(__name__)


class EmbedderSettings(Protocol):
    embedding_model: str
    embedding_device: str
    embedding_batch_size: int
    embedding_query_prefix: str
    embedding_dim_hint: int


class Embedder:
    """懒加载的 sentence-transformers 封装（CPU 友好）。"""

    def __init__(self, settings: EmbedderSettings) -> None:
        self.settings = settings
        self._model = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self.settings.embedding_model

    def _ensure_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    model = self._load_model()
                    model.eval()
                    self._model = model
        return self._model

    def _load_model(self):
        """优先使用本地缓存，缺失时再联网下载。

        transformers 5.x 会在加载时联网探测仓库元数据，网络不通时会把已缓存的
        模型误判为“缺少 processing class”，因此这里先以 local_files_only 方式加载。
        """
        from sentence_transformers import SentenceTransformer

        model_id = self.settings.embedding_model
        kwargs = {"device": self.settings.embedding_device}
        try:
            model = SentenceTransformer(model_id, local_files_only=True, **kwargs)
            logger.info("已从本地缓存加载 embedding 模型 %s", model_id)
            return model
        except Exception as exc:  # noqa: BLE001 - 缓存缺失/不完整可能抛不同异常，统一回退在线下载
            logger.warning("本地缓存不可用（%s），改为在线下载 %s", exc, model_id)

        try:
            return SentenceTransformer(model_id, **kwargs)
        except Exception as exc:
            raise RuntimeError(
                f"无法加载 embedding 模型 {model_id}：{exc}。请检查网络连通性，"
                "或先把模型下载到本地缓存；"
                "国内网络可设置 HF_ENDPOINT=https://hf-mirror.com 后重试。"
            ) from exc

    @property
    def dimension(self) -> int:
        if self.settings.embedding_dim_hint:
            return self.settings.embedding_dim_hint
        return int(self._ensure_model().get_sentence_embedding_dimension())

    def encode_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        vectors = model.encode(
            list(texts),
            batch_size=self.settings.embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(vectors, dtype=np.float32).tolist()

    def encode_query(self, text: str) -> list[float]:
        model = self._ensure_model()
        prefix = self.settings.embedding_query_prefix or ""
        vector = model.encode(
            [prefix + text],
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(vector, dtype=np.float32)[0].tolist()
