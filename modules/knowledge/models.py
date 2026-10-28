from __future__ import annotations

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    text: str
    index: int
    page: int | None = None
    heading: str | None = None


class DocumentInfo(BaseModel):
    id: str
    filename: str
    extension: str
    size_bytes: int
    sha256: str
    chunk_count: int
    created_at: str
    duplicate: bool = False


class IngestResponse(BaseModel):
    documents: list[DocumentInfo]
    skipped: list[dict] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int | None = None
    doc_ids: list[str] | None = None
    min_score: float | None = None


class Source(BaseModel):
    index: int
    doc_id: str
    filename: str
    chunk_index: int
    page: int | None = None
    heading: str | None = None
    score: float
    snippet: str


class SearchResponse(BaseModel):
    query: str
    sources: list[Source]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    top_k: int | None = None
    doc_ids: list[str] | None = None
    min_score: float | None = None
    history: list[dict] = Field(default_factory=list)


class Usage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    model: str
    usage: Usage | None = None
    data_version: str
    # 结构化查询结果：命中的话题、来源接口、涉及的表、数据版本
    structured: dict | None = None
    # ok（走结构化 + LLM）/ no_llm（只有结构化事实与文档片段）/ documents_only（结构化没命中）
    mode: str = "ok"
