"""应用服务：所有业务命令在这里校验规则后落成事件。

两条拨付闸门同时满足才放行：
1. 证据完整 —— 里程碑要求的每类证据都有"已采纳"版本；
2. 职责分离 —— 技术评审与财务审批两个角色都批准，且审批人都不是申请人本人。
"""

from __future__ import annotations

from .domain import APPROVAL_ROLES, ProjectState, apply_event
from .events import Event
from .permissions import project_view
from .store import EventStore


class DomainError(RuntimeError):
    """违反业务规则。"""


class StateError(DomainError):
    """项目/单据当前状态不允许该操作。"""


class EvidenceIncomplete(DomainError):
    """里程碑证据不完整，不得拨付。"""


class PermissionDenied(DomainError):
    """违反职责分离或越权。"""


class ProjectService:
    def __init__(self, store: EventStore | None = None):
        self._store = store or EventStore()
        self._states: dict[str, ProjectState] = {}

    @property
    def store(self) -> EventStore:
        return self._store

    def state(self, project_id: str) -> ProjectState:
        if project_id not in self._states:
            st = ProjectState(project_id=project_id)
            for ev in self._store.events_for(project_id):
                apply_event(st, ev)
            self._states[project_id] = st
        return self._states[project_id]

    def view(self, project_id: str, role: str | None) -> dict:
        """按角色返回视图；权限不足的角色拿不到敏感字段。"""
        return project_view(self.state(project_id), role)

    # ---- 内部 ----

    def _emit(self, project_id: str, occurred_at: str, payload: dict, event_id: str | None = None) -> Event:
        version = self._store.current_version(project_id) + 1
        event = Event(
            event_id=event_id or f"{project_id}-V{version}",
            occurred_at=occurred_at,
            entity_id=project_id,
            version=version,
            payload=payload,
        )
        self._store.append(event)
        if project_id in self._states:
            apply_event(self._states[project_id], event)
        return event

    @staticmethod
    def _adopted(state: ProjectState, milestone_id: str) -> list:
        return [
            e for e in state.evidences.values()
            if e.milestone_id == milestone_id and e.status == "已采纳"
        ]

    @classmethod
    def _missing_evidence(cls, state: ProjectState, milestone) -> list:
        adopted_kinds = {e.kind for e in cls._adopted(state, milestone.milestone_id)}
        return [k for k in milestone.required_evidence if k not in adopted_kinds]

    @staticmethod
    def _committed(state: ProjectState, milestone_id: str) -> int:
        return sum(
            d.amount for d in state.disbursements.values()
            if d.milestone_id == milestone_id and d.status in ("已申请", "审批中", "可执行", "已执行")
        )

    def _require_active(self, state: ProjectState) -> None:
        if state.status != "在研":
            raise StateError(f"项目状态为「{state.status}」，该操作仅在研期间可用")

    # ---- 登记与承诺 ----

    def register_project(self, project_id, *, occurred_at, goals, budget_subjects, route, event_id=None):
        if self._store.current_version(project_id) > 0:
            raise StateError(f"项目 {project_id} 已登记")
        return self._emit(project_id, occurred_at, {
            "type": "项目登记",
            "goals": list(goals),
            "budget_subjects": dict(budget_subjects),
            "route": route,
        }, event_id)

    def negotiate_commitment(self, project_id, *, occurred_at, milestones, event_id=None):
        """把研究目标与预算科目协商为阶段承诺；承诺总额不得超出科目总额。"""
        state = self.state(project_id)
        if state.status != "已登记":
            raise StateError("只有已登记项目可以协商阶段承诺")
        ids = [m["milestone_id"] for m in milestones]
        if len(ids) != len(set(ids)):
            raise DomainError("里程碑标识重复")
        total = sum(m["budget"] for m in milestones)
        cap = sum(state.budget_subjects.values())
        if total > cap:
            raise DomainError(f"承诺总额 {total} 超出预算科目总额 {cap}")
        return self._emit(project_id, occurred_at, {"type": "阶段承诺", "milestones": list(milestones)}, event_id)

    # ---- 证据 ----

    def submit_evidence(self, project_id, *, occurred_at, evidence_id, milestone_id, kind, summary, event_id=None):
        state = self.state(project_id)
        self._require_active(state)
        if milestone_id not in state.milestones:
            raise DomainError(f"未知里程碑 {milestone_id}")
        if evidence_id in state.evidences:
            raise DomainError(f"证据 {evidence_id} 已存在")
        return self._emit(project_id, occurred_at, {
            "type": "证据提交", "evidence_id": evidence_id,
            "milestone_id": milestone_id, "kind": kind, "summary": summary,
        }, event_id)

    def adopt_evidence(self, project_id, *, occurred_at, evidence_id, reviewer, event_id=None):
        state = self.state(project_id)
        self._require_active(state)
        ev = state.evidences.get(evidence_id)
        if ev is None:
            raise DomainError(f"未知证据 {evidence_id}")
        if ev.status != "已提交":
            raise StateError(f"证据 {evidence_id} 状态为「{ev.status}」，不能采纳")
        return self._emit(project_id, occurred_at, {
            "type": "证据采纳", "evidence_id": evidence_id, "reviewer": reviewer,
        }, event_id)

    def correct_evidence(self, project_id, *, occurred_at, evidence_id, new_summary, reason, event_id=None):
        """更正证据：追加新版本，原始版本保留在 history 中，不得覆盖。"""
        state = self.state(project_id)
        ev = state.evidences.get(evidence_id)
        if ev is None:
            raise DomainError(f"未知证据 {evidence_id}")
        return self._emit(project_id, occurred_at, {
            "type": "证据更正", "evidence_id": evidence_id,
            "new_summary": new_summary, "reason": reason,
        }, event_id)

    # ---- 失败、评审、知识产权 ----

    def record_failure(self, project_id, *, occurred_at, failure_id, milestone_id, experiment, lesson, event_id=None):
        state = self.state(project_id)
        self._require_active(state)
        if milestone_id not in state.milestones:
            raise DomainError(f"未知里程碑 {milestone_id}")
        return self._emit(project_id, occurred_at, {
            "type": "试验失败", "failure_id": failure_id, "milestone_id": milestone_id,
            "experiment": experiment, "lesson": lesson,
        }, event_id)

    def record_external_review(self, project_id, *, occurred_at, review_id, expert, conclusion, event_id=None):
        self._require_active(self.state(project_id))
        return self._emit(project_id, occurred_at, {
            "type": "外部评审", "review_id": review_id, "expert": expert, "conclusion": conclusion,
        }, event_id)

    def record_ip(self, project_id, *, occurred_at, ip_id, owner, share, scope, event_id=None):
        self._require_active(self.state(project_id))
        return self._emit(project_id, occurred_at, {
            "type": "知识产权登记", "ip_id": ip_id, "owner": owner, "share": share, "scope": scope,
        }, event_id)

    # ---- 路线变更与预算重估 ----

    def change_route(self, project_id, *, occurred_at, new_route, failure_id, reestimates, review_id=None, event_id=None):
        """切换技术路线：必须基于已记录的失败；未拨付的里程碑同步重估预算。"""
        state = self.state(project_id)
        self._require_active(state)
        if failure_id not in state.failures:
            raise DomainError("路线变更必须关联一条已记录的试验失败")
        for mid in reestimates:
            m = state.milestones.get(mid)
            if m is None:
                raise DomainError(f"未知里程碑 {mid}")
            if any(d.milestone_id == mid and d.status == "已执行" for d in state.disbursements.values()):
                raise DomainError(f"里程碑 {mid} 已有拨付，不得重估")
        self._emit(project_id, occurred_at, {
            "type": "路线变更", "from_route": state.route, "to_route": new_route,
            "failure_id": failure_id, "review_id": review_id,
        }, event_id)
        for mid, new_budget in reestimates.items():
            self._emit(project_id, occurred_at, {
                "type": "承诺重估", "milestone_id": mid,
                "new_budget": new_budget, "reason": f"路线变更至「{new_route}」后重估",
            })

    # ---- 拨款（双重闸门） ----

    def request_disbursement(self, project_id, *, occurred_at, disbursement_id, milestone_id, amount, requester, event_id=None):
        state = self.state(project_id)
        self._require_active(state)
        m = state.milestones.get(milestone_id)
        if m is None:
            raise DomainError(f"未知里程碑 {milestone_id}")
        missing = self._missing_evidence(state, m)
        if missing:
            raise EvidenceIncomplete(f"里程碑 {milestone_id} 缺少已采纳证据: {missing}")
        remaining = m.budget - self._committed(state, milestone_id)
        if not 0 < amount <= remaining:
            raise DomainError(f"申请金额 {amount} 超出里程碑剩余额度 {remaining}")
        return self._emit(project_id, occurred_at, {
            "type": "拨款申请", "disbursement_id": disbursement_id,
            "milestone_id": milestone_id, "amount": amount, "requester": requester,
        }, event_id)

    def approve_disbursement(self, project_id, *, occurred_at, disbursement_id, role, actor, event_id=None):
        state = self.state(project_id)
        self._require_active(state)
        d = state.disbursements.get(disbursement_id)
        if d is None:
            raise DomainError(f"未知拨款单 {disbursement_id}")
        if d.status not in ("已申请", "审批中"):
            raise StateError(f"拨款单状态为「{d.status}」，不能审批")
        if role not in APPROVAL_ROLES:
            raise PermissionDenied(f"角色「{role}」无拨款审批权，须为 {list(APPROVAL_ROLES)}")
        if actor == d.requester:
            raise PermissionDenied("职责分离：申请人不得审批自己的拨款")
        if any(a["actor"] == actor for a in d.approvals):
            raise PermissionDenied("职责分离：同一人员不得兼任技术与财务两道审批")
        if any(a["role"] == role for a in d.approvals):
            raise DomainError(f"角色「{role}」已审批过该拨款单")
        return self._emit(project_id, occurred_at, {
            "type": "拨款批准", "disbursement_id": disbursement_id, "role": role, "actor": actor,
        }, event_id)

    def execute_disbursement(self, project_id, *, occurred_at, disbursement_id, event_id=None):
        """执行拨付：快照当时批准的证据（标识+版本），资金与证据一一对应。"""
        state = self.state(project_id)
        self._require_active(state)
        d = state.disbursements.get(disbursement_id)
        if d is None:
            raise DomainError(f"未知拨款单 {disbursement_id}")
        if d.status != "可执行":
            raise StateError(f"拨款单状态为「{d.status}」，须先完成技术与财务双审批")
        m = state.milestones[d.milestone_id]
        missing = self._missing_evidence(state, m)
        if missing:
            raise EvidenceIncomplete(f"执行时里程碑 {m.milestone_id} 证据不再完整: {missing}")
        refs = [
            {"evidence_id": e.evidence_id, "kind": e.kind, "version": e.version}
            for e in sorted(self._adopted(state, m.milestone_id), key=lambda e: e.evidence_id)
        ]
        return self._emit(project_id, occurred_at, {
            "type": "拨款执行", "disbursement_id": disbursement_id,
            "amount": d.amount, "evidence_refs": refs,
        }, event_id)

    def confirm_milestone(self, project_id, *, occurred_at, milestone_id, event_id=None):
        state = self.state(project_id)
        self._require_active(state)
        m = state.milestones.get(milestone_id)
        if m is None:
            raise DomainError(f"未知里程碑 {milestone_id}")
        missing = self._missing_evidence(state, m)
        if missing:
            raise EvidenceIncomplete(f"里程碑 {milestone_id} 缺少已采纳证据: {missing}")
        return self._emit(project_id, occurred_at, {"type": "里程碑达成", "milestone_id": milestone_id}, event_id)

    # ---- 暂停与处置义务 ----

    def suspend_project(self, project_id, *, occurred_at, reason, event_id=None):
        """暂停项目：保留设备处置与未用资金两项义务，未履行前不得验收。"""
        state = self.state(project_id)
        self._require_active(state)
        self._emit(project_id, occurred_at, {"type": "项目暂停", "reason": reason}, event_id)
        self._emit(project_id, occurred_at, {
            "type": "处置义务", "obligation_id": f"{project_id}-OB-设备",
            "kind": "设备处置", "detail": "盘点在研设备并妥善处置，暂停期间不得挪用",
        })
        unused = sum(m.budget for m in state.milestones.values()) - sum(
            d.amount for d in state.disbursements.values() if d.status == "已执行"
        )
        self._emit(project_id, occurred_at, {
            "type": "处置义务", "obligation_id": f"{project_id}-OB-资金",
            "kind": "未用资金", "amount": unused,
            "detail": "未使用资金按资助协议退回或冻结",
        })

    def settle_obligation(self, project_id, *, occurred_at, obligation_id, note, event_id=None):
        state = self.state(project_id)
        o = state.obligations.get(obligation_id)
        if o is None:
            raise DomainError(f"未知义务 {obligation_id}")
        if o.status != "待履行":
            raise StateError(f"义务 {obligation_id} 状态为「{o.status}」")
        return self._emit(project_id, occurred_at, {
            "type": "义务履行", "obligation_id": obligation_id, "note": note,
        }, event_id)

    # ---- 验收 ----

    def accept_project(self, project_id, *, occurred_at, summary, event_id=None):
        """验收：全部里程碑达成、处置义务履行完毕；失败记录随验收归档保留。"""
        state = self.state(project_id)
        self._require_active(state)
        not_done = [mid for mid, m in state.milestones.items() if m.status != "已达成"]
        if not_done:
            raise StateError(f"存在未达成里程碑: {not_done}")
        pending = [oid for oid, o in state.obligations.items() if o.status != "已履行"]
        if pending:
            raise StateError(f"存在未履行的处置义务: {pending}")
        total = sum(d.amount for d in state.disbursements.values() if d.status == "已执行")
        preserved = [
            {"failure_id": f.failure_id, "experiment": f.experiment, "lesson": f.lesson}
            for f in state.failures.values()
        ]
        return self._emit(project_id, occurred_at, {
            "type": "验收通过", "summary": summary,
            "preserved_failures": preserved, "total_disbursed": total,
        }, event_id)
