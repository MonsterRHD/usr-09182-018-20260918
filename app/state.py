"""事件流重放得到的状态。

状态本身不落盘、不可直接修改；每个事件有且仅有一个 apply 分支。
更正事件不覆盖原始事实：原件保留并标记 superseded。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional


class ReplayState:
    def __init__(self) -> None:
        self.projects: dict[str, dict] = {}
        self.milestones: dict[str, dict] = {}
        self.evidence: dict[str, dict] = {}
        self.reviews: dict[str, dict] = {}
        self.changes: dict[str, dict] = {}
        self.payments: dict[str, dict] = {}
        self.obligations: dict[str, dict] = {}
        self.timeline: list[dict] = []

    # -- 入口 ----------------------------------------------------------------

    def apply(self, event: dict) -> None:
        p = event["payload"]
        etype = p["type"]
        handler = getattr(self, f"_on_{etype}", None)
        if handler is None:
            raise ValueError(f"未知事件类型: {etype}")
        handler(event)
        self.timeline.append(event)

    @classmethod
    def replay(cls, events: list[dict]) -> "ReplayState":
        state = cls()
        for e in sorted(events, key=lambda x: (x["occurred_at"], x["event_id"])):
            state.apply(e)
        return state

    # -- 项目与预算 -----------------------------------------------------------

    def _on_ProjectRegistered(self, event: dict) -> None:
        p = deepcopy(event["payload"])
        p.pop("type")
        p["status"] = "active"
        p["budget_versions"] = []
        p["current_budget_version"] = 0
        self.projects[p["project_code"]] = p

    def _on_BudgetEstablished(self, event: dict) -> None:
        p = event["payload"]
        project = self.projects[event["entity_id"]]
        version = {
            "budget_version": p["budget_version"],
            "items": deepcopy(p["items"]),
            "basis": p.get("basis", ""),
            "supersedes_version": p.get("supersedes_version"),
            "occurred_at": event["occurred_at"],
        }
        project["budget_versions"].append(version)
        project["current_budget_version"] = p["budget_version"]

    # -- 里程碑（阶段承诺）----------------------------------------------------

    def _start_revision(self, m: dict, revision: int, event: dict) -> dict:
        base = m["revisions"][-1] if m["revisions"] else {}
        rev = deepcopy(base)
        rev.update({"revision": revision, "occurred_at": event["occurred_at"]})
        m["revisions"].append(rev)
        return rev

    def _on_MilestoneProposed(self, event: dict) -> None:
        p = event["payload"]
        m = {
            "milestone_id": p["milestone_id"],
            "project_code": p["project_code"],
            "stage_index": p["stage_index"],
            "status": "proposed",
            "committed_revision": 0,
            "revisions": [],
            "route_history": [],
            "change_ids": [],
            "evidence_ids": [],
        }
        rev = self._start_revision(m, 1, event)
        rev.update(
            name=p["name"],
            objective=p["objective"],
            acceptance_metrics=deepcopy(p["acceptance_metrics"]),
            evidence_requirements=deepcopy(p["evidence_requirements"]),
            linked_budget_items=list(p["linked_budget_items"]),
            amount=p["amount"],
            due_date=p.get("due_date"),
            route=p.get("route", "初始路线"),
            ip_ownership=p.get("ip_ownership", "按项目约定"),
        )
        m["route_history"].append({"revision": 1, "route": rev["route"]})
        self.milestones[p["milestone_id"]] = m

    def _on_MilestoneNegotiated(self, event: dict) -> None:
        p = event["payload"]
        m = self.milestones[p["milestone_id"]]
        if m["status"] not in ("proposed", "committed", "in_progress"):
            raise ValueError("当前状态不可协商")
        rev = self._start_revision(m, p["revision"], event)
        changes = p.get("changes", {})
        for key in (
            "name",
            "objective",
            "acceptance_metrics",
            "evidence_requirements",
            "linked_budget_items",
            "amount",
            "due_date",
            "route",
            "ip_ownership",
        ):
            if key in changes:
                rev[key] = deepcopy(changes[key])
        rev["negotiated_by"] = deepcopy(p.get("negotiated_by", []))
        if rev.get("route") != m["route_history"][-1]["route"]:
            m["route_history"].append({"revision": p["revision"], "route": rev["route"]})

    def _on_MilestoneCommitted(self, event: dict) -> None:
        p = event["payload"]
        m = self.milestones[p["milestone_id"]]
        m["status"] = "committed"
        m["committed_revision"] = p["revision"]

    def _on_AcceptanceRequested(self, event: dict) -> None:
        p = event["payload"]
        m = self.milestones[p["milestone_id"]]
        m["status"] = "completed"
        m["claimed_metrics"] = deepcopy(p["claimed_metrics"])

    def _on_AcceptanceApproved(self, event: dict) -> None:
        p = event["payload"]
        m = self.milestones[p["milestone_id"]]
        m["status"] = "accepted"
        m["final_metrics"] = deepcopy(p["final_metrics"])
        m["acceptance_review_id"] = p["review_id"]

    # -- 证据（含失败与更正）--------------------------------------------------

    def _index_evidence(self, event: dict, p: dict, kind: str) -> dict:
        ev = {
            "evidence_id": p["evidence_id"],
            "milestone_id": p["milestone_id"],
            "kind": kind,
            "title": p.get("title") or p.get("trial_name", ""),
            "requirement_code": p.get("requirement_code"),
            "result": p.get("result"),
            "data_ref": p.get("data_ref"),
            "ip_ownership": p.get("ip_ownership"),
            "submitter_id": p["submitter_id"],
            "submitted_for_revision": p.get("submitted_for_revision"),
            "version": event["version"],
            "occurred_at": event["occurred_at"],
            "status": "submitted",
            "accepted": None,
            "reviewer_id": None,
            "review_note": None,
            "superseded_by": None,
            "corrects": None,
            "failure_detail": None,
            "raw_event": event,
        }
        self.evidence[p["evidence_id"]] = ev
        self.milestones[p["milestone_id"]]["evidence_ids"].append(p["evidence_id"])
        return ev

    def _on_EvidenceSubmitted(self, event: dict) -> None:
        p = event["payload"]
        self._index_evidence(event, p, p.get("kind", "test_result"))

    def _on_TrialFailed(self, event: dict) -> None:
        p = event["payload"]
        ev = self._index_evidence(event, p, "failure_log")
        ev["failure_detail"] = {
            "trial_name": p["trial_name"],
            "hypothesis": p.get("hypothesis"),
            "observed": p.get("observed"),
            "root_cause": p.get("root_cause"),
            "learned_value": p.get("learned_value"),
        }

    def _on_EvidenceCorrected(self, event: dict) -> None:
        p = event["payload"]
        old = self.evidence[p["corrects_evidence_id"]]
        if old["submitter_id"] != p["submitter_id"]:
            raise ValueError("仅原提交人可发起更正")
        # 原始事实保留，仅标记被后继版本取代
        old["status"] = "superseded"
        old["superseded_by"] = p["evidence_id"]
        new = {
            "evidence_id": p["evidence_id"],
            "milestone_id": old["milestone_id"],
            "kind": old["kind"],
            "title": old["title"],
            "requirement_code": old.get("requirement_code"),
            "result": p.get("result", old["result"]),
            "data_ref": p.get("new_data_ref"),
            "ip_ownership": old.get("ip_ownership"),
            "submitter_id": p["submitter_id"],
            "submitted_for_revision": old.get("submitted_for_revision"),
            "version": event["version"],
            "occurred_at": event["occurred_at"],
            "status": "submitted",
            "accepted": None,
            "reviewer_id": None,
            "review_note": p.get("reason"),
            "superseded_by": None,
            "corrects": old["evidence_id"],
            "failure_detail": deepcopy(old.get("failure_detail")),
            "correction_reason": p.get("reason"),
            "raw_event": event,
        }
        self.evidence[p["evidence_id"]] = new
        self.milestones[old["milestone_id"]]["evidence_ids"].append(p["evidence_id"])

    def _on_EvidenceVerified(self, event: dict) -> None:
        p = event["payload"]
        ev = self.evidence[p["evidence_id"]]
        ev["status"] = "verified"
        ev["accepted"] = bool(p["accepted"])
        ev["reviewer_id"] = p["reviewer_id"]
        ev["review_note"] = p.get("review_note")

    # -- 外部评审 -------------------------------------------------------------

    def _on_ExternalReviewRecorded(self, event: dict) -> None:
        p = event["payload"]
        self.reviews[p["review_id"]] = {
            "review_id": p["review_id"],
            "milestone_id": p["milestone_id"],
            "scope": p["scope"],
            "verdict": p["verdict"],
            "recommendation": p.get("recommendation"),
            "expert_id": p["expert_id"],
            "expert_org": p.get("expert_org"),
            "data_ref": p.get("data_ref"),
            "occurred_at": event["occurred_at"],
        }

    # -- 路线变更 -------------------------------------------------------------

    def _on_RouteSwitchRequested(self, event: dict) -> None:
        p = event["payload"]
        self.changes[p["change_id"]] = {
            "change_id": p["change_id"],
            "project_code": p["project_code"],
            "milestone_id": p["milestone_id"],
            "status": "requested",
            "from_route": p["from_route"],
            "to_route": p["to_route"],
            "reason": p.get("reason"),
            "failure_evidence_ids": list(p.get("failure_evidence_ids", [])),
            "requested_by": p["requested_by"],
            "review_id": None,
            "budget_reestimate": None,
            "conditions": None,
            "occurred_at": event["occurred_at"],
        }
        self.milestones[p["milestone_id"]]["change_ids"].append(p["change_id"])

    def _on_RouteSwitchApproved(self, event: dict) -> None:
        p = event["payload"]
        ch = self.changes[p["change_id"]]
        ch["status"] = "approved"
        ch["review_id"] = p["review_id"]
        ch["budget_reestimate"] = deepcopy(p.get("budget_reestimate", []))
        ch["conditions"] = deepcopy(p.get("conditions", []))
        ch["approved_by"] = p["approved_by"]

        m = self.milestones[ch["milestone_id"]]
        rev = self._start_revision(m, p["new_milestone_revision"], event)
        rev["route"] = ch["to_route"]
        if p.get("new_requirements") is not None:
            rev["evidence_requirements"] = deepcopy(p["new_requirements"])
        if p.get("new_amount") is not None:
            rev["amount"] = p["new_amount"]
        if p.get("new_linked_budget_items") is not None:
            rev["linked_budget_items"] = list(p["new_linked_budget_items"])
        rev["change_id"] = p["change_id"]
        rev["ip_note"] = "切换前知识产权归属不变，见 conditions"
        m["route_history"].append(
            {"revision": p["new_milestone_revision"], "route": ch["to_route"]}
        )

    # -- 暂停 / 恢复 / 终止与处置义务 ------------------------------------------

    def _on_MilestoneSuspended(self, event: dict) -> None:
        p = event["payload"]
        scope = p["scope"]
        if scope in self.projects:
            self.projects[scope]["status"] = "suspended"
            for m in self.milestones.values():
                if m["project_code"] == scope and m["status"] not in ("accepted", "terminated"):
                    m["status"] = "suspended"
        elif scope in self.milestones:
            self.milestones[scope]["status"] = "suspended"
        else:
            raise ValueError(f"暂停范围未知: {scope}")
        for i, ob in enumerate(p.get("disposal_obligations", []), start=1):
            ref = f"{scope}-OB{i}"
            self.obligations[ref] = {
                "obligation_ref": ref,
                "scope": scope,
                "asset_or_fund": ob["asset_or_fund"],
                "type": ob["type"],
                "action": ob["action"],
                "deadline": ob.get("deadline"),
                "responsible_id": ob["responsible_id"],
                "status": "open",
                "recorded_at": event["occurred_at"],
                "fulfillment": None,
            }

    def _on_MilestoneResumed(self, event: dict) -> None:
        p = event["payload"]
        scope = p["scope"]
        if scope in self.projects:
            self.projects[scope]["status"] = "active"
            for m in self.milestones.values():
                if m["project_code"] == scope and m["status"] == "suspended":
                    m["status"] = "committed" if m["committed_revision"] else "proposed"
        elif scope in self.milestones:
            m = self.milestones[scope]
            m["status"] = "committed" if m["committed_revision"] else "proposed"

    def _on_ProjectTerminated(self, event: dict) -> None:
        p = event["payload"]
        code = event["entity_id"]
        self.projects[code]["status"] = "terminated"
        for m in self.milestones.values():
            if m["project_code"] == code and m["status"] not in ("accepted",):
                m["status"] = "terminated"
        # 未决处置义务保持 open，不随终止消失

    def _on_DisposalObligationFulfilled(self, event: dict) -> None:
        p = event["payload"]
        ob = self.obligations[p["obligation_ref"]]
        ob["status"] = "fulfilled"
        ob["fulfillment"] = {
            "action_taken": p["action_taken"],
            "proof_ref": p.get("proof_ref"),
            "fulfilled_by": p["fulfilled_by"],
            "occurred_at": event["occurred_at"],
        }

    # -- 付款 -----------------------------------------------------------------

    def _on_PaymentApproved(self, event: dict) -> None:
        p = event["payload"]
        self.payments[p["payment_id"]] = {
            "payment_id": p["payment_id"],
            "milestone_id": p["milestone_id"],
            "amount": p["amount"],
            "currency": p.get("currency", "CNY"),
            "linked_budget_items": deepcopy(p.get("linked_budget_items", [])),
            "approved_by": p["approved_by"],
            "evidence_snapshot": deepcopy(p["evidence_snapshot"]),
            "gate_result": deepcopy(p["gate_result"]),
            "status": "approved",
            "approved_at": event["occurred_at"],
            "execution": None,
        }

    def _on_PaymentExecuted(self, event: dict) -> None:
        p = event["payload"]
        pay = self.payments[p["payment_id"]]
        pay["status"] = "executed"
        pay["execution"] = {
            "executed_by": p["executed_by"],
            "payee": p.get("payee"),
            "voucher_ref": p.get("voucher_ref"),
            "executed_at": p.get("executed_at", event["occurred_at"]),
        }

    # -- 查询辅助 -------------------------------------------------------------

    def milestone(self, milestone_id: str) -> dict:
        return self.milestones[milestone_id]

    def current_revision(self, milestone_id: str) -> dict:
        return self.milestones[milestone_id]["revisions"][-1]

    def effective_evidence(self, milestone_id: str) -> list[dict]:
        """当前有效证据：未被更正取代。失败记录同样有效。"""
        return [
            self.evidence[eid]
            for eid in self.milestones[milestone_id]["evidence_ids"]
            if self.evidence[eid]["status"] != "superseded"
        ]

    def paid_amount(self, milestone_id: str) -> float:
        return sum(
            pay["amount"]
            for pay in self.payments.values()
            if pay["milestone_id"] == milestone_id
        )
