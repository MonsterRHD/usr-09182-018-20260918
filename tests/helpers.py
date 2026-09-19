"""测试公共装置：确定性时钟与最小可用项目。"""

from milestone_funding import ProjectService


class Clock:
    """生成确定性的带时区时间戳，避免测试依赖真实时间。"""

    def __init__(self):
        self.n = 0

    def tick(self) -> str:
        self.n += 1
        return f"2026-03-01T09:{self.n:02d}:00+08:00"


def boot_project(project_id="P-TEST", milestones=None):
    """登记一个项目并协商好阶段承诺，返回 (service, clock)。"""
    svc = ProjectService()
    clock = Clock()
    svc.register_project(
        project_id,
        occurred_at=clock.tick(),
        goals=["目标A"],
        budget_subjects={"设备费": 500, "测试费": 500},
        route="路线甲",
    )
    svc.negotiate_commitment(
        project_id,
        occurred_at=clock.tick(),
        milestones=milestones
        or [
            {
                "milestone_id": "M1",
                "title": "方案设计",
                "goal_ref": "目标A",
                "required_evidence": ["设计文档"],
                "budget": 400,
            }
        ],
    )
    return svc, clock


def prepare_disbursable(svc, clock, project_id="P-TEST", milestone_id="M1", evidence_id="EV-1"):
    """提交并采纳里程碑所需证据，使其满足拨付的证据闸门。"""
    svc.submit_evidence(
        project_id,
        occurred_at=clock.tick(),
        evidence_id=evidence_id,
        milestone_id=milestone_id,
        kind="设计文档",
        summary="方案设计文档 v1",
    )
    svc.adopt_evidence(project_id, occurred_at=clock.tick(), evidence_id=evidence_id, reviewer="王评")
    return evidence_id
