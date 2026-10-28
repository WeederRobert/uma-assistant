from __future__ import annotations

from .loaders import LoadedDocument, Paragraph
from .models import Chunk

_BREAK_MARKERS = ("\n\n", "。", "！", "？", ". ", "! ", "? ", "\n", "；", "; ")


def chunk_document(doc: LoadedDocument, chunk_size: int = 800, overlap: int = 120) -> list[Chunk]:
    if chunk_size < 100:
        raise ValueError("chunk_size 必须 >= 100")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_overlap 必须满足 0 <= overlap < chunk_size")

    chunks: list[Chunk] = []
    for paragraph in doc.paragraphs:
        prefix = f"[{paragraph.heading}]\n" if paragraph.heading else ""
        budget = max(chunk_size - len(prefix), 200)
        for piece in _split_windows(paragraph.text, budget, overlap):
            chunks.append(
                Chunk(
                    text=f"{prefix}{piece}",
                    index=len(chunks),
                    page=paragraph.page,
                    heading=paragraph.heading,
                )
            )

    return _merge_small_tail(chunks, chunk_size)


def _split_windows(text: str, size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    windows: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + size, length)
        if end < length:
            cut = _last_break(text, start, end)
            if cut is not None:
                end = cut
        piece = text[start:end].strip()
        if piece:
            windows.append(piece)
        if end >= length:
            break
        next_start = end - overlap
        start = next_start if next_start > start else start + 1
    return windows


def _last_break(text: str, start: int, end: int) -> int | None:
    search_from = start + int((end - start) * 0.6)
    best = None
    for marker in _BREAK_MARKERS:
        position = text.rfind(marker, search_from, end)
        if position != -1:
            candidate = position + len(marker)
            if best is None or candidate > best:
                best = candidate
    return best


def _merge_small_tail(chunks: list[Chunk], chunk_size: int) -> list[Chunk]:
    """把极短的尾部块并入上一块，避免产生无意义的碎片。"""
    if len(chunks) < 2:
        return chunks

    merged: list[Chunk] = []
    for chunk in chunks:
        can_merge = (
            merged
            and len(chunk.text) < chunk_size * 0.25
            and merged[-1].page == chunk.page
            and merged[-1].heading == chunk.heading
            and len(merged[-1].text) + len(chunk.text) <= chunk_size * 1.2
        )
        if can_merge:
            previous = merged[-1]
            previous.text = f"{previous.text}\n{chunk.text}"
        else:
            merged.append(chunk)

    for index, chunk in enumerate(merged):
        chunk.index = index
    return merged


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    paragraph = Paragraph(text=text)
    return _split_windows(paragraph.text, chunk_size, overlap)
