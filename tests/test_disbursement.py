import unittest

from helpers import boot_project, prepare_disbursable
from milestone_funding import DomainError, EvidenceIncomplete, PermissionDenied, StateError


class DisbursementGateTest(unittest.TestCase):
    def setUp(self):
        self.svc, self.clock = boot_project()

    def test_request_rejected_when_evidence_incomplete(self):
        with self.assertRaises(EvidenceIncomplete):
            self.svc.request_disbursement(
                "P-TEST", occurred_at=self.clock.tick(),
                disbursement_id="D-1", milestone_id="M1", amount=100, requester="李工",
            )

    def _request(self, amount=100, requester="李工"):
        prepare_disbursable(self.svc, self.clock)
        self.svc.request_disbursement(
            "P-TEST", occurred_at=self.clock.tick(),
            disbursement_id="D-1", milestone_id="M1", amount=amount, requester=requester,
        )

    def test_requester_cannot_approve_own_request(self):
        self._request()
        with self.assertRaises(PermissionDenied):
            self.svc.approve_disbursement(
                "P-TEST", occurred_at=self.clock.tick(),
                disbursement_id="D-1", role="技术评审", actor="李工",
            )

    def test_same_person_cannot_hold_both_approval_roles(self):
        self._request()
        self.svc.approve_disbursement(
            "P-TEST", occurred_at=self.clock.tick(),
            disbursement_id="D-1", role="技术评审", actor="王评",
        )
        with self.assertRaises(PermissionDenied):
            self.svc.approve_disbursement(
                "P-TEST", occurred_at=self.clock.tick(),
                disbursement_id="D-1", role="财务审批", actor="王评",
            )

    def test_execute_requires_dual_approval(self):
        self._request()
        self.svc.approve_disbursement(
            "P-TEST", occurred_at=self.clock.tick(),
            disbursement_id="D-1", role="技术评审", actor="王评",
        )
        with self.assertRaises(StateError):
            self.svc.execute_disbursement("P-TEST", occurred_at=self.clock.tick(), disbursement_id="D-1")

    def test_amount_capped_by_milestone_budget(self):
        with self.assertRaises(DomainError):
            self._request(amount=500)  # M1 预算 400

    def test_full_flow_snapshots_evidence_approved_at_that_time(self):
        self._request()
        self.svc.approve_disbursement(
            "P-TEST", occurred_at=self.clock.tick(),
            disbursement_id="D-1", role="技术评审", actor="王评",
        )
        self.svc.approve_disbursement(
            "P-TEST", occurred_at=self.clock.tick(),
            disbursement_id="D-1", role="财务审批", actor="赵财",
        )
        self.svc.execute_disbursement("P-TEST", occurred_at=self.clock.tick(), disbursement_id="D-1")

        d = self.svc.state("P-TEST").disbursements["D-1"]
        self.assertEqual(d.status, "已执行")
        self.assertEqual(d.evidence_refs, [{"evidence_id": "EV-1", "kind": "设计文档", "version": 1}])

        # 拨付后证据被更正：原版本留痕，拨款单仍指向当时批准的版本
        self.svc.correct_evidence(
            "P-TEST", occurred_at=self.clock.tick(),
            evidence_id="EV-1", new_summary="方案设计文档 v2（补签章）", reason="补盖公章",
        )
        ev = self.svc.state("P-TEST").evidences["EV-1"]
        self.assertEqual(ev.version, 2)
        self.assertEqual(ev.history[0]["summary"], "方案设计文档 v1")
        self.assertEqual(d.evidence_refs[0]["version"], 1)


class SuspensionTest(unittest.TestCase):
    def test_suspension_blocks_disbursement_and_keeps_obligations(self):
        svc, clock = boot_project()
        prepare_disbursable(svc, clock)
        svc.request_disbursement(
            "P-TEST", occurred_at=clock.tick(),
            disbursement_id="D-1", milestone_id="M1", amount=100, requester="李工",
        )
        svc.approve_disbursement("P-TEST", occurred_at=clock.tick(), disbursement_id="D-1", role="技术评审", actor="王评")
        svc.approve_disbursement("P-TEST", occurred_at=clock.tick(), disbursement_id="D-1", role="财务审批", actor="赵财")
        svc.execute_disbursement("P-TEST", occurred_at=clock.tick(), disbursement_id="D-1")

        svc.suspend_project("P-TEST", occurred_at=clock.tick(), reason="关键外购件断供")
        state = svc.state("P-TEST")
        self.assertEqual(state.status, "暂停")

        kinds = {o.kind: o for o in state.obligations.values()}
        self.assertEqual(set(kinds), {"设备处置", "未用资金"})
        self.assertEqual(kinds["未用资金"].amount, 300)  # 承诺 400 - 已拨 100

        with self.assertRaises(StateError):
            svc.request_disbursement(
                "P-TEST", occurred_at=clock.tick(),
                disbursement_id="D-2", milestone_id="M1", amount=50, requester="李工",
            )

        for oid in list(state.obligations):
            svc.settle_obligation("P-TEST", occurred_at=clock.tick(), obligation_id=oid, note="已按协议处置")
        self.assertTrue(all(o.status == "已履行" for o in svc.state("P-TEST").obligations.values()))


if __name__ == "__main__":
    unittest.main()
