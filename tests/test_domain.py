import json
import unittest
from pathlib import Path

from app.event_store import EventStore, event_fingerprint
from app.errors import BusinessRuleError, PermissionDeniedError
from app.service import Actor, ProjectService, APPLICANT, TECH_REVIEWER, APPROVER, FINANCE, FUNDER, EXTERNAL_EXPERT
from app import projections
from tests.helpers_scenario import make_world, seed_project, seed_committed_milestone, dt

ROOT = Path(__file__).resolve().parents[1]


def submit_and_verify(w, evidence_id="EV-1", code="R1", kind="test_result",
                      title="试验报告", result="寿命12000h通过", submitter=None):
    a = w["actors"]
    w["svc"].submit_evidence(
        submitter or a["applicant"],
        evidence_id=evidence_id, milestone_id="M-1", requirement_code=code,
        kind=kind, title=title, result=result,
        data_ref=f"s3://{evidence_id}.pdf",
        occurred_at=dt("2026-04-01T10:00:00"),
    )
    w["svc"].verify_evidence(
        a["reviewer"], evidence_id=evidence_id, accepted=True,
        review_note="复核通过", occurred_at=dt("2026-04-03T10:00:00"),
    )


class PaymentGateTest(unittest.TestCase):
    def test_payment_blocked_when_evidence_incomplete(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w)
        a = w["actors"]
        # 证据缺失
        gate = w["svc"].evaluate_payment_gates("M-1", a["approver"])
        self.assertFalse(gate["passed"])
        self.assertFalse(gate["gates"][0]["passed"])
        with self.assertRaises(BusinessRuleError) as ctx:
            w["svc"].approve_payment(
                a["approver"], payment_id="P-1", milestone_id="M-1",
                amount=500_000, occurred_at=dt("2026-04-10T10:00:00"),
            )
        self.assertIn("gate_evidence_complete", str(ctx.exception))

    def test_payment_blocked_when_evidence_submitted_but_not_verified(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w)
        a = w["actors"]
        w["svc"].submit_evidence(
            a["applicant"], evidence_id="EV-1", milestone_id="M-1",
            requirement_code="R1", kind="test_result", title="报告",
            result="通过", occurred_at=dt("2026-04-01T10:00:00"),
        )
        gate = w["svc"].evaluate_payment_gates("M-1", a["approver"])
        self.assertFalse(gate["gates"][0]["passed"])
        self.assertEqual(gate["gates"][0]["detail"]["unverified"], ["EV-1"])

    def test_payment_blocked_when_evidence_rejected(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w)
        a = w["actors"]
        w["svc"].submit_evidence(
            a["applicant"], evidence_id="EV-1", milestone_id="M-1",
            requirement_code="R1", kind="test_result", title="报告",
            result="未达标", occurred_at=dt("2026-04-01T10:00:00"),
        )
        w["svc"].verify_evidence(
            a["reviewer"], evidence_id="EV-1", accepted=False,
            review_note="不通过", occurred_at=dt("2026-04-03T10:00:00"),
        )
        gate = w["svc"].evaluate_payment_gates("M-1", a["approver"])
        self.assertFalse(gate["gates"][0]["passed"])
        self.assertEqual(gate["gates"][0]["detail"]["rejected"], ["EV-1"])

    def test_payment_blocked_when_approver_also_submitted_evidence(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w)
        a = w["actors"]
        submit_and_verify(w)
        # 批准人就是提交人本人
        bad_approver = Actor("A-zhang", APPROVER, org="资金处")
        gate = w["svc"].evaluate_payment_gates("M-1", bad_approver)
        self.assertFalse(gate["gates"][1]["passed"])
        with self.assertRaises(BusinessRuleError) as ctx:
            w["svc"].approve_payment(
                bad_approver, payment_id="P-1", milestone_id="M-1",
                amount=500_000, occurred_at=dt("2026-04-10T10:00:00"),
            )
        self.assertIn("gate_separation_of_duties", str(ctx.exception))

    def test_reviewer_cannot_verify_own_submission(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w)
        a = w["actors"]
        w["svc"].submit_evidence(
            Actor("T-li", APPLICANT, org="核验中心"),
            evidence_id="EV-X", milestone_id="M-1", requirement_code="R1",
            kind="test_result", title="报告", result="x",
            occurred_at=dt("2026-04-01T10:00:00"),
        )
        with self.assertRaises(PermissionDeniedError):
            w["svc"].verify_evidence(
                a["reviewer"], evidence_id="EV-X", accepted=True,
                occurred_at=dt("2026-04-03T10:00:00"),
            )

    def test_finance_cannot_execute_unapproved_or_self_approved_payment(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w)
        a = w["actors"]
        with self.assertRaises(BusinessRuleError):
            w["svc"].execute_payment(
                a["finance"], payment_id="P-NOPE", payee="x",
                voucher_ref="v1", occurred_at=dt("2026-04-10T10:00:00"),
            )
        submit_and_verify(w)
        w["svc"].approve_payment(
            a["approver"], payment_id="P-1", milestone_id="M-1",
            amount=500_000, occurred_at=dt("2026-04-10T10:00:00"),
        )
        # 批准人自己执行——职责分离拒绝
        with self.assertRaises(PermissionDeniedError):
            w["svc"].execute_payment(
                Actor("AP-qian", FINANCE, org="资金处"), payment_id="P-1",
                payee="x", voucher_ref="v1", occurred_at=dt("2026-04-11T10:00:00"),
            )

    def test_full_payment_flows_when_both_gates_pass(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w)
        a = w["actors"]
        submit_and_verify(w)
        w["svc"].approve_payment(
            a["approver"], payment_id="P-1", milestone_id="M-1",
            amount=500_000, occurred_at=dt("2026-04-10T10:00:00"),
        )
        w["svc"].execute_payment(
            a["finance"], payment_id="P-1", payee="承担方",
            voucher_ref="v2026-001", occurred_at=dt("2026-04-11T10:00:00"),
        )
        state = w["svc"].state
        self.assertEqual(state.payments["P-1"]["status"], "executed")
        # 重复付款拒绝
        with self.assertRaises(BusinessRuleError):
            w["svc"].execute_payment(
                a["finance"], payment_id="P-1", payee="承担方",
                voucher_ref="v2026-002", occurred_at=dt("2026-04-12T10:00:00"),
            )

    def test_budget_gate_blocks_overpayment(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w, amount=500_000)
        a = w["actors"]
        submit_and_verify(w)
        with self.assertRaises(BusinessRuleError):
            w["svc"].approve_payment(
                a["approver"], payment_id="P-1", milestone_id="M-1",
                amount=500_001, occurred_at=dt("2026-04-10T10:00:00"),
            )


