"""验收场景：机器人灵巧手项目中途切换技术路线、最终达标。

时间线：谐波减速器直驱路线 → 原理样机试验失败 → 外部评审 →
切换为腱绳驱动+行星减速并重估后续预算 → 各里程碑证据齐备后拨付 → 验收通过。
平台须保留失败的价值，且每笔资金都对应当时批准的证据版本。
"""

import unittest

from helpers import Clock
from milestone_funding import DomainError, ProjectService
from milestone_funding.permissions import SENSITIVE_KEYS

PID = "P-灵巧手"


def walk_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(node, list):
        for item in node:
            yield from walk_keys(item)


class RouteSwitchAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        svc = ProjectService()
        clock = Clock()
        t = clock.tick

        svc.register_project(
            PID, occurred_at=t(),
            goals=["灵巧手抓握力≥50N", "关键零部件国产化率≥90%"],
            budget_subjects={"设备费": 300, "材料费": 150, "测试费": 100, "人员费": 200},
            route="谐波减速器直驱",
        )
        svc.negotiate_commitment(PID, occurred_at=t(), milestones=[
            {"milestone_id": "M1", "title": "方案设计", "goal_ref": "灵巧手抓握力≥50N",
             "required_evidence": ["设计文档"], "budget": 100},
            {"milestone_id": "M2", "title": "原理样机", "goal_ref": "灵巧手抓握力≥50N",
             "required_evidence": ["试验报告", "测试数据"], "budget": 350},
            {"milestone_id": "M3", "title": "集成验证", "goal_ref": "关键零部件国产化率≥90%",
             "required_evidence": ["验收测试报告"], "budget": 250},
        ])

        # M1 顺利完成并拨付
        svc.submit_evidence(PID, occurred_at=t(), evidence_id="EV-M1", milestone_id="M1",
                            kind="设计文档", summary="总体方案设计文档")
        svc.adopt_evidence(PID, occurred_at=t(), evidence_id="EV-M1", reviewer="王评")
        svc.confirm_milestone(PID, occurred_at=t(), milestone_id="M1")
        cls._disburse(svc, t, "D-1", "M1", 100)

        # M2 谐波路线试验失败：记录失败的价值
        svc.record_failure(
            PID, occurred_at=t(), failure_id="F-1", milestone_id="M2",
            experiment="谐波减速器直驱样机抓握试验：背隙超限，抓握力仅 32N",
            lesson="谐波路线在小型化下刚度不足；腱绳传动试验数据可为新路线复用",
        )
        svc.record_external_review(PID, occurred_at=t(), review_id="R-1",
                                   expert="外部专家组", conclusion="建议改用腱绳驱动+行星减速")
        svc.record_ip(PID, occurred_at=t(), ip_id="IP-1", owner="承担单位/资助方",
                      share="70/30", scope="腱绳张紧机构专利")

        # 切换技术路线，重估后续里程碑预算（M2 350→300，M3 250→300）
        svc.change_route(PID, occurred_at=t(), new_route="腱绳驱动+行星减速",
                         failure_id="F-1", review_id="R-1", reestimates={"M2": 300, "M3": 300})

        # 新路线下 M2 达标并拨付
        svc.submit_evidence(PID, occurred_at=t(), evidence_id="EV-M2A", milestone_id="M2",
                            kind="试验报告", summary="腱绳驱动样机抓握试验报告：52N")
        svc.submit_evidence(PID, occurred_at=t(), evidence_id="EV-M2B", milestone_id="M2",
                            kind="测试数据", summary="样机全工况测试数据包")
        svc.adopt_evidence(PID, occurred_at=t(), evidence_id="EV-M2A", reviewer="王评")
        svc.adopt_evidence(PID, occurred_at=t(), evidence_id="EV-M2B", reviewer="王评")
        svc.confirm_milestone(PID, occurred_at=t(), milestone_id="M2")
        cls._disburse(svc, t, "D-2", "M2", 300)

        # M3 达标并拨付；拨付后证据被更正（补盖章），拨款单仍锚定当时版本
        svc.submit_evidence(PID, occurred_at=t(), evidence_id="EV-M3", milestone_id="M3",
                            kind="验收测试报告", summary="集成验证测试报告：抓握力 52N，国产化率 93%")
        svc.adopt_evidence(PID, occurred_at=t(), evidence_id="EV-M3", reviewer="王评")
        svc.confirm_milestone(PID, occurred_at=t(), milestone_id="M3")
        cls._disburse(svc, t, "D-3", "M3", 250)
        svc.correct_evidence(PID, occurred_at=t(), evidence_id="EV-M3",
                             new_summary="集成验证测试报告（补盖检测机构章）", reason="补盖公章")

        svc.accept_project(PID, occurred_at=t(), summary="抓握力 52N、国产化率 93%，指标全部达标")
        cls.svc = svc

    @staticmethod
    def _disburse(svc, t, did, mid, amount):
        svc.request_disbursement(PID, occurred_at=t(), disbursement_id=did,
                                 milestone_id=mid, amount=amount, requester="李工")
        svc.approve_disbursement(PID, occurred_at=t(), disbursement_id=did, role="技术评审", actor="王评")
        svc.approve_disbursement(PID, occurred_at=t(), disbursement_id=did, role="财务审批", actor="赵财")
        svc.execute_disbursement(PID, occurred_at=t(), disbursement_id=did)

    def test_accepted_after_route_switch(self):
        state = self.svc.state(PID)
        self.assertEqual(state.status, "验收通过")
        self.assertEqual(state.route, "腱绳驱动+行星减速")

    def test_failure_value_preserved(self):
        state = self.svc.state(PID)
        failure = state.failures["F-1"]
        self.assertTrue(failure.linked_route_change)
        preserved = state.acceptance["preserved_failures"]
        self.assertEqual(preserved[0]["failure_id"], "F-1")
        self.assertIn("腱绳", preserved[0]["lesson"])

    def test_budget_reestimated_with_history(self):
        state = self.svc.state(PID)
        self.assertEqual(state.milestones["M2"].budget, 300)
        self.assertEqual(state.milestones["M2"].budget_history[0]["from"], 350)
        self.assertEqual(state.milestones["M3"].budget, 300)
        self.assertEqual(state.milestones["M3"].budget_history[0]["from"], 250)

    def test_every_disbursement_anchors_evidence_approved_at_that_time(self):
        state = self.svc.state(PID)
        executed = [d for d in state.disbursements.values() if d.status == "已执行"]
        self.assertEqual(len(executed), 3)
        for d in executed:
            self.assertTrue(d.evidence_refs, f"{d.disbursement_id} 缺少证据锚点")
        # D-3 执行时 EV-M3 为 v1；之后更正为 v2，拨款单仍锚定 v1
        self.assertEqual(state.disbursements["D-3"].evidence_refs,
                         [{"evidence_id": "EV-M3", "kind": "验收测试报告", "version": 1}])
        self.assertEqual(state.evidences["EV-M3"].version, 2)
        self.assertEqual(state.acceptance["total_disbursed"], 650)

    def test_permission_redaction(self):
        expert_view = self.svc.view(PID, role="外部专家")
        self.assertFalse(SENSITIVE_KEYS & set(walk_keys(expert_view)))
        self.assertEqual(expert_view["milestones"][1]["title"], "原理样机")  # 技术信息可见

        auditor_view = self.svc.view(PID, role="审计")
        self.assertEqual(auditor_view["budget_subjects"]["设备费"], 300)
        self.assertEqual(auditor_view["ip_records"]["IP-1"]["share"], "70/30")
        self.assertEqual(auditor_view["acceptance"]["total_disbursed"], 650)


