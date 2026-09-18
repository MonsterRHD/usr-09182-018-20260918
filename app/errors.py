class DomainError(Exception):
    """所有领域规则冲突的基类。"""


class DuplicateEventError(DomainError):
    """event_id 已生效过——同一事件只能生效一次。"""


class VersionConflictError(DomainError):
    """同一实体 version 必须严格递增且连续。"""


class InvalidEventError(DomainError):
    """事件缺少契约字段或时间格式非法。"""


class BusinessRuleError(DomainError):
    """命令违反业务闸门（证据/职责分离/状态/预算等）。"""


class PermissionDeniedError(DomainError):
    """角色无权执行该命令。"""