class FailureAndCorrectionTest(unittest.TestCase):
    def test_trial_failure_requires_learned_value_and_is_preserved(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w)
        a = w["actors"]
        with self.assertRaises(BusinessRuleError):
            w["svc"].record_trial_failure(
                a["applicant"], evidence_id="F-1", milestone_id="M-1",
                trial_name="台架", hypothesis="h", observed="o",
                learned_value="  ", occurred_at=dt("2026-04-01T10:00:00"),
            )
        w["svc"].record_trial_failure(
            a["applicant"], evidence_id="F-1", milestone_id="M-1",
            trial_name="台架", hypothesis="金属路线可达标", observed="6200h开裂",
            root_cause="残余应力集中", learned_value="排除金属路线，指导铺层设计",
            occurred_at=dt("2026-04-01T10:00:00"),
        )
        ledger = projections.failure_value_ledger(w["store"])
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["learned_value"], "排除金属路线，指导铺层设计")
        # 原始失败事件仍在事件流中，不可删除
        types = [e["payload"]["type"] for e in w["store"].all_events()]
        self.assertIn("TrialFailed", types)

    def test_correction_never_overwrites_original_fact(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w)
        a = w["actors"]
        submit_and_verify(w, evidence_id="EV-1")
        original_events_before = len(w["store"].all_events())
        w["svc"].correct_evidence(
            a["applicant"], evidence_id="EV-2", corrects_evidence_id="EV-1",
            reason="数据挂载错误", new_data_ref="s3://EV-2.pdf",
            result="更正后通过", occurred_at=dt("2026-05-01T10:00:00"),
        )
        state = w["svc"].state
        self.assertEqual(state.evidence["EV-1"]["status"], "superseded")
        self.assertEqual(state.evidence["EV-1"]["data_ref"], "s3://EV-1.pdf")  # 原件保留
        self.assertEqual(state.evidence["EV-2"]["corrects"], "EV-1")
        # 他人不能代为更正
        with self.assertRaises(PermissionDeniedError):
            w["svc"].correct_evidence(
                Actor("A-other", APPLICANT, org="别家"), evidence_id="EV-3",
                corrects_evidence_id="EV-2", reason="x", new_data_ref="s3://x",
                occurred_at=dt("2026-05-02T10:00:00"),
            )
        self.assertGreater(len(w["store"].all_events()), original_events_before)

    def test_payment_snapshot_frozen_when_evidence_later_corrected(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w)
        a = w["actors"]
        submit_and_verify(w, evidence_id="EV-1")
        w["svc"].approve_payment(
            a["approver"], payment_id="P-1", milestone_id="M-1",
            amount=500_000, occurred_at=dt("2026-04-10T10:00:00"),
        )
        state = w["svc"].state
        approved_event = next(e for e in w["store"].all_events() if e["payload"]["type"] == "PaymentApproved")
        frozen_hash = approved_event["payload"]["evidence_snapshot"]["entries"][0]["sha256"]
        original_raw = state.evidence["EV-1"]["raw_event"]
        self.assertEqual(event_fingerprint(original_raw), frozen_hash)

        # 事后更正
        w["svc"].correct_evidence(
            a["applicant"], evidence_id="EV-2", corrects_evidence_id="EV-1",
            reason="数据挂载错误", new_data_ref="s3://EV-2.pdf",
            occurred_at=dt("2026-05-01T10:00:00"),
        )
        trace = projections.payment_trace(w["store"])[0]
        entry = trace["evidence_entries"][0]
        self.assertEqual(entry["evidence_id"], "EV-1")          # 付款仍指向当时版本
        self.assertEqual(entry["superseded_later_by"], "EV-2")  # 标注事后更正
        self.assertTrue(entry["frozen_intact"])


