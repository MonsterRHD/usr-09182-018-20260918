"""领域投影：把项目的事件流折叠为当前状态。

所有状态变化都由事件驱动，本模块不做业务校验（校验在服务层），
只负责忠实落账；历史痕迹（预算重估、证据更正）一律保留在 *_history 中。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 拨款审批所需的两个互斥角色：技术评审确认证据完整，财务审批确认资金合规。
APPROVAL_ROLES = ("技术评审", "财务审批")


@dataclass
class Milestone:
    milestone_id: str
    title: str
    goal_ref: str
    required_evidence: list
    budget: int
    status: str = "已承诺"
    budget_history: list = field(default_factory=list)  # 每次重估留痕


@dataclass
class Evidence:
    evidence_id: str
    milestone_id: str
    kind: str
    summary: str
    status: str = "已提交"
    version: int = 1
    history: list = field(default_factory=list)  # 更正前的原文留痕


@dataclass
class Failure:
    failure_id: str
    milestone_id: str
    experiment: str
    lesson: str  # 失败的价值：沉淀的经验，供路线变更与后续项目引用
    linked_route_change: bool = False


@dataclass
class Disbursement:
    disbursement_id: str
    milestone_id: str
    amount: int
    requester: str
    status: str = "已申请"
    approvals: list = field(default_factory=list)
    evidence_refs: list = field(default_factory=list)  # 执行时快照：当时批准的证据版本


@dataclass
class Obligation:
    obligation_id: str
    kind: str  # 设备处置 / 未用资金
    detail: str
    amount: int = 0
    status: str = "待履行"
    settled_note: str = ""


@dataclass
class ProjectState:
    project_id: str
    status: str = "未登记"  # 未登记/已登记/在研/暂停/验收通过
    goals: list = field(default_factory=list)
    budget_subjects: dict = field(default_factory=dict)
    route: str = ""
    milestones: dict = field(default_factory=dict)
    evidences: dict = field(default_factory=dict)
    failures: dict = field(default_factory=dict)
    reviews: dict = field(default_factory=dict)
    ip_records: dict = field(default_factory=dict)
    disbursements: dict = field(default_factory=dict)
    obligations: dict = field(default_factory=dict)
    acceptance: dict | None = None


def apply_event(state: ProjectState, event) -> ProjectState:
    p = event.payload
    t = p.get("type")

    if t == "项目登记":
        state.status = "已登记"
        state.goals = list(p["goals"])
        state.budget_subjects = dict(p["budget_subjects"])
        state.route = p["route"]
    elif t == "阶段承诺":
        for m in p["milestones"]:
            state.milestones[m["milestone_id"]] = Milestone(
                milestone_id=m["milestone_id"],
                title=m["title"],
                goal_ref=m["goal_ref"],
                required_evidence=list(m["required_evidence"]),
                budget=m["budget"],
            )
        state.status = "在研"
    elif t == "证据提交":
        state.evidences[p["evidence_id"]] = Evidence(
            evidence_id=p["evidence_id"],
            milestone_id=p["milestone_id"],
            kind=p["kind"],
            summary=p["summary"],
        )
    elif t == "证据采纳":
        state.evidences[p["evidence_id"]].status = "已采纳"
    elif t == "证据更正":
        ev = state.evidences[p["evidence_id"]]
        ev.history.append({"version": ev.version, "summary": ev.summary})
        ev.summary = p["new_summary"]
        ev.version += 1
    elif t == "试验失败":
        state.failures[p["failure_id"]] = Failure(
            failure_id=p["failure_id"],
            milestone_id=p["milestone_id"],
            experiment=p["experiment"],
            lesson=p["lesson"],
        )
    elif t == "外部评审":
        state.reviews[p["review_id"]] = {"expert": p["expert"], "conclusion": p["conclusion"]}
    elif t == "知识产权登记":
        state.ip_records[p["ip_id"]] = {
            "owner": p["owner"],
            "share": p["share"],
            "scope": p["scope"],
        }
    elif t == "路线变更":
        state.route = p["to_route"]
        state.failures[p["failure_id"]].linked_route_change = True
    elif t == "承诺重估":
        m = state.milestones[p["milestone_id"]]
        m.budget_history.append({"from": m.budget, "to": p["new_budget"], "reason": p["reason"]})
        m.budget = p["new_budget"]
    elif t == "拨款申请":
        state.disbursements[p["disbursement_id"]] = Disbursement(
            disbursement_id=p["disbursement_id"],
            milestone_id=p["milestone_id"],
            amount=p["amount"],
            requester=p["requester"],
        )
    elif t == "拨款批准":
        d = state.disbursements[p["disbursement_id"]]
        d.approvals.append({"role": p["role"], "actor": p["actor"]})
        roles = {a["role"] for a in d.approvals}
        d.status = "可执行" if set(APPROVAL_ROLES) <= roles else "审批中"
    elif t == "拨款执行":
        d = state.disbursements[p["disbursement_id"]]
        d.status = "已执行"
        d.evidence_refs = list(p["evidence_refs"])
    elif t == "里程碑达成":
        state.milestones[p["milestone_id"]].status = "已达成"
    elif t == "项目暂停":
        state.status = "暂停"
    elif t == "处置义务":
        state.obligations[p["obligation_id"]] = Obligation(
            obligation_id=p["obligation_id"],
            kind=p["kind"],
            detail=p["detail"],
            amount=p.get("amount", 0),
        )
    elif t == "义务履行":
        o = state.obligations[p["obligation_id"]]
        o.status = "已履行"
        o.settled_note = p["note"]
    elif t == "验收通过":
        state.status = "验收通过"
        state.acceptance = {
            "summary": p["summary"],
            "preserved_failures": p["preserved_failures"],
            "total_disbursed": p["total_disbursed"],
        }
    else:
        raise ValueError(f"未知事件类型: {t!r}")
    return state