class RouteChangeRuleTest(unittest.TestCase):
    def test_route_change_requires_recorded_failure(self):
        from helpers import boot_project
        svc, clock = boot_project()
        with self.assertRaises(DomainError):
            svc.change_route("P-TEST", occurred_at=clock.tick(), new_route="路线乙",
                             failure_id="F-X", reestimates={})

    def test_disbursed_milestone_cannot_be_reestimated(self):
        from helpers import boot_project, prepare_disbursable
        svc, clock = boot_project()
        prepare_disbursable(svc, clock)
        svc.request_disbursement("P-TEST", occurred_at=clock.tick(), disbursement_id="D-1",
                                 milestone_id="M1", amount=100, requester="李工")
        svc.approve_disbursement("P-TEST", occurred_at=clock.tick(), disbursement_id="D-1", role="技术评审", actor="王评")
        svc.approve_disbursement("P-TEST", occurred_at=clock.tick(), disbursement_id="D-1", role="财务审批", actor="赵财")
        svc.execute_disbursement("P-TEST", occurred_at=clock.tick(), disbursement_id="D-1")
        svc.record_failure("P-TEST", occurred_at=clock.tick(), failure_id="F-1",
                           milestone_id="M1", experiment="试验未达预期", lesson="需要换路线")
        with self.assertRaises(DomainError):
            svc.change_route("P-TEST", occurred_at=clock.tick(), new_route="路线乙",
                             failure_id="F-1", reestimates={"M1": 200})

    def test_commitment_total_capped_by_budget_subjects(self):
        svc = ProjectService()
        clock = Clock()
        svc.register_project("P-CAP", occurred_at=clock.tick(), goals=["G"],
                             budget_subjects={"设备费": 100}, route="路线甲")
        with self.assertRaises(DomainError):
            svc.negotiate_commitment("P-CAP", occurred_at=clock.tick(), milestones=[
                {"milestone_id": "M1", "title": "T", "goal_ref": "G",
                 "required_evidence": [], "budget": 101},
            ])


if __name__ == "__main__":
    unittest.main()