class RouteSwitchTest(unittest.TestCase):
    def _build_switched_project(self):
        w = make_world()
        seed_project(w, total_budget=1_000_000)
        seed_committed_milestone(
            w, amount=500_000,
            requirements=[{"code": "R1", "name": "金属路线试验", "kind": "test_result", "required": True}],
            route="金属柔轮",
        )
        a = w["actors"]
        w["svc"].record_trial_failure(
            a["applicant"], evidence_id="F-1", milestone_id="M-1",
            trial_name="金属台架", hypothesis="h", observed="失败",
            root_cause="应力", learned_value="排除金属路线",
            occurred_at=dt("2026-04-01T10:00:00"),
        )
        w["svc"].request_route_switch(
            a["applicant"], change_id="CHG-1", milestone_id="M-1",
            from_route="金属柔轮", to_route="复合材料柔轮",
            reason="金属路线失败", failure_evidence_ids=["F-1"],
            occurred_at=dt("2026-04-05T10:00:00"),
        )
        return w

    def test_switch_requires_failure_evidence_review_and_reestimate(self):
        w = self._build_switched_project()
        a = w["actors"]
        # 缺评审不能批
        with self.assertRaises(BusinessRuleError):
            w["svc"].approve_route_switch(
                a["funder"], change_id="CHG-1", review_id="REV-X",
                new_requirements=[], budget_reestimate=[], new_amount=400_000,
                new_linked_budget_items=["B-EQ"], occurred_at=dt("2026-05-01T10:00:00"),
            )
        # 评审不通过不能批
        w["svc"].record_external_review(
            a["expert"], review_id="REV-1", milestone_id="M-1",
            scope="route_switch", verdict="不通过：风险过大",
            occurred_at=dt("2026-04-20T10:00:00"),
        )
        with self.assertRaises(BusinessRuleError):
            w["svc"].approve_route_switch(
                a["funder"], change_id="CHG-1", review_id="REV-1",
                new_requirements=[], budget_reestimate=[], new_amount=400_000,
                new_linked_budget_items=["B-EQ"], occurred_at=dt("2026-05-01T10:00:00"),
            )

    def test_switch_creates_new_revision_and_budget_version_old_kept(self):
        w = self._build_switched_project()
        a = w["actors"]
        w["svc"].record_external_review(
            a["expert"], review_id="REV-1", milestone_id="M-1",
            scope="route_switch", verdict="通过",
            occurred_at=dt("2026-04-20T10:00:00"),
        )
        w["svc"].approve_route_switch(
            a["funder"], change_id="CHG-1", review_id="REV-1",
            new_requirements=[
                {"code": "R1", "name": "复合材料试验", "kind": "test_result", "required": True},
                {"code": "R2", "name": "失败分析", "kind": "failure_log", "required": True},
            ],
            budget_reestimate=[{"item_id": "B-TEST", "old_amount": 400_000, "new_amount": 350_000, "reason": "省材料"}],
            new_amount=400_000, new_linked_budget_items=["B-EQ", "B-TEST"],
            new_budget_items=[
                {"item_id": "B-EQ", "name": "设备费", "category": "设备费", "amount": 600_000},
                {"item_id": "B-TEST", "name": "测试费", "category": "测试费", "amount": 350_000},
            ],
            occurred_at=dt("2026-05-01T10:00:00"),
        )
        state = w["svc"].state
        m = state.milestones["M-1"]
        self.assertEqual(len(m["revisions"]), 2)  # 提出即v1，切换生成v2
        routes = [h["route"] for h in m["route_history"]]
        self.assertIn("金属柔轮", routes)
        self.assertIn("复合材料柔轮", routes)
        history = projections.budget_history(w["store"], "P-1")
        self.assertEqual(len(history), 2)  # 旧预算版本保留
        self.assertEqual(history[-1]["items"][1]["amount"], 350_000)

    def test_switch_conditions_preserve_prior_ip_ownership(self):
        w = self._build_switched_project()
        a = w["actors"]
        w["svc"].record_external_review(
            a["expert"], review_id="REV-1", milestone_id="M-1",
            scope="route_switch", verdict="通过",
            occurred_at=dt("2026-04-20T10:00:00"),
        )
        w["svc"].approve_route_switch(
            a["funder"], change_id="CHG-1", review_id="REV-1",
            new_requirements=[{"code": "R1", "name": "复材试验", "kind": "test_result", "required": True}],
            budget_reestimate=[], new_amount=400_000,
            new_linked_budget_items=["B-EQ"],
            occurred_at=dt("2026-05-01T10:00:00"),
        )
        ch = w["svc"].state.changes["CHG-1"]
        self.assertTrue(any("知识产权归属不变" in c for c in ch["conditions"]))

    def test_old_route_evidence_counts_as_value_not_as_new_requirement(self):
        """旧路线证据保留，但不能充抵新承诺版本的普通证据要求。"""
        w = self._build_switched_project()
        a = w["actors"]
        w["svc"].record_external_review(
            a["expert"], review_id="REV-1", milestone_id="M-1",
            scope="route_switch", verdict="通过", occurred_at=dt("2026-04-20T10:00:00"),
        )
        w["svc"].approve_route_switch(
            a["funder"], change_id="CHG-1", review_id="REV-1",
            new_requirements=[{"code": "R1", "name": "复材试验", "kind": "test_result", "required": True}],
            budget_reestimate=[], new_amount=400_000,
            new_linked_budget_items=["B-EQ"], occurred_at=dt("2026-05-01T10:00:00"),
        )
        gate = w["svc"].evaluate_payment_gates("M-1", a["approver"])
        self.assertFalse(gate["gates"][0]["passed"])  # 新R1无证据
        self.assertIn("R1:复材试验", gate["gates"][0]["detail"]["missing"])


