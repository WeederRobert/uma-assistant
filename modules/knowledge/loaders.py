from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt"}


class UnsupportedFileType(ValueError):
    pass


class DocumentParseError(ValueError):
    pass


@dataclass
class Paragraph:
    text: str
    page: int | None = None
    heading: str | None = None


@dataclass
class LoadedDocument:
    filename: str
    extension: str
    paragraphs: list[Paragraph] = field(default_factory=list)
    size_bytes: int = 0
    sha256: str = ""
    raw_text: str = ""

    @property
    def char_count(self) -> int:
        return len(self.raw_text)


def detect_extension(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".mdown":
        suffix = ".md"
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileType(
            f"不支持的文件类型: {suffix or '(无扩展名)'}，仅支持 pdf / md / markdown / txt"
        )
    return suffix


def decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def load_document(data: bytes, filename: str) -> LoadedDocument:
    extension = detect_extension(filename)
    doc = LoadedDocument(
        filename=filename,
        extension=extension,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )

    if extension == ".pdf":
        doc.paragraphs = _load_pdf(data, filename)
    else:
        doc.paragraphs = _load_text_document(decode_bytes(data), extension)

    doc.raw_text = "\n\n".join(p.text for p in doc.paragraphs)
    return doc


def _load_pdf(data: bytes, filename: str) -> list[Paragraph]:
    import io

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # pypdf raises a variety of errors
        raise DocumentParseError(f"无法解析 PDF {filename}: {exc}") from exc

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise DocumentParseError(f"PDF {filename} 已加密，无法读取") from exc

    paragraphs: list[Paragraph] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except (ValueError, KeyError, TypeError) as exc:  # 单页解析失败不应中断整份文档
            logger.warning("第 %d 页文本提取失败: %s", page_number, exc)
            text = ""
        text = _normalize(text)
        if text:
            paragraphs.extend(
                Paragraph(text=block, page=page_number) for block in _split_blocks(text)
            )

    if not paragraphs:
        raise DocumentParseError(f"PDF {filename} 未提取到文本，可能是扫描件（需要 OCR）")
    return paragraphs


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _load_text_document(text: str, extension: str) -> list[Paragraph]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs: list[Paragraph] = []

    if extension in {".md", ".markdown"}:
        heading_stack: list[tuple[int, str]] = []
        buffer: list[str] = []

        def flush() -> None:
            block = _normalize("\n".join(buffer))
            buffer.clear()
            if not block:
                return
            heading = " > ".join(h for _, h in heading_stack) or None
            paragraphs.extend(
                Paragraph(text=part, heading=heading) for part in _split_blocks(block)
            )

        in_fence = False
        for line in text.split("\n"):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                buffer.append(line)
                continue
            match = None if in_fence else _HEADING_RE.match(line)
            if match:
                flush()
                level = len(match.group(1))
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, match.group(2).strip()))
                continue
            buffer.append(line)
        flush()
        return paragraphs

    normalized = _normalize(text)
    return [Paragraph(text=part) for part in _split_blocks(normalized)]


def _normalize(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_blocks(text: str) -> list[str]:
    """按空行切块，过长的块再按单个换行切分。"""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    result: list[str] = []
    for block in blocks:
        if len(block) <= 2000:
            result.append(block)
            continue
        result.extend(line.strip() for line in block.split("\n") if line.strip())
    return result
