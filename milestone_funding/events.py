"""事件模型：字段与取值对齐 contracts/domain.json 的事件契约。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contracts" / "domain.json"


@lru_cache(maxsize=1)
def load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


class ContractViolation(ValueError):
    """事件不满足领域契约。"""


def _validate(event_id, occurred_at, entity_id, version, payload) -> None:
    if not isinstance(event_id, str) or not event_id:
        raise ContractViolation("event_id 必须是全局唯一的非空字符串")
    try:
        parsed = datetime.fromisoformat(occurred_at)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(f"occurred_at 必须是带时区时间，实际为 {occurred_at!r}") from exc
    if parsed.tzinfo is None:
        raise ContractViolation("occurred_at 必须携带时区")
    if not isinstance(entity_id, str) or not entity_id:
        raise ContractViolation("entity_id 必须是非空的领域实体标识")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ContractViolation("version 必须是正整数")
    if not isinstance(payload, dict) or "type" not in payload:
        raise ContractViolation("payload 必须是包含 type 的业务对象")


@dataclass(frozen=True)
class Event:
    """一条不可变领域事件。字段集合固定为契约中的五个键。"""

    event_id: str
    occurred_at: str
    entity_id: str
    version: int
    payload: dict

    def __post_init__(self):
        _validate(self.event_id, self.occurred_at, self.entity_id, self.version, self.payload)

    @classmethod
    def from_dict(cls, raw: dict) -> "Event":
        contract_keys = set(load_contract()["event_contract"])
        if not isinstance(raw, dict) or set(raw) != contract_keys:
            actual = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
            raise ContractViolation(f"事件字段必须恰好为 {sorted(contract_keys)}，实际为 {actual}")
        return cls(
            event_id=raw["event_id"],
            occurred_at=raw["occurred_at"],
            entity_id=raw["entity_id"],
            version=raw["version"],
            payload=raw["payload"],
        )

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "entity_id": self.entity_id,
            "version": self.version,
            "payload": self.payload,
        }