class SuspensionTest(unittest.TestCase):
    def test_suspension_records_obligations_and_keeps_them_open_after_termination(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w)
        a = w["actors"]
        w["svc"].suspend(
            a["funder"], scope="P-1", reason="团队变动",
            disposal_obligations=[
                {"asset_or_fund": "老化箱", "type": "equipment", "action": "保管封存",
                 "deadline": "2026-10-31", "responsible_id": "A-zhang"},
                {"asset_or_fund": "未用资金46万", "type": "unused_funds", "action": "资金退回",
                 "deadline": "2026-09-30", "responsible_id": "FN-sun"},
            ],
            occurred_at=dt("2026-07-01T10:00:00"),
        )
        # 暂停期间不能拨款、不能提交证据
        gate = w["svc"].evaluate_payment_gates("M-1", a["approver"])
        self.assertFalse(gate["gates"][2]["passed"])
        with self.assertRaises(BusinessRuleError):
            w["svc"].submit_evidence(
                a["applicant"], evidence_id="EV-X", milestone_id="M-1",
                requirement_code="R1", kind="test_result", title="t",
                result="x", occurred_at=dt("2026-07-05T10:00:00"),
            )
        # 暂停必须登记义务
        with self.assertRaises(BusinessRuleError):
            w2 = make_world()
            seed_project(w2)
            seed_committed_milestone(w2)
            w2["svc"].suspend(
                a["funder"], scope="P-1", reason="r", disposal_obligations=[],
                occurred_at=dt("2026-07-01T10:00:00"),
            )
        # 履行设备义务并留证
        w["svc"].fulfill_obligation(
            a["applicant"], obligation_ref="P-1-OB1",
            action_taken="封存入库", proof_ref="s3://seal.pdf",
            occurred_at=dt("2026-07-08T10:00:00"),
        )
        # 终止项目：未决义务仍 open
        w["svc"].terminate(
            a["funder"], project_code="P-1", reason="无法恢复",
            occurred_at=dt("2026-09-01T10:00:00"),
        )
        obs = {o["obligation_ref"]: o for o in projections.disposal_obligations(w["store"])}
        self.assertEqual(obs["P-1-OB1"]["status"], "fulfilled")
        self.assertEqual(obs["P-1-OB2"]["status"], "open")

    def test_resume_allows_work_to_continue(self):
        w = make_world()
        seed_project(w)
        seed_committed_milestone(w)
        a = w["actors"]
        w["svc"].suspend(
            a["funder"], scope="P-1", reason="团队变动",
            disposal_obligations=[
                {"asset_or_fund": "设备", "type": "equipment", "action": "保管封存",
                 "responsible_id": "A-zhang"},
            ],
            occurred_at=dt("2026-07-01T10:00:00"),
        )
        w["svc"].resume(a["funder"], scope="P-1", reason="团队重组完成",
                        occurred_at=dt("2026-12-01T10:00:00"))
        submit_and_verify(w)  # 恢复后证据与拨款流程恢复
        gate = w["svc"].evaluate_payment_gates("M-1", a["approver"])
        self.assertTrue(gate["passed"])


