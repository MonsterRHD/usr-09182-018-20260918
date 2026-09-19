import unittest

from milestone_funding import (
    ContractViolation,
    Event,
    EventStore,
    IdempotencyViolation,
    VersionConflict,
)


def make_event(eid="E-1", version=1, entity="P-1", payload=None):
    return Event(
        event_id=eid,
        occurred_at="2026-09-18T08:00:00+08:00",
        entity_id=entity,
        version=version,
        payload=payload or {"type": "项目登记"},
    )


class EventStoreTest(unittest.TestCase):
    def test_same_event_takes_effect_only_once(self):
        store = EventStore()
        store.append(make_event())
        with self.assertRaises(IdempotencyViolation):
            store.append(make_event())
        self.assertEqual(len(store), 1)

    def test_versions_must_increase_per_entity(self):
        store = EventStore()
        store.append(make_event(eid="E-1", version=1))
        with self.assertRaises(VersionConflict):
            store.append(make_event(eid="E-2", version=3))

    def test_correction_appends_without_overwriting(self):
        store = EventStore()
        store.append(make_event(eid="E-1", version=1, payload={"type": "证据提交", "summary": "原始数据"}))
        store.append(make_event(eid="E-2", version=2, payload={"type": "证据更正", "corrects": "E-1"}))
        events = store.events_for("P-1")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].payload["summary"], "原始数据")  # 原始事实仍在

    def test_contract_requires_timezone(self):
        with self.assertRaises(ContractViolation):
            Event(
                event_id="E-9",
                occurred_at="2026-09-18T08:00:00",
                entity_id="P-1",
                version=1,
                payload={"type": "项目登记"},
            )

    def test_contract_requires_exact_keys(self):
        raw = {
            "event_id": "E-10",
            "occurred_at": "2026-09-18T08:00:00+08:00",
            "entity_id": "P-1",
            "payload": {"type": "项目登记"},
        }
        with self.assertRaises(ContractViolation):
            Event.from_dict(raw)


if __name__ == "__main__":
    unittest.main()
