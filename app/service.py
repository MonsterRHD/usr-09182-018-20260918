"""领域服务：把研究目标、技术证据与预算科目转换为可协商的阶段承诺。

所有写操作都以追加事件完成；任何校验失败都抛 DomainError，状态不被修改。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

from .errors import BusinessRuleError, PermissionDeniedError
from .event_store import EventStore, event_fingerprint
from .state import ReplayState

# 角色常量
APPLICANT = "applicant"
TECH_REVIEWER = "technical_reviewer"
EXTERNAL_EXPERT = "external_expert"
APPROVER = "approver"
FINANCE = "finance"
FUNDER = "funder"

ACTOR_ROLES = {APPLICANT, TECH_REVIEWER, EXTERNAL_EXPERT, APPROVER, FINANCE, FUNDER}


class Actor:
    def __init__(self, actor_id: str, role: str, org: str | None = None) -> None:
        if role not in ACTOR_ROLES:
            raise PermissionDeniedError(f"未知角色: {role}")
        self.id = actor_id
        self.role = role
        self.org = org

    def require_role(self, *roles: str) -> None:
        if self.role not in roles:
            raise PermissionDeniedError(
                f"角色 {self.role} 无权执行此操作（需要 {'/'.join(roles)}）"
            )


class ProjectService:
    def __init__(self, store: EventStore) -> None:
        self.store = store

    # -- 内部工具 -------------------------------------------------------------

    @property
    def state(self) -> ReplayState:
        return ReplayState.replay(self.store.all_events())

    def _emit(
        self,
        entity_id: str,
        payload: dict,
        actor: Actor,
        occurred_at: datetime,
        event_id: str | None = None,
    ) -> dict:
        if occurred_at.tzinfo is None:
            raise BusinessRuleError("occurred_at 必须带时区")
        version = self.store.current_version(entity_id) + 1
        event = {
            "event_id": event_id or f"{entity_id}-E{version}",
            "occurred_at": occurred_at.isoformat(timespec="seconds"),
            "entity_id": entity_id,
            "version": version,
            "actor_id": actor.id,
            "payload": payload,
        }
        return self.store.append(event)

    # -- 项目登记与预算 --------------------------------------------------------

    def register_project(
        self,
        actor: Actor,
        *,
        project_code: str,
        title: str,
        research_goal: str,
        applicant_name: str,
        total_budget: float,
        occurred_at: datetime,
        currency: str = "CNY",
        ip_ownership: str = "承担方所有，资助方享有实施许可",
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(APPLICANT)
        if project_code in self.state.projects:
            raise BusinessRuleError(f"项目 {project_code} 已登记")
        payload = {
            "type": "ProjectRegistered",
            "project_code": project_code,
            "title": title,
            "research_goal": research_goal,
            "applicant_id": actor.id,
            "applicant_name": applicant_name,
            "total_budget": total_budget,
            "currency": currency,
            "ip_ownership": ip_ownership,
        }
        return self._emit(project_code, payload, actor, occurred_at, event_id)

    def establish_budget(
        self,
        actor: Actor,
        *,
        project_code: str,
        items: list[dict],
        occurred_at: datetime,
        basis: str = "",
        budget_version: int | None = None,
        supersedes_version: int | None = None,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(APPLICANT, FUNDER)
        state = self.state
        if project_code not in state.projects:
            raise BusinessRuleError("项目不存在")
        total = sum(float(i["amount"]) for i in items)
        project_total = float(state.projects[project_code]["total_budget"])
        if total > project_total + 1e-9 and budget_version is None:
            raise BusinessRuleError(
                f"预算合计 {total} 超过项目总额 {project_total}（调增需走重估版本）"
            )
        version = budget_version or (state.projects[project_code]["current_budget_version"] + 1)
        payload = {
            "type": "BudgetEstablished",
            "budget_version": version,
            "items": items,
            "basis": basis,
            "supersedes_version": supersedes_version,
        }
        return self._emit(project_code, payload, actor, occurred_at, event_id)

    # -- 阶段承诺：提出 / 协商 / 生效 ------------------------------------------

    def propose_milestone(
        self,
        actor: Actor,
        *,
        milestone_id: str,
        project_code: str,
        stage_index: int,
        name: str,
        objective: str,
        acceptance_metrics: list[dict],
        evidence_requirements: list[dict],
        linked_budget_items: list[str],
        amount: float,
        due_date: str | None,
        occurred_at: datetime,
        route: str = "初始路线",
        ip_ownership: str | None = None,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(APPLICANT)
        state = self.state
        if project_code not in state.projects:
            raise BusinessRuleError("项目不存在")
        if milestone_id in state.milestones:
            raise BusinessRuleError("里程碑已存在")
        self._check_budget_items(state, project_code, linked_budget_items, amount)
        payload = {
            "type": "MilestoneProposed",
            "milestone_id": milestone_id,
            "project_code": project_code,
            "stage_index": stage_index,
            "name": name,
            "objective": objective,
            "acceptance_metrics": acceptance_metrics,
            "evidence_requirements": evidence_requirements,
            "linked_budget_items": linked_budget_items,
            "amount": amount,
            "due_date": due_date,
            "route": route,
            "ip_ownership": ip_ownership
            or state.projects[project_code]["ip_ownership"],
        }
        return self._emit(milestone_id, payload, actor, occurred_at, event_id)

    def negotiate_milestone(
        self,
        actor: Actor,
        *,
        milestone_id: str,
        changes: dict,
        negotiated_by: list[dict],
        occurred_at: datetime,
        revision: int | None = None,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(APPLICANT, FUNDER)
        state = self.state
        m = self._get_milestone(state, milestone_id)
        rev = revision or (len(m["revisions"]) + 1)
        if "amount" in changes:
            self._check_budget_items(
                state,
                m["project_code"],
                changes.get("linked_budget_items", m["revisions"][-1]["linked_budget_items"]),
                float(changes["amount"]),
            )
        payload = {
            "type": "MilestoneNegotiated",
            "milestone_id": milestone_id,
            "revision": rev,
            "changes": changes,
            "negotiated_by": negotiated_by,
        }
        return self._emit(milestone_id, payload, actor, occurred_at, event_id)

    def commit_milestone(
        self,
        actor: Actor,
        *,
        milestone_id: str,
        occurred_at: datetime,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(APPLICANT, FUNDER)
        state = self.state
        m = self._get_milestone(state, milestone_id)
        revision = len(m["revisions"])
        payload = {
            "type": "MilestoneCommitted",
            "milestone_id": milestone_id,
            "revision": revision,
            "committed_by": [actor.id, actor.role],
        }
        return self._emit(milestone_id, payload, actor, occurred_at, event_id)

    # -- 证据：提交 / 失败留痕 / 更正 / 核验 -----------------------------------

    def submit_evidence(
        self,
        actor: Actor,
        *,
        evidence_id: str,
        milestone_id: str,
        requirement_code: str,
        kind: str,
        title: str,
        result: str,
        occurred_at: datetime,
        data_ref: str | None = None,
        ip_ownership: str | None = None,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(APPLICANT)
        state = self.state
        m = self._get_milestone(state, milestone_id)
        self._require_active_milestone(m)
        if evidence_id in state.evidence:
            raise BusinessRuleError("证据已存在")
        payload = {
            "type": "EvidenceSubmitted",
            "evidence_id": evidence_id,
            "milestone_id": milestone_id,
            "requirement_code": requirement_code,
            "kind": kind,
            "title": title,
            "result": result,
            "data_ref": data_ref,
            "ip_ownership": ip_ownership or m["revisions"][-1].get("ip_ownership"),
            "submitter_id": actor.id,
            "submitted_for_revision": len(m["revisions"]),
        }
        return self._emit(evidence_id, payload, actor, occurred_at, event_id)

    def record_trial_failure(
        self,
        actor: Actor,
        *,
        evidence_id: str,
        milestone_id: str,
        trial_name: str,
        hypothesis: str,
        observed: str,
        learned_value: str,
        occurred_at: datetime,
        data_ref: str | None = None,
        root_cause: str | None = None,
        event_id: str | None = None,
    ) -> dict:
        """失败即证据：必须结构化留痕，价值随项目永久保留。"""
        actor.require_role(APPLICANT)
        state = self.state
        m = self._get_milestone(state, milestone_id)
        self._require_active_milestone(m)
        if not learned_value.strip():
            raise BusinessRuleError("失败记录必须写明 learned_value（保留失败的价值）")
        payload = {
            "type": "TrialFailed",
            "evidence_id": evidence_id,
            "milestone_id": milestone_id,
            "trial_name": trial_name,
            "hypothesis": hypothesis,
            "observed": observed,
            "data_ref": data_ref,
            "root_cause": root_cause,
            "learned_value": learned_value,
            "submitter_id": actor.id,
        }
        return self._emit(evidence_id, payload, actor, occurred_at, event_id)

    def correct_evidence(
        self,
        actor: Actor,
        *,
        evidence_id: str,
        corrects_evidence_id: str,
        reason: str,
        new_data_ref: str,
        occurred_at: datetime,
        result: str | None = None,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(APPLICANT)
        state = self.state
        old = state.evidence.get(corrects_evidence_id)
        if old is None:
            raise BusinessRuleError("被更正证据不存在")
        if old["submitter_id"] != actor.id:
            # 更正不得由他人代笔；原始事实也不允许覆盖
            raise PermissionDeniedError("仅证据原提交人可发起更正")
        if old["status"] == "superseded":
            raise BusinessRuleError("证据已被更正，应基于最新版本再次更正")
        payload = {
            "type": "EvidenceCorrected",
            "evidence_id": evidence_id,
            "corrects_evidence_id": corrects_evidence_id,
            "reason": reason,
            "new_data_ref": new_data_ref,
            "result": result,
            "submitter_id": actor.id,
        }
        return self._emit(evidence_id, payload, actor, occurred_at, event_id)

    def verify_evidence(
        self,
        actor: Actor,
        *,
        evidence_id: str,
        accepted: bool,
        occurred_at: datetime,
        review_note: str = "",
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(TECH_REVIEWER)
        state = self.state
        ev = state.evidence.get(evidence_id)
        if ev is None:
            raise BusinessRuleError("证据不存在")
        if ev["submitter_id"] == actor.id:
            raise PermissionDeniedError("核验人不得核验自己提交的证据（职责分离）")
        if ev["status"] == "superseded":
            raise BusinessRuleError("证据已被更正，应核验最新版本")
        payload = {
            "type": "EvidenceVerified",
            "evidence_id": evidence_id,
            "milestone_id": ev["milestone_id"],
            "accepted": accepted,
            "review_note": review_note,
            "reviewer_id": actor.id,
        }
        return self._emit(evidence_id, payload, actor, occurred_at, event_id)

    # -- 外部评审与知识产权 ----------------------------------------------------

    def record_external_review(
        self,
        actor: Actor,
        *,
        review_id: str,
        milestone_id: str,
        scope: str,
        verdict: str,
        occurred_at: datetime,
        recommendation: str = "",
        expert_org: str | None = None,
        data_ref: str | None = None,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(EXTERNAL_EXPERT, FUNDER, APPLICANT)
        state = self.state
        self._get_milestone(state, milestone_id)
        if review_id in state.reviews:
            raise BusinessRuleError("评审已登记")
        # 外部专家不得来自承担方
        project = state.projects[state.milestones[milestone_id]["project_code"]]
        if actor.role == EXTERNAL_EXPERT and actor.org == project["applicant_name"]:
            raise PermissionDeniedError("外部评审专家不得来自承担方")
        payload = {
            "type": "ExternalReviewRecorded",
            "review_id": review_id,
            "milestone_id": milestone_id,
            "scope": scope,
            "verdict": verdict,
            "recommendation": recommendation,
            "expert_id": actor.id,
            "expert_org": expert_org or actor.org,
            "data_ref": data_ref,
        }
        return self._emit(review_id, payload, actor, occurred_at, event_id)

    # -- 技术路线切换（保留失败价值 + 预算重估）---------------------------------

    def request_route_switch(
        self,
        actor: Actor,
        *,
        change_id: str,
        milestone_id: str,
        from_route: str,
        to_route: str,
        reason: str,
        failure_evidence_ids: list[str],
        occurred_at: datetime,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(APPLICANT)
        state = self.state
        m = self._get_milestone(state, milestone_id)
        if change_id in state.changes:
            raise BusinessRuleError("变更单已存在")
        if not failure_evidence_ids:
            raise BusinessRuleError("切换路线必须引用失败证据（失败的价值是决策依据）")
        for fid in failure_evidence_ids:
            ev = state.evidence.get(fid)
            if ev is None or ev["milestone_id"] != milestone_id:
                raise BusinessRuleError(f"失败证据 {fid} 不属于该里程碑")
            if ev["kind"] != "failure_log":
                raise BusinessRuleError(f"证据 {fid} 不是失败记录")
        payload = {
            "type": "RouteSwitchRequested",
            "change_id": change_id,
            "project_code": m["project_code"],
            "milestone_id": milestone_id,
            "from_route": from_route,
            "to_route": to_route,
            "reason": reason,
            "failure_evidence_ids": failure_evidence_ids,
            "requested_by": actor.id,
        }
        return self._emit(change_id, payload, actor, occurred_at, event_id)

    def approve_route_switch(
        self,
        actor: Actor,
        *,
        change_id: str,
        review_id: str,
        occurred_at: datetime,
        new_requirements: list[dict],
        budget_reestimate: list[dict],
        new_amount: float,
        new_linked_budget_items: list[str],
        conditions: list[str] | None = None,
        new_budget_items: list[dict] | None = None,
        event_id: str | None = None,
    ) -> list[dict]:
        """批准切换：外部评审通过 + 资助机构批准；产出新承诺版本并重估预算。"""
        actor.require_role(FUNDER)
        state = self.state
        ch = state.changes.get(change_id)
        if ch is None or ch["status"] != "requested":
            raise BusinessRuleError("变更单不存在或非待批状态")
        review = state.reviews.get(review_id)
        if review is None or review["milestone_id"] != ch["milestone_id"]:
            raise BusinessRuleError("缺少对应的外部评审")
        if review["scope"] != "route_switch" or not self._verdict_passed(review["verdict"]):
            raise BusinessRuleError("外部评审未同意路线切换")
        if review["expert_id"] == actor.id:
            raise PermissionDeniedError("评审专家不得兼任批准人")
        m = state.milestones[ch["milestone_id"]]
        new_revision = len(m["revisions"]) + 1
        conditions = list(conditions or [])
        # 切换前形成的知识产权归属不因路线变更而改变
        prior_ip = m["revisions"][-1].get("ip_ownership")
        conditions.append(f"切换前知识产权归属不变：{prior_ip}")

        approve_payload = {
            "type": "RouteSwitchApproved",
            "change_id": change_id,
            "review_id": review_id,
            "approved_by": actor.id,
            "new_milestone_revision": new_revision,
            "new_requirements": new_requirements,
            "new_amount": new_amount,
            "new_linked_budget_items": new_linked_budget_items,
            "budget_reestimate": budget_reestimate,
            "conditions": conditions,
        }
        events = [self._emit(change_id, approve_payload, actor, occurred_at, event_id)]

        # 重估预算形成新版本（旧版本保留，付款可回溯到各自批准时点的版本）
        if new_budget_items is not None:
            project_code = ch["project_code"]
            fresh = self.state
            bv = fresh.projects[project_code]["current_budget_version"] + 1
            budget_payload = {
                "type": "BudgetEstablished",
                "budget_version": bv,
                "items": new_budget_items,
                "basis": f"路线切换 {change_id} 后的预算重估",
                "supersedes_version": bv - 1,
            }
            events.append(
                self._emit(
                    project_code,
                    budget_payload,
                    actor,
                    occurred_at,
                    f"{change_id}-BUDGET",
                )
            )
        return events

    # -- 暂停 / 恢复 / 终止与处置义务 ------------------------------------------

    def suspend(
        self,
        actor: Actor,
        *,
        scope: str,
        reason: str,
        disposal_obligations: list[dict],
        occurred_at: datetime,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(FUNDER)
        state = self.state
        if scope not in state.projects and scope not in state.milestones:
            raise BusinessRuleError("暂停范围不存在")
        if not disposal_obligations:
            raise BusinessRuleError("暂停必须同步登记设备与未用资金的处置义务")
        for ob in disposal_obligations:
            if ob["type"] not in ("equipment", "unused_funds"):
                raise BusinessRuleError("处置义务类型须为 equipment/unused_funds")
            if not ob.get("responsible_id"):
                raise BusinessRuleError("每项处置义务须指定责任人")
        payload = {
            "type": "MilestoneSuspended",
            "scope": scope,
            "reason": reason,
            "suspended_by": actor.id,
            "disposal_obligations": disposal_obligations,
        }
        return self._emit(scope, payload, actor, occurred_at, event_id)

    def resume(
        self,
        actor: Actor,
        *,
        scope: str,
        reason: str,
        occurred_at: datetime,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(FUNDER)
        payload = {
            "type": "MilestoneResumed",
            "scope": scope,
            "reason": reason,
            "resumed_by": actor.id,
        }
        return self._emit(scope, payload, actor, occurred_at, event_id)

    def terminate(
        self,
        actor: Actor,
        *,
        project_code: str,
        reason: str,
        occurred_at: datetime,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(FUNDER)
        state = self.state
        if project_code not in state.projects:
            raise BusinessRuleError("项目不存在")
        payload = {"type": "ProjectTerminated", "reason": reason, "terminated_by": actor.id}
        return self._emit(project_code, payload, actor, occurred_at, event_id)

    def fulfill_obligation(
        self,
        actor: Actor,
        *,
        obligation_ref: str,
        action_taken: str,
        occurred_at: datetime,
        proof_ref: str | None = None,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(APPLICANT, FUNDER)
        state = self.state
        ob = state.obligations.get(obligation_ref)
        if ob is None:
            raise BusinessRuleError("处置义务不存在")
        if ob["status"] == "fulfilled":
            raise BusinessRuleError("处置义务已履行")
        if actor.role == APPLICANT and actor.id != ob["responsible_id"]:
            raise PermissionDeniedError("仅义务责任人可申报履行")
        payload = {
            "type": "DisposalObligationFulfilled",
            "obligation_ref": obligation_ref,
            "asset_or_fund": ob["asset_or_fund"],
            "action_taken": action_taken,
            "proof_ref": proof_ref,
            "fulfilled_by": actor.id,
        }
        return self._emit(ob["scope"], payload, actor, occurred_at, event_id)

    # -- 付款：证据完整 + 职责分离双闸门 ---------------------------------------

    def evaluate_payment_gates(self, milestone_id: str, approver: Actor) -> dict:
        """返回四个闸门的判定明细；任一不过即不得批准。"""
        state = self.state
        m = self._get_milestone(state, milestone_id)
        rev = m["revisions"][-1]
        effective = state.effective_evidence(milestone_id)

        # 闸门 1：证据完整（按当前生效承诺版本）
        missing: list[str] = []
        unverified: list[str] = []
        rejected: list[str] = []
        matched: list[dict] = []
        for req in rev["evidence_requirements"]:
            if not req.get("required", True):
                continue
            candidates = [
                e
                for e in effective
                if self._evidence_satisfies(e, req, rev["revision"])
            ]
            if not candidates:
                missing.append(f"{req['code']}:{req['name']}")
                continue
            pick = candidates[-1]
            if pick["accepted"] is None:
                unverified.append(pick["evidence_id"])
            elif pick["accepted"] is False:
                rejected.append(pick["evidence_id"])
            else:
                matched.append(pick)
        evidence_ok = not (missing or unverified or rejected)

        # 闸门 2：职责分离
        submitters = {e["submitter_id"] for e in effective}
        reviewers = {e["reviewer_id"] for e in effective if e["reviewer_id"]}
        sep_problems: list[str] = []
        if approver.id in submitters:
            sep_problems.append("批准人提交过该里程碑证据")
        if approver.id in reviewers:
            sep_problems.append("批准人核验过该里程碑证据")
        separation_ok = not sep_problems

        # 闸门 3：状态
        state_problems: list[str] = []
        if m["status"] not in ("committed", "in_progress"):
            state_problems.append(f"里程碑状态为 {m['status']}，不可拨款")
        state_ok = not state_problems

        # 闸门 4：预算
        amount = float(rev["amount"])
        paid = state.paid_amount(milestone_id)
        budget_problems: list[str] = []
        if paid + 1e-9 >= amount:
            budget_problems.append(f"该里程碑承诺金额 {amount} 已付 {paid}，无剩余额度")
        budget_ok = not budget_problems

        return {
            "passed": evidence_ok and separation_ok and state_ok and budget_ok,
            "gates": [
                {
                    "code": "gate_evidence_complete",
                    "passed": evidence_ok,
                    "detail": {
                        "missing": missing,
                        "unverified": unverified,
                        "rejected": rejected,
                        "matched_evidence_ids": [e["evidence_id"] for e in matched],
                    },
                },
                {
                    "code": "gate_separation_of_duties",
                    "passed": separation_ok,
                    "detail": {"problems": sep_problems},
                },
                {
                    "code": "gate_state",
                    "passed": state_ok,
                    "detail": {"problems": state_problems, "status": m["status"]},
                },
                {
                    "code": "gate_budget",
                    "passed": budget_ok,
                    "detail": {
                        "problems": budget_problems,
                        "committed_amount": amount,
                        "already_paid": paid,
                        "remaining": max(0.0, amount - paid),
                    },
                },
            ],
            "_matched": matched,
        }

    def approve_payment(
        self,
        actor: Actor,
        *,
        payment_id: str,
        milestone_id: str,
        amount: float,
        occurred_at: datetime,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(APPROVER)
        state = self.state
        if payment_id in state.payments:
            raise BusinessRuleError("付款单已存在，不得重复拨款")
        m = self._get_milestone(state, milestone_id)
        rev = m["revisions"][-1]

        gate = self.evaluate_payment_gates(milestone_id, actor)
        if not gate["passed"]:
            failed = [g["code"] for g in gate["gates"] if not g["passed"]]
            raise BusinessRuleError(f"拨款闸门未通过: {failed}; 明细: {gate['gates']}")

        remaining = float(rev["amount"]) - state.paid_amount(milestone_id)
        if amount - remaining > 1e-9:
            raise BusinessRuleError(
                f"本次申请 {amount} 超过剩余承诺额度 {remaining:.2f}"
            )

        # 冻结批准时点的证据快照——日后证据更正不影响本笔付款的追溯
        snapshot_entries = []
        for e in gate["_matched"]:
            snapshot_entries.append(
                {
                    "requirement_code": self._requirement_code_for(e, rev),
                    "evidence_id": e["evidence_id"],
                    "version": e["version"],
                    "sha256": event_fingerprint(e["raw_event"]),
                }
            )
        snapshot_bundle = {
            "milestone_id": milestone_id,
            "commit_revision": rev["revision"],
            "route": rev["route"],
            "requirements": rev["evidence_requirements"],
            "entries": snapshot_entries,
        }
        snapshot_id = hashlib.sha256(
            json.dumps(snapshot_bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

        payload = {
            "type": "PaymentApproved",
            "payment_id": payment_id,
            "milestone_id": milestone_id,
            "amount": amount,
            "currency": "CNY",
            "linked_budget_items": rev["linked_budget_items"],
            "approved_by": actor.id,
            "evidence_snapshot": {
                "snapshot_id": snapshot_id,
                "commit_revision": rev["revision"],
                "route": rev["route"],
                "entries": snapshot_entries,
            },
            "gate_result": {
                "passed": True,
                "gates": [{k: v for k, v in g.items() if k != "_matched"} for g in gate["gates"]],
            },
        }
        return self._emit(payment_id, payload, actor, occurred_at, event_id)

    def execute_payment(
        self,
        actor: Actor,
        *,
        payment_id: str,
        payee: str,
        voucher_ref: str,
        occurred_at: datetime,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(FINANCE)
        state = self.state
        pay = state.payments.get(payment_id)
        if pay is None:
            raise BusinessRuleError("付款单不存在")
        if pay["status"] == "executed":
            raise BusinessRuleError("付款已执行，不得重复付款")
        if pay["status"] != "approved":
            raise BusinessRuleError("付款单未经批准，不得执行")
        if actor.id == pay["approved_by"]:
            raise PermissionDeniedError("付款执行人不得是批准人（职责分离）")
        m = state.milestones[pay["milestone_id"]]
        submitters = {e["submitter_id"] for e in state.effective_evidence(m["milestone_id"])}
        reviewers = {
            e["reviewer_id"] for e in state.effective_evidence(m["milestone_id"]) if e["reviewer_id"]
        }
        if actor.id in submitters or actor.id in reviewers:
            raise PermissionDeniedError("付款执行人不得同时是证据提交人或核验人")
        payload = {
            "type": "PaymentExecuted",
            "payment_id": payment_id,
            "amount": pay["amount"],
            "currency": pay["currency"],
            "executed_by": actor.id,
            "payee": payee,
            "voucher_ref": voucher_ref,
            "executed_at": occurred_at.isoformat(timespec="seconds"),
        }
        return self._emit(payment_id, payload, actor, occurred_at, event_id)

    # -- 验收 -----------------------------------------------------------------

    def request_acceptance(
        self,
        actor: Actor,
        *,
        milestone_id: str,
        claimed_metrics: dict,
        occurred_at: datetime,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(APPLICANT)
        state = self.state
        m = self._get_milestone(state, milestone_id)
        if m["status"] not in ("committed", "in_progress"):
            raise BusinessRuleError(f"状态 {m['status']} 不可申请验收")
        payload = {
            "type": "AcceptanceRequested",
            "milestone_id": milestone_id,
            "claimed_metrics": claimed_metrics,
            "requested_by": actor.id,
        }
        return self._emit(milestone_id, payload, actor, occurred_at, event_id)

    def approve_acceptance(
        self,
        actor: Actor,
        *,
        milestone_id: str,
        final_metrics: dict,
        review_id: str,
        occurred_at: datetime,
        event_id: str | None = None,
    ) -> dict:
        actor.require_role(FUNDER)
        state = self.state
        m = self._get_milestone(state, milestone_id)
        rev = m["revisions"][-1]
        review = state.reviews.get(review_id)
        if review is None or review["milestone_id"] != milestone_id:
            raise BusinessRuleError("验收须有对应外部评审")
        if review["scope"] != "acceptance" or not self._verdict_passed(review["verdict"]):
            raise BusinessRuleError("外部评审未给出验收通过结论")
        failures = self._check_metrics(rev["acceptance_metrics"], final_metrics)
        if failures:
            raise BusinessRuleError(f"最终指标未达标: {failures}")
        payload = {
            "type": "AcceptanceApproved",
            "milestone_id": milestone_id,
            "final_metrics": final_metrics,
            "review_id": review_id,
            "approved_by": actor.id,
        }
        return self._emit(milestone_id, payload, actor, occurred_at, event_id)

    # -- 辅助 -----------------------------------------------------------------

    @staticmethod
    def _verdict_passed(verdict: str) -> bool:
        # “不通过”包含“通过”二字，必须先排除否决表述
        return "通过" in verdict and "不通过" not in verdict

    @staticmethod
    def _get_milestone(state: ReplayState, milestone_id: str) -> dict:
        m = state.milestones.get(milestone_id)
        if m is None:
            raise BusinessRuleError(f"里程碑 {milestone_id} 不存在")
        return m

    @staticmethod
    def _require_active_milestone(m: dict) -> None:
        if m["status"] in ("suspended", "terminated", "accepted"):
            raise BusinessRuleError(f"里程碑状态 {m['status']}，不可提交证据")

    @staticmethod
    def _check_budget_items(
        state: ReplayState, project_code: str, item_ids: list[str], amount: float
    ) -> None:
        project = state.projects[project_code]
        if not project["budget_versions"]:
            raise BusinessRuleError("项目尚未编制预算科目")
        current = project["budget_versions"][-1]["items"]
        known = {i["item_id"] for i in current}
        unknown = [i for i in item_ids if i not in known]
        if unknown:
            raise BusinessRuleError(f"预算科目不存在: {unknown}")
        tied_total = sum(
            float(i["amount"]) for i in current if i["item_id"] in item_ids
        )
        if amount - tied_total > 1e-9:
            raise BusinessRuleError(
                f"里程碑金额 {amount} 超过挂钩科目合计 {tied_total}"
            )

    @staticmethod
    def _evidence_satisfies(ev: dict, req: dict, current_revision: int) -> bool:
        if ev["kind"] == "failure_log" and req.get("kind") == "failure_log":
            return True  # 失败证据按类型满足失败留痕要求
        if ev.get("requirement_code") != req["code"]:
            return False
        # 路线切换后的承诺版本只认针对新版本提交的证据（旧路线证据保留作价值，不充数）
        return ev.get("submitted_for_revision") == current_revision

    @staticmethod
    def _requirement_code_for(ev: dict, rev: dict) -> str:
        if ev["kind"] == "failure_log":
            match = next(
                (r["code"] for r in rev["evidence_requirements"] if r.get("kind") == "failure_log"),
                "FAILURE_LOG",
            )
            return match
        return ev.get("requirement_code", "")

    @staticmethod
    def _check_metrics(specs: list[dict], actual: dict) -> list[str]:
        failures: list[str] = []
        for spec in specs:
            name = spec["name"]
            if name not in actual:
                failures.append(f"{name}: 缺测")
                continue
            value = float(actual[name])
            target = float(spec["target"])
            op = spec.get("operator", ">=")
            ok = {
                ">=": value >= target,
                "<=": value <= target,
                ">": value > target,
                "<": value < target,
                "==": abs(value - target) < 1e-9,
            }[op]
            if not ok:
                failures.append(f"{name}: 实测 {value} {op} 目标 {target} 不成立")
        return failures
