"""把 docs/kb 下的知识库素材导入正在运行的应用。

示例：
    uv run python scripts/import_kb.py
    uv run python scripts/import_kb.py --base-url http://127.0.0.1:8737 --glob "docs/kb/0*.md"

同名文档内容变化时，脚本会先删掉旧版本再导入，避免旧切块继续参与检索；
需要保留历史版本时加 `--keep-stale`。

需要先启动应用（`scripts/dev.ps1` 或 `uv run python -m app.desktop`），脚本只调用公开 HTTP 接口。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.kernel.console import enable_utf8_console  # noqa: E402


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prune_stale(client: httpx.Client, base: str, paths: list[Path]) -> list[str]:
    """删除与本地文件同名、但内容已变化的旧文档，返回被删的 id。"""
    response = client.get(f"{base}/api/knowledge/documents")
    response.raise_for_status()
    payload = response.json()
    documents = payload["documents"] if isinstance(payload, dict) else payload

    by_name: dict[str, list[dict]] = {}
    for doc in documents:
        by_name.setdefault(doc["filename"], []).append(doc)

    removed: list[str] = []
    for path in paths:
        current = sha256_of(path)
        stale = [d for d in by_name.get(path.name, []) if d["sha256"] != current]
        for doc in stale:
            client.delete(f"{base}/api/knowledge/documents/{doc['id']}").raise_for_status()
            removed.append(doc["filename"])
    return removed


def main(argv: list[str] | None = None) -> int:
    enable_utf8_console()
    parser = argparse.ArgumentParser(description="批量导入知识库素材到运行中的应用")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8737",
        help="应用地址，默认 http://127.0.0.1:8737",
    )
    parser.add_argument("--glob", default="docs/kb/*.md", help="文件匹配模式，相对项目根目录")
    parser.add_argument("--timeout", type=float, default=600.0, help="单次请求超时秒数")
    parser.add_argument(
        "--keep-stale",
        action="store_true",
        help="保留同名旧版本文档（默认会删掉内容已变化的旧版本）",
    )
    args = parser.parse_args(argv)

    paths = sorted(PROJECT_ROOT.glob(args.glob))
    if not paths:
        print(f"没有匹配到文件: {args.glob}")
        return 1

    base = args.base_url.rstrip("/")
    files = [("files", (p.name, p.read_bytes(), "text/markdown")) for p in paths]
    try:
        with httpx.Client(timeout=args.timeout) as client:
            if not args.keep_stale:
                for name in prune_stale(client, base, paths):
                    print(f"替换 {name}: 已删除旧版本")
            response = client.post(f"{base}/api/knowledge/documents", files=files)
            response.raise_for_status()
            payload = response.json()
            info = client.get(f"{base}/api/system/info").json()
    except httpx.HTTPError as exc:
        print(f"导入失败: {exc}")
        return 1

    for doc in payload["documents"]:
        mark = " (已存在，跳过内容)" if doc.get("duplicate") else ""
        print(f"导入 {doc['filename']}: {doc['chunk_count']} 个切块{mark}")
    for item in payload["skipped"]:
        print(f"跳过 {item['filename']}: {item['reason']}")

    print(json.dumps({"data_version": info["data"]["version"]}, ensure_ascii=False))
    return 1 if payload["skipped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
