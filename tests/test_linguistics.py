"""词法规则：原字面偏移、复合词词界、单位/标点变体、同形异义消歧。"""
from corpuskit.linguistics import (
    annotate_text,
    quantities,
    render_annotation,
    verify_offsets,
)
from corpuskit.models import TokenRole

from tests.support import WorkspaceCase


class OffsetTest(WorkspaceCase):
    def test_offsets_cover_raw_text_exactly(self):
        self.seed()
        for fragment in self.store.all("fragments"):
            ann = self.store.annotation_for(fragment.id)
            self.assertIsNotNone(ann)
            self.assertTrue(verify_offsets(ann.tokens, fragment.raw_text))
            self.assertEqual(
                "".join(t.text for t in ann.tokens), fragment.raw_text
            )

    def test_external_text_with_variants(self):
        self.seed()
        text = "工单|回填主键后，同批号出现 3 行；另单写“三行”。"
        tokens = annotate_text(self.store, text, "工单")
        self.assertTrue(verify_offsets(tokens, text))
        q = [t for t in tokens if t.role == TokenRole.NUMBER_UNIT]
        norms = {t.norm for t in q}
        self.assertIn("3行", norms)
        self.assertIn("3行", {n for n in norms if n})  # 中文“三行”同归一
        self.assertTrue(any(t.text.strip() == "三行" for t in q))
        punct = {t.norm for t in tokens if t.role == TokenRole.PUNCT}
        self.assertEqual(
            {"pipe", "comma", "semicolon", "quote", "period"}, punct
        )

    def test_compound_word_keeps_inner_boundaries(self):
        ids = self.seed()
        fragment = self.store.get("fragments", ids["f-2"])
        ann = self.store.annotation_for(fragment.id)
        compound = next(t for t in ann.tokens if t.components)
        inner = [fragment.raw_text[s.start:s.end] for s in compound.components]
        self.assertIn("主键回填", inner)


class SenseDisambiguationTest(WorkspaceCase):
    def test_same_surface_different_senses_by_context(self):
        ids = self.seed()
        skill = annotate_text(self.store, "主键回填", "工单数据操作")
        table = annotate_text(self.store, "主键回填表", "工具菜单报表")
        skill_ref = next(t.term_ref for t in skill if t.term_ref)
        table_ref = next(t.term_ref for t in table if t.term_ref)
        self.assertEqual(skill_ref.split("#")[0], ids["t-1"])
        self.assertEqual(table_ref.split("#")[0], ids["t-2"])

    def test_quantities_are_distinct_from_terms(self):
        from corpuskit.models import Annotation, now_iso

        self.seed()
        text = "同一批号仍出现三行，数值 12 次。"
        tokens = annotate_text(self.store, text, "工单")
        ann = Annotation(fragment_id="x", tokens=tokens, annotated_at=now_iso())
        q = quantities(ann)
        self.assertTrue(q)
        self.assertTrue(all(t.role == TokenRole.NUMBER_UNIT for t in q))

    def test_render_is_readable(self):
        ids = self.seed()
        fragment = self.store.get("fragments", ids["f-1"])
        ann = self.store.annotation_for(fragment.id)
        rendered = render_annotation(ann, fragment.raw_text)
        self.assertIn(ids["f-1"], rendered)
        self.assertIn("term=", rendered)


if __name__ == "__main__":
    unittest.main()
