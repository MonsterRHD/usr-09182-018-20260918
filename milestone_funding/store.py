"""事件存储：只增不改，落实两条不变量。

- 同一事件只能生效一次：重复的 event_id 直接拒绝；
- 更正不得覆盖原始事实：每个实体的事件版本必须严格递增，
  存储不提供任何更新/删除接口，更正只能以新版本事件追加。
"""

from __future__ import annotations

from .events import Event


class IdempotencyViolation(RuntimeError):
    """同一事件被重复提交。"""


class VersionConflict(RuntimeError):
    """事件版本未按实体严格递增，疑似覆盖或乱序。"""


class EventStore:
    def __init__(self):
        self._events: list[Event] = []
        self._seen_ids: set[str] = set()
        self._versions: dict[str, int] = {}

    def append(self, event: Event) -> Event:
        if event.event_id in self._seen_ids:
            raise IdempotencyViolation(f"事件 {event.event_id} 已生效，同一事件只能生效一次")
        expected = self._versions.get(event.entity_id, 0) + 1
        if event.version != expected:
            raise VersionConflict(
                f"实体 {event.entity_id} 期望版本 {expected}，实际 {event.version}；"
                "更正须以新版本追加，不得覆盖原始事实"
            )
        self._events.append(event)
        self._seen_ids.add(event.event_id)
        self._versions[event.entity_id] = event.version
        return event

    def append_dict(self, raw: dict) -> Event:
        return self.append(Event.from_dict(raw))

    def current_version(self, entity_id: str) -> int:
        return self._versions.get(entity_id, 0)

    def events_for(self, entity_id: str) -> list[Event]:
        return [e for e in self._events if e.entity_id == entity_id]

    def all(self) -> list[Event]:
        return list(self._events)

    def __len__(self) -> int:
        return len(self._events)
