"""创新项目里程碑拨付系统。

事件溯源实现：研究目标、技术证据与预算科目经协商形成阶段承诺，
拨款同时受"证据完整"与"职责分离"双重闸门约束；试验失败、方案变更、
外部评审与知识产权归属全部留痕，暂停后保留设备与未用资金处置义务。
"""

from .events import ContractViolation, Event
from .store import EventStore, IdempotencyViolation, VersionConflict
from .service import (
    DomainError,
    EvidenceIncomplete,
    PermissionDenied,
    ProjectService,
    StateError,
)
from .permissions import FULL_ACCESS_ROLES, project_view

__all__ = [
    "ContractViolation",
    "DomainError",
    "Event",
    "EventStore",
    "EvidenceIncomplete",
    "FULL_ACCESS_ROLES",
    "IdempotencyViolation",
    "PermissionDenied",
    "ProjectService",
    "StateError",
    "VersionConflict",
    "project_view",
]