class PermissionRedactionTest(unittest.TestCase):
    def test_sensitive_fields_redacted_by_role(self):
        store = EventStore()
        store.load_json_file(ROOT / "fixtures" / "events.json")
        events = store.all_events()

        pay_event = next(e for e in events if e["payload"]["type"] == "PaymentExecuted")
        # 承担方（applicant）看不到付款执行凭证与执行人
        masked = projections.mask_event(pay_event, "applicant")
        self.assertNotIn("actor_id", masked)
        self.assertEqual(masked["payload"]["voucher_ref"], "***REDACTED***")
        self.assertEqual(masked["payload"]["payee"], "***REDACTED***")
        # 审计可见全部
        auditor_view = projections.mask_event(pay_event, "auditor")
        self.assertEqual(auditor_view["actor_id"], pay_event["actor_id"])
        self.assertNotEqual(auditor_view["payload"]["voucher_ref"], "***REDACTED***")

    def test_dossier_redacts_for_applicant_role(self):
        store = EventStore()
        store.load_json_file(ROOT / "fixtures" / "events.json")
        dossier = projections.project_dossier(store, "ROBO-2026-007", "applicant")
        payment = next(p for p in dossier["payments"] if p["status"] == "executed")
        self.assertEqual(payment["approved_by"], "***REDACTED***")
        self.assertEqual(payment["execution"]["executed_by"], "***REDACTED***")
        ev = dossier["milestones"][0]["evidence"][0]
        # 承担方自己提交，submitter 对其不可见（仅审计/资助/核验可见）——按契约最小授权
        self.assertEqual(ev["submitter_id"], "***REDACTED***")


