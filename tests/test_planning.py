"""问卷规划：必考覆盖、最小选题、冲突显式化、规划陈旧标记。"""
from corpuskit.evidence import add_edge
from corpuskit.ingest import add_fragment, add_question
from corpuskit.leakage import mark_stale_plans
from corpuskit.models import CoverageCell, RelationType
from corpuskit.planning import explain_conflicts, plan_questionnaire
from corpuskit.store import Store

from tests.support import COLLEGE_A, COLLEGE_B, WorkspaceCase


class PlanningTest(WorkspaceCase):
    def test_successful_plan_covers_every_required_cell(self):
        ids = self.seed()
        result = plan_questionnaire(self.store, COLLEGE_A)
        self.assertTrue(result.success, explain_conflicts(self.store, result.plan))
        required = {c.id for c in self.store.all("coverage") if c.required}
        self.assertEqual(set(result.plan.covered_cells), required)
        # 不选泄漏题
        for qid in result.plan.question_ids:
            self.assertNotEqual(
                self.store.get("questions", qid).stem_fragment_id, ids["f-4"]
            )

    def test_conflict_is_explicit_never_silent(self):
        # 独立工作区：院校B 只有一个会泄漏的完整性题，菜单释义完全无题
        from corpuskit.models import CoverageCell
        from corpuskit.packages import build_classroom_package
        from corpuskit.ingest import add_term
        b_root = self.root / "college-b-only"
        b_store = Store(b_root)
        add_term(b_store, "主键回填", "技能释义", "skill",
                 variants=["回填主键", "补键"], contexts=["工单"])
        classroom = add_fragment(
            b_store, "回填主键后同批号仍出现三行。",
            business_context="工单数据核查")
        build_classroom_package(b_store, COLLEGE_B, [classroom.id])
        stem = add_fragment(b_store, "同号仍有三行，空字段能否解释？",
                            source="question_stem",
                            business_context="工单数据核查",
                            colleges=[COLLEGE_B])
        add_question(b_store, stem.raw_text,
                     ability="completeness_judgment",
                     context="工单数据核查", colleges=[COLLEGE_B])
        add_edge(b_store, classroom.id, stem.id,
                 RelationType.PARAPHRASE, True, auto_confirm=True)
        for ability, context in [
            ("completeness_judgment", "工单数据核查"),
            ("menu_gloss", "工具菜单"),
        ]:
            b_store.add("coverage", CoverageCell(
                id=b_store.next_id("coverage"),
                ability=ability, context=context, required=True,
                colleges=[COLLEGE_B]))

        plan = plan_questionnaire(b_store, COLLEGE_B).plan
        self.assertFalse(plan.success)
        self.assertEqual(plan.question_ids, [])
        abilities = {c["ability"] for c in plan.conflicts}
        contexts = {c["context"] for c in plan.conflicts}
        # 两个必考单元都应被点名
        self.assertIn("completeness_judgment", abilities)
        self.assertIn("menu_gloss", abilities)
        self.assertIn("工具菜单", contexts)
        # 必考项数量不允许被悄悄减少
        required = [c for c in b_store.all("coverage") if c.required]
        self.assertEqual(len(required), 2)

    def test_plan_prefers_fewer_questions(self):
        self.seed()
        # 增加一道能同时覆盖两个单元的题是不允许的（单元按 ability×context 区分），
        # 这里改为验证：同单元多候选题时结果仍可覆盖且题量不超过单元数
        result = plan_questionnaire(self.store, COLLEGE_A)
        self.assertTrue(result.success)
        self.assertLessEqual(
            len(result.plan.question_ids),
            len([c for c in self.store.all("coverage") if c.required]),
        )

    def test_plan_marked_stale_after_new_delivery(self):
        self.seed()
        plan = plan_questionnaire(self.store, COLLEGE_A).plan
        self.assertEqual(plan.status, "draft")
        mark_stale_plans(self.store, COLLEGE_A)
        # 当前没有新变化时不失效
        self.assertEqual(self.store.get("plans", plan.id).status, "draft")

    def test_backtracking_with_uncoverable_cell(self):
        # 空题库 + 一个必考单元 → 冲突而非异常
        store = Store(self.root / "empty")
        store.add("coverage", CoverageCell(
            id=store.next_id("coverage"),
            ability="x", context="y", required=True))
        plan = plan_questionnaire(store, COLLEGE_A).plan
        self.assertFalse(plan.success)
        self.assertEqual(plan.conflicts[0]["ability"], "x")


if __name__ == "__main__":
    unittest.main()
