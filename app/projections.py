"""只读投影：面向不同角色的脱敏视图与审计追溯视图。

投影不改变状态；敏感字段可见性以 contracts/domain.json 的 sensitive_fields 为准。
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from .event_store import event_fingerprint
from .state import ReplayState

_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contracts" / "domain.json"


def load_sensitive_fields() -> dict[str, list[str]]:
    contract = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    return contract["sensitive_fields"]


SENSITIVE_FIELDS = load_sensitive_fields()


def mask_event(event: dict, role: str) -> dict:
    """按角色过滤事件：顶层 actor_id 与载荷中的敏感字段双重控制。"""
    out = deepcopy(event)
    allowed_top = SENSITIVE_FIELDS.get("actor_id", [])
    if role not in allowed_top:
        out.pop("actor_id", None)
    payload = out.get("payload", {})
    for field_name, allowed_roles in SENSITIVE_FIELDS.items():
        if field_name == "actor_id":
            continue
        if field_name in payload and role not in allowed_roles:
            payload[field_name] = "***REDACTED***"
    return out


def mask_value(value, field_name: str, role: str):
    if role in SENSITIVE_FIELDS.get(field_name, []):
        return value
    return "***REDACTED***"


# ---------------------------------------------------------------------------
# 审计追溯：每笔资金 ↔ 批准时点证据
# ---------------------------------------------------------------------------

def payment_trace(store) -> list[dict]:
    """每笔付款对应当时批准的证据快照；并标注证据此后是否被更正（付款仍指向旧版本）。"""
    state = ReplayState.replay(store.all_events())
    rows: list[dict] = []
    for pay in state.payments.values():
        entries = []
        for entry in pay["evidence_snapshot"]["entries"]:
            ev = state.evidence.get(entry["evidence_id"])
            later_correction = None
            current_status = None
            if ev is not None:
                current_status = ev["status"]
                if ev["status"] == "superseded" and ev.get("superseded_by"):
                    later_correction = ev["superseded_by"]
            entries.append(
                {
                    **entry,
                    "evidence_status_today": current_status,
                    "superseded_later_by": later_correction,
                    "frozen_intact": ev is None
                    or event_fingerprint(ev["raw_event"]) == entry["sha256"]
                    or ev["status"] == "superseded",  # 原件不可变，只是被标记
                }
            )
        m = state.milestones.get(pay["milestone_id"], {})
        rows.append(
            {
                "payment_id": pay["payment_id"],
                "milestone_id": pay["milestone_id"],
                "project_code": m.get("project_code"),
                "amount": pay["amount"],
                "currency": pay["currency"],
                "status": pay["status"],
                "approved_at": pay["approved_at"],
                "commit_revision": pay["evidence_snapshot"]["commit_revision"],
                "route_at_approval": pay["evidence_snapshot"]["route"],
                "snapshot_id": pay["evidence_snapshot"]["snapshot_id"],
                "evidence_entries": entries,
                "execution": pay.get("execution"),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 验收选取：中途切换技术路线但最终达标的项目
# ---------------------------------------------------------------------------

def acceptance_candidates(store) -> list[dict]:
    state = ReplayState.replay(store.all_events())
    out: list[dict] = []
    for m in state.milestones.values():
        if m["status"] != "accepted":
            continue
        switch_changes = [c for c in (state.changes[cid] for cid in m["change_ids"]) if c["status"] == "approved"]
        if not switch_changes:
            continue
        first_switch = switch_changes[0]
        revisions = m["revisions"]
        final = revisions[-1]
        # 切换前金额：切换产生的修订带 change_id，取其前一版（即切换时已承诺金额）
        switch_idx = next(
            (idx for idx, r in enumerate(revisions) if r.get("change_id") == first_switch["change_id"]),
            0,
        )
        initial = revisions[switch_idx - 1] if switch_idx > 0 else revisions[0]
        # 预算重估：按里程碑金额与科目预算版本对照
        project = state.projects[m["project_code"]]
        budget_versions = project["budget_versions"]
        out.append(
            {
                "project_code": m["project_code"],
                "milestone_id": m["milestone_id"],
                "name": final["name"],
                "route_before": first_switch["from_route"],
                "route_after": first_switch["to_route"],
                "switch_change_id": first_switch["change_id"],
                "milestone_amount_before_switch": initial["amount"],
                "milestone_amount_after_switch": final["amount"],
                "budget_versions_kept": len(budget_versions),
                "final_metrics": m.get("final_metrics"),
                "acceptance_review_id": m.get("acceptance_review_id"),
                "payments": _milestone_payments(state, m["milestone_id"]),
                "preserved_failures": _preserved_failures(state, m["milestone_id"]),
            }
        )
    return out


def _milestone_payments(state: ReplayState, milestone_id: str) -> list[dict]:
    return [
        {
            "payment_id": p["payment_id"],
            "amount": p["amount"],
            "status": p["status"],
            "snapshot_id": p["evidence_snapshot"]["snapshot_id"],
            "commit_revision": p["evidence_snapshot"]["commit_revision"],
        }
        for p in state.payments.values()
        if p["milestone_id"] == milestone_id
    ]


def _preserved_failures(state: ReplayState, milestone_id: str) -> list[dict]:
    rows = []
    for eid in state.milestones[milestone_id]["evidence_ids"]:
        ev = state.evidence[eid]
        if ev["kind"] == "failure_log":
            rows.append(
                {
                    "evidence_id": ev["evidence_id"],
                    "trial_name": ev["failure_detail"]["trial_name"],
                    "learned_value": ev["failure_detail"]["learned_value"],
                    "status": ev["status"],
                    "cited_by_changes": [
                        cid
                        for cid, ch in state.changes.items()
                        if ev["evidence_id"] in ch.get("failure_evidence_ids", [])
                    ],
                }
            )
    return rows


# ---------------------------------------------------------------------------
# 失败价值台账 / 处置义务 / 预算历史
# ---------------------------------------------------------------------------

def failure_value_ledger(store) -> list[dict]:
    state = ReplayState.replay(store.all_events())
    rows = []
    for ev in state.evidence.values():
        if ev["kind"] != "failure_log":
            continue
        rows.append(
            {
                "evidence_id": ev["evidence_id"],
                "milestone_id": ev["milestone_id"],
                "project_code": state.milestones[ev["milestone_id"]]["project_code"],
                "trial_name": ev["failure_detail"]["trial_name"],
                "hypothesis": ev["failure_detail"]["hypothesis"],
                "root_cause": ev["failure_detail"].get("root_cause"),
                "learned_value": ev["failure_detail"]["learned_value"],
                "status": ev["status"],  # 即便被更正，原始事实仍保留
                "used_in_switch": [
                    cid
                    for cid, ch in state.changes.items()
                    if ev["evidence_id"] in ch.get("failure_evidence_ids", [])
                ],
            }
        )
    return rows


def disposal_obligations(store) -> list[dict]:
    state = ReplayState.replay(store.all_events())
    return [dict(ob) for ob in state.obligations.values()]


def budget_history(store, project_code: str) -> list[dict]:
    state = ReplayState.replay(store.all_events())
    project = state.projects[project_code]
    return deepcopy(project["budget_versions"])


def project_dossier(store, project_code: str, role: str) -> dict:
    """按角色脱敏的项目全卷：承诺版本、证据、评审、变更、付款、义务。"""
    state = ReplayState.replay(store.all_events())
    project = deepcopy(state.projects[project_code])
    raw_events = [mask_event(e, role) for e in store.all_events() if _event_of_project(e, state, project_code)]
    milestones = []
    for m in state.milestones.values():
        if m["project_code"] != project_code:
            continue
        mc = deepcopy(m)
        mc.pop("evidence_ids", None)
        mc["evidence"] = [_mask_evidence(state.evidence[eid], role) for eid in m["evidence_ids"]]
        milestones.append(mc)
    payments = [
        _mask_payment(p, role)
        for p in state.payments.values()
        if state.milestones[p["milestone_id"]]["project_code"] == project_code
    ]
    return {
        "project": project,
        "milestones": milestones,
        "reviews": [
            _mask_review(r, role)
            for r in state.reviews.values()
            if state.milestones[r["milestone_id"]]["project_code"] == project_code
        ],
        "changes": [
            deepcopy(c)
            for c in state.changes.values()
            if c["project_code"] == project_code
        ],
        "payments": payments,
        "obligations": [
            deepcopy(o) for o in state.obligations.values() if o["scope"] == project_code
        ],
        "events": raw_events,
    }


def _event_of_project(event: dict, state: ReplayState, project_code: str) -> bool:
    eid = event["entity_id"]
    if eid == project_code:
        return True
    if eid in state.milestones and state.milestones[eid]["project_code"] == project_code:
        return True
    payload = event["payload"]
    if payload.get("milestone_id") in state.milestones:
        return state.milestones[payload["milestone_id"]]["project_code"] == project_code
    if payload.get("project_code") == project_code:
        return True
    return False


def _mask_evidence(ev: dict, role: str) -> dict:
    out = deepcopy(ev)
    out.pop("raw_event", None)
    out["submitter_id"] = mask_value(out.get("submitter_id"), "submitter_id", role)
    out["reviewer_id"] = mask_value(out.get("reviewer_id"), "reviewer_id", role)
    if out.get("data_ref") is not None:
        out["data_ref"] = mask_value(out.get("data_ref"), "data_ref", role)
    return out


def _mask_review(r: dict, role: str) -> dict:
    out = deepcopy(r)
    out["expert_id"] = mask_value(out.get("expert_id"), "expert_id", role)
    if out.get("data_ref") is not None:
        out["data_ref"] = mask_value(out.get("data_ref"), "data_ref", role)
    return out


def _mask_payment(p: dict, role: str) -> dict:
    out = deepcopy(p)
    out["approved_by"] = mask_value(out.get("approved_by"), "approved_by", role)
    if out.get("execution"):
        out["execution"]["executed_by"] = mask_value(
            out["execution"].get("executed_by"), "executed_by", role
        )
        out["execution"]["payee"] = mask_value(out["execution"].get("payee"), "payee", role)
        if out["execution"].get("voucher_ref") is not None:
            out["execution"]["voucher_ref"] = mask_value(
                out["execution"].get("voucher_ref"), "voucher_ref", role
            )
    return out
