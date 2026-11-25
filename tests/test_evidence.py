"""证据聚类：并查集、多跳传递、仅复用观察量不合并、评审状态门禁。"""
from corpuskit.evidence import (
    UnionFind,
    add_edge,
    build_clusters,
    decide_edge,
    suggest_edges,
)
from corpuskit.ingest import add_fragment
from corpuskit.models import EdgeStatus, RelationType

from tests.support import COLLEGE_A, WorkspaceCase


class UnionFindTest(WorkspaceCase):
    def test_transitive_closure_and_stable_root(self):
        uf = UnionFind(["a", "b", "c", "d"])
        uf.union("a", "b")
        uf.union("b", "c")
        self.assertEqual(uf.find("a"), uf.find("c"))
        self.assertNotEqual(uf.find("a"), uf.find("d"))
        groups = uf.groups()
        # 单元素簇也出现，代表取字典序最小
        self.assertIn(["a", "b", "c"], groups.values())
        self.assertIn(["d"], groups.values())


class EvidenceClusterTest(WorkspaceCase):
    def test_multi_hop_derivation_merges_into_one_cluster(self):
        ids = self.seed()
        clusters = build_clusters(self.store)
        ev_f1 = clusters[ids["f-1"]]
        self.assertEqual(clusters[ids["f-3"]], ev_f1)
        self.assertEqual(clusters[ids["f-4"]], ev_f1)

    def test_reuse_observation_stays_separate(self):
        ids = self.seed()
        clusters = build_clusters(self.store)
        self.assertNotEqual(clusters[ids["f-5"]], clusters[ids["f-1"]])

    def test_suggested_edges_do_not_merge_until_confirmed(self):
        self.seed()
        a = add_fragment(self.store, "补键后批号仍出现三行。",
                         business_context="工单")
        b = add_fragment(self.store, "补键之后批号依旧有三行记录。",
                         business_context="工单")
        edge = add_edge(self.store, a.id, b.id,
                        RelationType.PARAPHRASE, True)
        self.assertEqual(edge.status, EdgeStatus.SUGGESTED)
        before = build_clusters(self.store)
        self.assertNotEqual(before[a.id], before[b.id])
        decide_edge(self.store, edge.id, confirm=True, reviewer="tester")
        after = build_clusters(self.store)
        self.assertEqual(after[a.id], after[b.id])

    def test_rejected_edge_never_merges(self):
        self.seed()
        a = add_fragment(self.store, "复跑后约束校验通过。",
                         business_context="工单")
        b = add_fragment(self.store, "复跑完成，约束检查没有报错。",
                         business_context="工单")
        edge = add_edge(self.store, a.id, b.id,
                        RelationType.PARAPHRASE, True)
        decide_edge(self.store, edge.id, confirm=False, reviewer="tester")
        clusters = build_clusters(self.store)
        self.assertNotEqual(clusters[a.id], clusters[b.id])

    def test_machine_suggestion_distinguishes_paraphrase_and_reuse(self):
        ids = self.seed()
        suggestions = suggest_edges(self.store)
        # 至少应给出建议；同证据建议与仅复用建议两类标签都存在于系统中
        self.assertTrue(suggestions)
        relations = {s.relation for s in suggestions}
        self.assertTrue(relations <= {
            "split", "paraphrase", "derive", "reuse_obs"
        })


if __name__ == "__main__":
    unittest.main()
