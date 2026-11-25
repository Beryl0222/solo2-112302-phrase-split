"""交付包：冻结快照、屏障拒绝、历史查阅、状态机与规划失效门禁。"""
from corpuskit.evidence import add_edge
from corpuskit.ingest import add_fragment
from corpuskit.leakage import mark_stale_plans
from corpuskit.models import PackageKind, QuestionStatus, RelationType
from corpuskit.packages import (
    FrozenView,
    LeakageError,
    build_classroom_package,
    build_questionnaire_package,
    list_packages,
    view_package,
)
from corpuskit.planning import plan_questionnaire

from tests.support import COLLEGE_A, WorkspaceCase


class PackageTest(WorkspaceCase):
    def test_seed_classroom_package_is_frozen_snapshot(self):
        ids = self.seed()
        pkg = next(p for p in self.store.all("packages")
                   if p.kind == PackageKind.CLASSROOM)
        self.assertEqual(set(pkg.fragment_ids), {ids["f-1"], ids["f-2"]})
        self.assertTrue(pkg.term_snapshot)
        self.assertTrue(pkg.annotation_snapshot)
        self.assertEqual(set(pkg.cluster_map), set(pkg.fragment_ids))
        view = view_package(self.store, pkg.id)
        self.assertIsInstance(view, FrozenView)
        self.assertIsNotNone(view.annotation_of(ids["f-1"]))

    def test_questionnaire_delivery_marks_questions_delivered(self):
        self.seed()
        plan = plan_questionnaire(self.store, COLLEGE_A).plan
        pkg = build_questionnaire_package(self.store, COLLEGE_A, plan.id)
        self.assertEqual(pkg.kind, PackageKind.QUESTIONNAIRE)
        for qid in plan.question_ids:
            self.assertEqual(
                self.store.get("questions", qid).status,
                QuestionStatus.DELIVERED,
            )
        self.assertEqual(pkg.plan_id, plan.id)

    def test_questionnaire_re_checked_at_delivery_time(self):
        self.seed()
        plan = plan_questionnaire(self.store, COLLEGE_A).plan
        # 规划之后产生新变化：把一个新片段与规划题干确认同证据，
        # 再让该片段通过新课堂包先行交付 → 冻结问卷时必须二次拦下
        extra = add_fragment(self.store, "补键后同批号仍有三行。",
                             business_context="工单")
        target_stem = self.store.get(
            "questions", plan.question_ids[0]).stem_fragment_id
        add_edge(self.store, target_stem, extra.id,
                 RelationType.DERIVE, True, auto_confirm=True)
        # extra 与未交付题干同簇，不与既有课堂包冲突，课堂包可以冻结
        build_classroom_package(self.store, COLLEGE_A, [extra.id],
                                note="规划后新增课堂内容")
        with self.assertRaises(LeakageError):
            build_questionnaire_package(self.store, COLLEGE_A, plan.id)
        # 原规划应被标记 stale，需重新规划而非强行交付
        self.assertEqual(self.store.get("plans", plan.id).status, "stale")

    def test_stale_plan_cannot_be_delivered(self):
        self.seed()
        plan = plan_questionnaire(self.store, COLLEGE_A).plan
        mark_stale_plans(self.store, COLLEGE_A)
        # 人为置 stale 模拟后续变化
        plan.status = "stale"
        self.store.upsert("plans", plan)
        with self.assertRaises(LeakageError):
            build_questionnaire_package(self.store, COLLEGE_A, plan.id)

    def test_classroom_barrier_blocks_questionnaire_evidence(self):
        self.seed()
        plan = plan_questionnaire(self.store, COLLEGE_A).plan
        build_questionnaire_package(self.store, COLLEGE_A, plan.id)
        extra = add_fragment(self.store, "与问卷同源的新课堂片段。",
                             business_context="工单")
        anchor = self.store.get(
            "questions", plan.question_ids[0]).stem_fragment_id
        add_edge(self.store, anchor, extra.id,
                 RelationType.DERIVE, True, auto_confirm=True)
        with self.assertRaises(LeakageError):
            build_classroom_package(self.store, COLLEGE_A, [extra.id])

    def test_list_packages_filters(self):
        self.seed()
        plan = plan_questionnaire(self.store, COLLEGE_A).plan
        build_questionnaire_package(self.store, COLLEGE_A, plan.id)
        self.assertEqual(
            len(list_packages(self.store, COLLEGE_A, PackageKind.CLASSROOM)), 1
        )
        self.assertEqual(
            len(list_packages(self.store, COLLEGE_A, PackageKind.QUESTIONNAIRE)),
            1,
        )


if __name__ == "__main__":
    unittest.main()
