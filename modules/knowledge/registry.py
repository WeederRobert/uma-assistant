from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

from .models import DocumentInfo

logger = logging.getLogger(__name__)


class DocumentRegistry:
    """文档元数据登记表（JSON 落盘），用于列出/去重/删除。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._documents: dict[str, DocumentInfo] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for item in raw.get("documents", []):
            try:
                doc = DocumentInfo(**item)
            except (TypeError, ValueError) as exc:  # 跳过损坏/旧版本的条目
                logger.warning("跳过无法解析的文档记录: %s", exc)
                continue
            self._documents[doc.id] = doc

    def _persist(self) -> None:
        payload = {
            "documents": [doc.model_dump() for doc in self._documents.values()],
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, doc_id: str) -> DocumentInfo | None:
        with self._lock:
            return self._documents.get(doc_id)

    def find_by_sha(self, sha256: str) -> DocumentInfo | None:
        with self._lock:
            for doc in self._documents.values():
                if doc.sha256 == sha256:
                    return doc
        return None

    def add(self, doc: DocumentInfo) -> None:
        with self._lock:
            self._documents[doc.id] = doc
            self._persist()

    def remove(self, doc_id: str) -> DocumentInfo | None:
        with self._lock:
            doc = self._documents.pop(doc_id, None)
            if doc is not None:
                self._persist()
            return doc

    def list(self) -> list[DocumentInfo]:
        with self._lock:
            return sorted(self._documents.values(), key=lambda d: d.created_at, reverse=True)

    def total_chunks(self) -> int:
        with self._lock:
            return sum(doc.chunk_count for doc in self._documents.values())