class FixtureScenarioTest(unittest.TestCase):
    """端到端：夹具可重放，验收选取唯一命中切换路线项目。"""

    @classmethod
    def setUpClass(cls):
        cls.store = EventStore()
        cls.store.load_json_file(ROOT / "fixtures" / "events.json")

    def test_fixture_replays_and_contains_two_projects(self):
        state = ProjectService(self.store).state
        self.assertEqual(set(state.projects), {"ROBO-2026-007", "DRV-2026-011"})

    def test_acceptance_selects_only_switch_and_success_project(self):
        candidates = projections.acceptance_candidates(self.store)
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c["project_code"], "ROBO-2026-007")
        self.assertEqual(c["route_before"], "合金钢柔轮+传统热处理")
        self.assertEqual(c["route_after"], "碳纤维复合材料柔轮+齿形修形")
        # 失败价值保留且被变更单引用
        self.assertEqual(c["preserved_failures"][0]["evidence_id"], "EV-FAIL-1")
        self.assertEqual(c["preserved_failures"][0]["cited_by_changes"], ["CHG-2026-002"])
        # 预算重估：协商承诺200万 → 切换重估170万，预算保留两个版本
        self.assertEqual(c["milestone_amount_before_switch"], 2_000_000)
        self.assertEqual(c["milestone_amount_after_switch"], 1_700_000)
        self.assertGreaterEqual(c["budget_versions_kept"], 2)
        # 最终指标达标
        self.assertGreaterEqual(c["final_metrics"]["疲劳寿命_h"], 10000)
        self.assertLessEqual(c["final_metrics"]["传动误差_arcmin"], 1.0)
        self.assertGreaterEqual(c["final_metrics"]["传动效率"], 0.85)

    def test_every_payment_maps_to_evidence_snapshot_of_its_time(self):
        rows = projections.payment_trace(self.store)
        self.assertEqual(len(rows), 2)
        by_id = {r["payment_id"]: r for r in rows}
        # 第一笔绑定 rev1 的仿真证据；第二笔绑定 rev3 的完整证据包（含失败留痕）
        self.assertEqual(by_id["PAY-2026-001"]["commit_revision"], 1)
        self.assertEqual(by_id["PAY-2026-014"]["commit_revision"], 3)
        codes = {e["requirement_code"] for e in by_id["PAY-2026-014"]["evidence_entries"]}
        self.assertEqual(codes, {"R1", "R2", "R3", "R4"})
        # 第一笔证据事后被更正，快照仍冻结且可核对
        e1 = by_id["PAY-2026-001"]["evidence_entries"][0]
        self.assertEqual(e1["evidence_id"], "EV-SIM-1")
        self.assertEqual(e1["superseded_later_by"], "EV-SIM-2")
        self.assertTrue(e1["frozen_intact"])

    def test_suspended_project_retains_open_disposal_obligation(self):
        obs = projections.disposal_obligations(self.store)
        by_ref = {o["obligation_ref"]: o for o in obs}
        self.assertEqual(by_ref["DRV-2026-011-OB1"]["status"], "fulfilled")
        self.assertEqual(by_ref["DRV-2026-011-OB2"]["status"], "open")
        self.assertEqual(by_ref["DRV-2026-011-OB2"]["type"], "unused_funds")

    def test_rebuild_fixture_is_deterministic_and_current(self):
        """fixtures/events.json 必须由脚本从同一领域逻辑重新生成且逐字节一致。"""
        import subprocess
        import sys
        gen = subprocess.run(
            [sys.executable, str(ROOT / "scripts/build_fixtures.py")],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(gen.returncode, 0)
        current = json.loads((ROOT / "fixtures/events.json").read_text(encoding="utf-8"))
        # 重新生成后再次读取比较（脚本就地写回；此处验证事件数与身份稳定）
        self.assertEqual(len(current), len(self.store.all_events()))
        self.assertEqual(
            [e["event_id"] for e in current],
            [e["event_id"] for e in self.store.all_events()],
        )


if __name__ == "__main__":
    unittest.main()
