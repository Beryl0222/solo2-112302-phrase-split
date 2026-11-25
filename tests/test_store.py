"""存储层：ID 发放、往返序列化、原子文件、审计日志与重复保护。"""
import json

from corpuskit.ingest import add_fragment
from corpuskit.models import Fragment, Sense, Term, now_iso
from corpuskit.store import COLLECTIONS, Store

from tests.support import WorkspaceCase


class StoreRoundTripTest(WorkspaceCase):
    def test_collections_round_trip_through_json(self):
        self.seed()
        reopened = Store(self.root)
        for name, cls in COLLECTIONS.items():
            before = self.store.all(name)
            after = reopened.all(name)
            self.assertEqual(len(before), len(after), name)
            self.assertEqual(
                [x.to_dict() for x in before],
                [x.to_dict() for x in after],
                name,
            )

    def test_ids_are_unique_and_monotonic(self):
        self.seed()
        ids = [add_fragment(self.store, f"片段{i}", business_context="x").id
               for i in range(3)]
        nums = [int(x.split("-")[1]) for x in ids]
        self.assertEqual(nums, sorted(nums))
        self.assertEqual(len(nums), len(set(nums)))

    def test_duplicate_add_rejected(self):
        term = Term(id="t-999", headword="x",
                    senses=[Sense(sense_id="s1", label="l",
                                  term_class="business")],
                    active_sense_id="s1", created_at=now_iso())
        self.store.add("terms", term)
        with self.assertRaises(ValueError):
            self.store.add("terms", term)

    def test_event_log_is_appended(self):
        self.seed()
        log = (self.root / "events.log").read_text(encoding="utf-8")
        events = [json.loads(line) for line in log.splitlines() if line]
        names = {e["event"] for e in events}
        self.assertIn("classroom_delivered", names)
        for event in events:
            self.assertIn("at", event)

    def test_files_are_valid_utf8_json(self):
        self.seed()
        for path in self.root.glob("*.json"):
            with path.open(encoding="utf-8") as fh:
                json.load(fh)

    def test_missing_entity_raises_keyerror(self):
        with self.assertRaises(KeyError):
            self.store.get("fragments", "f-404")


if __name__ == "__main__":
    unittest.main()
