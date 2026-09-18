"""仅追加的事件存储。

实现契约不变量：
- 同一事件只能生效一次（event_id 全局唯一）；
- 同一实体 version 严格递增且连续；
- occurred_at 必须带时区，事件一经追加不可变。
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .errors import DuplicateEventError, InvalidEventError, VersionConflictError

EVENT_KEYS = ("event_id", "occurred_at", "entity_id", "version", "actor_id", "payload")
REQUIRED_PAYLOAD_TYPE = "type"


def parse_occurred_at(value: str) -> datetime:
    """解析带时区的时间字符串；无时区或格式非法即拒绝。"""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise InvalidEventError(f"occurred_at 必须带时区: {value!r}")
    return dt


def event_fingerprint(event: dict) -> str:
    """单个事件内容的指纹（用于证据快照等需要指向事件版本的地方）。"""
    canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class EventStore:
    """内存事件存储；可从/向 fixtures 使用的 JSON 格式导入导出。"""

    _events: list[dict] = field(default_factory=list)
    _seen_ids: dict[str, bool] = field(default_factory=dict)
    _next_global_seq: int = 0

    def append(self, event: dict) -> dict:
        self._validate_shape(event)
        event_id = event["event_id"]
        if event_id in self._seen_ids:
            raise DuplicateEventError(f"事件 {event_id} 已生效，不能重复追加")
        entity = event["entity_id"]
        version = event["version"]
        expected = self.current_version(entity) + 1
        if version != expected:
            raise VersionConflictError(
                f"实体 {entity} 版本冲突：期望 {expected}，收到 {version}"
            )
        stored = json.loads(json.dumps(event, ensure_ascii=False))  # 深拷贝，防外部突变
        self._seen_ids[event_id] = True
        self._events.append(stored)
        self._next_global_seq += 1
        return stored

    def append_many(self, events: Iterable[dict]) -> list[dict]:
        out: list[dict] = []
        for e in events:
            out.append(self.append(e))
        return out

    def stream(self, entity_id: str | None = None) -> list[dict]:
        if entity_id is None:
            return [json.loads(json.dumps(e, ensure_ascii=False)) for e in self._events]
        return [
            json.loads(json.dumps(e, ensure_ascii=False))
            for e in self._events
            if e["entity_id"] == entity_id
        ]

    def all_events(self) -> list[dict]:
        return self.stream(None)

    def current_version(self, entity_id: str) -> int:
        return max(
            (e["version"] for e in self._events if e["entity_id"] == entity_id),
            default=0,
        )

    def load_json_file(self, path: str | Path) -> None:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise InvalidEventError("fixtures 应为事件数组")
        self.append_many(raw)

    def export_json(self) -> str:
        return json.dumps(self.all_events(), ensure_ascii=False, indent=2) + "\n"

    def save_json_file(self, path: str | Path) -> None:
        Path(path).write_text(self.export_json(), encoding="utf-8")

    # -- 校验 ----------------------------------------------------------------

    def _validate_shape(self, event: dict) -> None:
        missing = [k for k in EVENT_KEYS if k not in event]
        if missing:
            raise InvalidEventError(f"事件缺少契约字段: {missing}")
        extra = set(event) - set(EVENT_KEYS)
        if extra:
            raise InvalidEventError(f"事件含未声明字段: {sorted(extra)}")
        if not isinstance(event["event_id"], str) or not event["event_id"]:
            raise InvalidEventError("event_id 必须为非空字符串")
        parse_occurred_at(event["occurred_at"])
        if not isinstance(event["entity_id"], str) or not event["entity_id"]:
            raise InvalidEventError("entity_id 必须为非空字符串")
        if not isinstance(event["version"], int) or isinstance(event["version"], bool) or event["version"] < 1:
            raise InvalidEventError("version 必须为正整数")
        if not isinstance(event["actor_id"], str) or not event["actor_id"]:
            raise InvalidEventError("actor_id 必须为非空字符串")
        payload = event["payload"]
        if not isinstance(payload, dict) or not isinstance(payload.get(REQUIRED_PAYLOAD_TYPE), str):
            raise InvalidEventError("payload 必须为含 type 字符串的对象")
