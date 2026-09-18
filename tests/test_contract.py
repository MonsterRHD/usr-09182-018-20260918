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

if __name__ == "__main__":
    unittest.main()
