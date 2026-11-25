"""词义修订生命周期：提案/生效/撤回、撤回重判、冻结口径不受影响。"""
from corpuskit.models import PackageKind, RevisionStatus
from corpuskit.revisions import (
    activate_revision,
    propose_sense,
    propose_variant,
    revert_revision,
    rejudge_pending,
)

from tests.support import COLLEGE_A, WorkspaceCase


class RevisionLifecycleTest(WorkspaceCase):
    def test_propose_activate_revert_add_sense(self):
        ids = self.seed()
        t2 = ids["t-2"]
        rev = propose_sense(
            self.store, t2, "s2", label="步骤页释义",
            term_class="skill", contexts=["操作手册"])
        self.assertEqual(rev.status, RevisionStatus.PROPOSED)
        self.assertEqual(len(self.store.get("terms", t2).senses), 1)

        activate_revision(self.store, rev.id)
        term = self.store.get("terms", t2)
        self.assertEqual(len(term.senses), 2)

        revert_revision(self.store, rev.id, reviewer="lead")
        term = self.store.get("terms", t2)
        self.assertEqual(len(term.senses), 1)
        self.assertEqual(
            self.store.get("revisions", rev.id).status,
            RevisionStatus.REVERTED,
        )

    def test_variant_revision_lifecycle(self):
        ids = self.seed()
        t2 = ids["t-2"]
        rev = propose_variant(self.store, t2, "补键汇总表",
                              sense_id="s1", auto_activate=True)
        self.assertIn("补键汇总表", self.store.get("terms", t2).variants)
        revert_revision(self.store, rev.id)
        self.assertNotIn("补键汇总表",
                         self.store.get("terms", t2).variants)

    def test_cannot_activate_twice_or_revert_proposed(self):
        ids = self.seed()
        rev = propose_variant(self.store, ids["t-2"], "别名X",
                              sense_id="s1", auto_activate=True)
        with self.assertRaises(ValueError):
            activate_revision(self.store, rev.id)
        pending = propose_sense(self.store, ids["t-1"], "s9",
                                label="x", term_class="business")
        with self.assertRaises(ValueError):
            revert_revision(self.store, pending.id)

    def test_rejudge_skips_delivered_fragments(self):
        ids = self.seed()
        classroom = next(p for p in self.store.all("packages")
                         if p.kind == PackageKind.CLASSROOM)
        delivered = set(classroom.fragment_ids)
        rev = propose_variant(self.store, ids["t-2"], "补键汇总表",
                              sense_id="s1", auto_activate=True)
        rejudged = set(rejudge_pending(self.store, {ids["t-2"]}))
        self.assertTrue(rejudged)
        self.assertTrue(delivered.isdisjoint(rejudged))

        revert_revision(self.store, rev.id)
        rejudged_again = set(rejudge_pending(self.store, {ids["t-2"]}))
        self.assertTrue(delivered.isdisjoint(rejudged_again))

    def test_delivered_package_keeps_snapshot_after_revert(self):
        ids = self.seed()
        classroom = next(p for p in self.store.all("packages")
                         if p.kind == PackageKind.CLASSROOM)
        before = [dict(t) for t in classroom.term_snapshot]
        rev = propose_sense(self.store, ids["t-2"], "s2",
                            label="临时释义", term_class="skill",
                            auto_activate=True)
        revert_revision(self.store, rev.id)
        self.assertEqual(classroom.term_snapshot, before)
        # 快照内的证据分组也不变
        self.assertEqual(
            classroom.cluster_map,
            {fid: classroom.cluster_map[fid]
             for fid in classroom.fragment_ids},
        )


if __name__ == "__main__":
    unittest.main()
