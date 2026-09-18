import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ContractTest(unittest.TestCase):
    def test_example_event_matches_contract(self):
        contract = json.loads((ROOT / "contracts/domain.json").read_text(encoding="utf-8"))
        events = json.loads((ROOT / "fixtures/events.json").read_text(encoding="utf-8"))
        self.assertTrue(contract["project"])
        self.assertEqual(set(contract["event_contract"]), set(events[0]))
        self.assertEqual(events[0]["version"], 1)

    def test_event_catalog_payload_types_are_documented(self):
        contract = json.loads((ROOT / "contracts/domain.json").read_text(encoding="utf-8"))
        events = json.loads((ROOT / "fixtures/events.json").read_text(encoding="utf-8"))
        catalog = set(contract["event_catalog"])
        used = {e["payload"]["type"] for e in events}
        self.assertTrue(used.issubset(catalog), f"未登记的事件类型: {used - catalog}")
        # 夹具应覆盖关键治理事件
        for required in [
            "TrialFailed",
            "ExternalReviewRecorded",
            "RouteSwitchRequested",
            "RouteSwitchApproved",
            "PaymentApproved",
            "PaymentExecuted",
            "EvidenceCorrected",
            "MilestoneSuspended",
            "DisposalObligationFulfilled",
            "AcceptanceApproved",
        ]:
            self.assertIn(required, used)

    def test_four_payment_gates_declared(self):
        contract = json.loads((ROOT / "contracts/domain.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(contract["payment_gates"]),
            {"gate_evidence_complete", "gate_separation_of_duties", "gate_state", "gate_budget"},
        )


if __name__ == "__main__":
    unittest.main()
