from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .database import Database

UNKNOWN = "unknown"

META_VERSION = "data.version"
META_GAME_VERSION = "data.game_version"
META_UPDATED_AT = "data.updated_at"
META_SOURCE = "data.source"
META_DATASETS = "data.datasets"


@dataclass(frozen=True, slots=True)
class DataVersion:
    """当前结构化数据的版本信息。

    助手的所有"事实性"结论都应能追溯到这里的版本号；界面必须始终展示它。
    """

    version: str = UNKNOWN
    game_version: str | None = None
    updated_at: str | None = None
    source: str | None = None
    datasets: dict[str, int] = field(default_factory=dict)

    @property
    def known(self) -> bool:
        return self.version != UNKNOWN

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "known": self.known,
            "game_version": self.game_version,
            "updated_at": self.updated_at,
            "source": self.source,
            "datasets": self.datasets,
        }

    def label(self) -> str:
        if not self.known:
            return "数据版本未知"
        parts = [f"数据版本 {self.version}"]
        if self.game_version:
            parts.append(f"游戏 {self.game_version}")
        if self.updated_at:
            parts.append(f"更新于 {self.updated_at[:19].replace('T', ' ')}")
        return " · ".join(parts)


def read_data_version(db: Database) -> DataVersion:
    datasets: dict[str, int] = {}
    raw = db.get_meta(META_DATASETS)
    if raw:
        try:
            datasets = {str(k): int(v) for k, v in json.loads(raw).items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            datasets = {}
    return DataVersion(
        version=db.get_meta(META_VERSION, UNKNOWN) or UNKNOWN,
        game_version=db.get_meta(META_GAME_VERSION),
        updated_at=db.get_meta(META_UPDATED_AT),
        source=db.get_meta(META_SOURCE),
        datasets=datasets,
    )


def write_data_version(
    db: Database,
    *,
    version: str,
    game_version: str | None = None,
    source: str | None = None,
    datasets: dict[str, int] | None = None,
) -> DataVersion:
    updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    db.set_meta(META_VERSION, version)
    db.set_meta(META_UPDATED_AT, updated_at)
    if game_version is not None:
        db.set_meta(META_GAME_VERSION, game_version)
    if source is not None:
        db.set_meta(META_SOURCE, source)
    if datasets is not None:
        db.set_meta(META_DATASETS, json.dumps(datasets, ensure_ascii=False, sort_keys=True))
    return read_data_version(db)
