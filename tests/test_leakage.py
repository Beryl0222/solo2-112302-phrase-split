"""泄漏检测：双向屏障、院校隔离、只复用名词/数值不泄漏。"""
from corpuskit.evidence import add_edge
from corpuskit.ingest import add_fragment, add_question
from corpuskit.leakage import (
    classroom_blockers,
    eligible_questions,
    questionnaire_leakage,
)
from corpuskit.models import PackageKind, RelationType

from tests.support import COLLEGE_A, COLLEGE_B, WorkspaceCase


class LeakageTest(WorkspaceCase):
    def test_seed_questionnaire_leakage(self):
        ids = self.seed()
        report = questionnaire_leakage(self.store, COLLEGE_A)
        self.assertTrue(report.is_leaked(ids["f-4"]))
        self.assertFalse(report.is_leaked(ids["f-5"]))
        # 阻挡来源必须指向已发课堂包
        blockers = report.blocked_by[ids["f-4"]]
        self.assertTrue(blockers)
        for pid in blockers:
            self.assertEqual(
                self.store.get("packages", pid).kind, PackageKind.CLASSROOM
            )

    def test_eligible_questions_exclude_leaked_only(self):
        ids = self.seed()
        eligible, report = eligible_questions(self.store, COLLEGE_A)
        stems = {q.stem_fragment_id for q in eligible}
        self.assertNotIn(ids["f-4"], stems)
        self.assertIn(ids["f-5"], stems)
        # 独立菜单题干同样可选
        self.assertTrue(any(q.ability == "menu_gloss" for q in eligible))

    def test_classroom_package_blocked_by_delivered_questionnaire(self):
        ids = self.seed()
        from corpuskit.planning import plan_questionnaire
        from corpuskit.packages import build_questionnaire_package

        plan = plan_questionnaire(self.store, COLLEGE_A).plan
        self.assertTrue(plan.success)
        q_pkg = build_questionnaire_package(self.store, COLLEGE_A, plan.id)

        new_frag = add_fragment(self.store, "补键后同批号仍有三笔。",
                                business_context="工单")
        # 锚到问卷实际占用的证据簇（问卷题干之一），而非课堂侧的 f-1
        anchor = self.store.get(
            "questions", plan.question_ids[0]).stem_fragment_id
        add_edge(self.store, anchor, new_frag.id,
                 RelationType.DERIVE, True, auto_confirm=True)
        blockers = classroom_blockers(
            self.store, COLLEGE_A, [new_frag.id])
        self.assertIn(new_frag.id, blockers)
        self.assertTrue(all(
            self.store.get("packages", pid).kind == PackageKind.QUESTIONNAIRE
            for pid in blockers[new_frag.id]
        ))
        self.assertEqual(blockers[new_frag.id], [q_pkg.id])

    def test_colleges_are_isolated(self):
        ids = self.seed()
        # 院校B 没有任何课堂包，不应被院校A 的交付阻挡
        report_b = questionnaire_leakage(self.store, COLLEGE_B)
        self.assertEqual(report_b.leaked_ids, [])

        frag_b = add_fragment(
            self.store, "院校B 专用工单：复跑后再校验约束。",
            business_context="工单", colleges=[COLLEGE_B])
        add_question(self.store, frag_b.raw_text,
                     ability="completeness_judgment",
                     context="工单数据核查", colleges=[COLLEGE_B])
        eligible_b, _ = eligible_questions(self.store, COLLEGE_B)
        self.assertIn(frag_b.id, [q.stem_fragment_id for q in eligible_b])


if __name__ == "__main__":
    unittest.main()
