"""测试共用：快速搭建一个最小可拨款世界。"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.event_store import EventStore
from app.service import (
    Actor,
    ProjectService,
    APPLICANT,
    TECH_REVIEWER,
    EXTERNAL_EXPERT,
    APPROVER,
    FINANCE,
    FUNDER,
)

TZ = "+08:00"


def dt(value: str) -> datetime:
    return datetime.fromisoformat(f"{value}{TZ}")


def make_world() -> dict:
    store = EventStore()
    svc = ProjectService(store)
    actors = {
        "applicant": Actor("A-zhang", APPLICANT, org="启辰机器人有限公司"),
        "reviewer": Actor("T-li", TECH_REVIEWER, org="核验中心"),
        "expert": Actor("E-wang", EXTERNAL_EXPERT, org="宁波材料所"),
        "approver": Actor("AP-qian", APPROVER, org="资金处"),
        "finance": Actor("FN-sun", FINANCE, org="结算中心"),
        "funder": Actor("F-zhao", FUNDER, org="科技厅"),
    }
    return {"store": store, "svc": svc, "actors": actors}


def seed_project(w: dict, total_budget: float = 1_000_000) -> None:
    a = w["actors"]
    w["svc"].register_project(
        a["applicant"],
        project_code="P-1",
        title="测试项目",
        research_goal="验证拨付闸门",
        applicant_name="启辰机器人有限公司",
        total_budget=total_budget,
        occurred_at=dt("2026-01-08T09:00:00"),
    )
    w["svc"].establish_budget(
        a["applicant"],
        project_code="P-1",
        items=[
            {"item_id": "B-EQ", "name": "设备费", "category": "设备费", "amount": 600_000},
            {"item_id": "B-TEST", "name": "测试费", "category": "测试费", "amount": 400_000},
        ],
        basis="预算v1",
        occurred_at=dt("2026-01-08T09:30:00"),
    )


def seed_committed_milestone(w: dict, amount: float = 500_000, requirements=None, route="路线A") -> None:
    a = w["actors"]
    reqs = requirements or [
        {"code": "R1", "name": "试验报告", "kind": "test_result", "required": True},
    ]
    w["svc"].propose_milestone(
        a["applicant"],
        milestone_id="M-1",
        project_code="P-1",
        stage_index=1,
        name="阶段一",
        objective="达成指标",
        acceptance_metrics=[{"name": "寿命_h", "operator": ">=", "target": 10000}],
        evidence_requirements=reqs,
        linked_budget_items=["B-EQ", "B-TEST"],
        amount=amount,
        due_date="2026-08-31",
        route=route,
        occurred_at=dt("2026-01-10T10:00:00"),
    )
    w["svc"].commit_milestone(a["funder"], milestone_id="M-1", occurred_at=dt("2026-01-15T14:00:00"))
