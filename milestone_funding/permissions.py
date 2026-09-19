"""权限视图：落实"权限不足时不得返回敏感字段"。

预算、金额、知识产权归属等字段仅对具备完整权限的角色开放；
技术评审、外部专家及未识别角色只能看到技术与进度信息。
"""

from __future__ import annotations

from dataclasses import asdict

FULL_ACCESS_ROLES = frozenset({"项目负责人", "资助方经办", "财务审批", "审计"})

SENSITIVE_KEYS = frozenset({
    "budget_subjects",   # 预算科目
    "budget",            # 里程碑预算
    "budget_history",    # 预算重估历史
    "amount",            # 拨款/资金义务金额
    "total_disbursed",   # 累计拨付
    "ip_records",        # 知识产权归属
    "share",             # 归属比例
})


def _redact(node):
    if isinstance(node, dict):
        return {k: _redact(v) for k, v in node.items() if k not in SENSITIVE_KEYS}
    if isinstance(node, list):
        return [_redact(item) for item in node]
    return node


def project_view(state, role: str | None) -> dict:
    view = {
        "project_id": state.project_id,
        "status": state.status,
        "route": state.route,
        "goals": list(state.goals),
        "budget_subjects": dict(state.budget_subjects),
        "milestones": [asdict(m) for m in state.milestones.values()],
        "evidences": [asdict(e) for e in state.evidences.values()],
        "failures": [asdict(f) for f in state.failures.values()],
        "reviews": dict(state.reviews),
        "ip_records": dict(state.ip_records),
        "disbursements": [asdict(d) for d in state.disbursements.values()],
        "obligations": [asdict(o) for o in state.obligations.values()],
        "acceptance": dict(state.acceptance) if state.acceptance else None,
    }
    if role in FULL_ACCESS_ROLES:
        return view
    return _redact(view)
