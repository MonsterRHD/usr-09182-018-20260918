import unittest

from app.event_store import EventStore
from app.errors import DuplicateEventError, InvalidEventError, VersionConflictError


def base_event(**overrides):
    e = {
        "event_id": "e1",
        "occurred_at": "2026-09-18T08:00:00+08:00",
        "entity_id": "entity-018",
        "version": 1,
        "actor_id": "A-1",
        "payload": {"type": "ProjectRegistered", "state": "已登记"},
    }
    e.update(overrides)
    return e


class EventStoreTest(unittest.TestCase):
    def test_append_valid_event(self):
        store = EventStore()
        stored = store.append(base_event())
        self.assertEqual(stored["payload"]["state"], "已登记")

    def test_same_event_takes_effect_once(self):
        store = EventStore()
        store.append(base_event())
        with self.assertRaises(DuplicateEventError):
            store.append(base_event())

    def test_version_must_be_strictly_increasing_and_contiguous(self):
        store = EventStore()
        store.append(base_event(event_id="e1"))
        with self.assertRaises(VersionConflictError):
            store.append(base_event(event_id="e2", version=3))  # 跳过 2
        store.append(base_event(event_id="e3", version=2))
        with self.assertRaises(VersionConflictError):
            store.append(base_event(event_id="e4", version=2))  # 倒退

    def test_occurred_at_must_carry_timezone(self):
        store = EventStore()
        with self.assertRaises(InvalidEventError):
            store.append(base_event(event_id="e-naive", occurred_at="2026-09-18T08:00:00"))

    def test_payload_must_have_type(self):
        store = EventStore()
        with self.assertRaises(InvalidEventError):
            store.append(base_event(event_id="e2", payload={"state": "x"}))

    def test_appended_events_are_immutable_to_callers(self):
        store = EventStore()
        e = base_event()
        store.append(e)
        e["payload"]["state"] = "被外部篡改"
        self.assertEqual(store.all_events()[0]["payload"]["state"], "已登记")

    def test_version_must_be_positive_integer(self):
        store = EventStore()
        with self.assertRaises(InvalidEventError):
            store.append(base_event(event_id="e2", version=0))
        with self.assertRaises(InvalidEventError):
            store.append(base_event(event_id="e3", version=True))


if __name__ == "__main__":
    unittest.main()
