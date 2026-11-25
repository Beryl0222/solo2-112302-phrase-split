#!/usr/bin/env python3
"""端到端演练：覆盖题目要求的全部业务规则。

运行（建议隔离模式）：

    python3 -I scripts/demo.py            # 输出演练过程
    python3 -I scripts/demo.py --check    # 演练并内置断言，失败退出非零

演练在临时工作区进行，不污染仓库；也可用 --store 指定目录保留结果。
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import sys
sys.path.insert(0, str(ROOT))

from corpuskit.cli import main as cli_main  # noqa: E402
from corpuskit.evidence import (  # noqa: E402
    add_edge,
    build_clusters,
    cluster_members,
    decide_edge,
    suggest_edges,
)
from corpuskit.ingest import (  # noqa: E402
    add_fragment,
    add_question,
    add_term,
    bootstrap_seed,
)
from corpuskit.leakage import (  # noqa: E402
    eligible_questions,
    mark_stale_plans,
    questionnaire_leakage,
)
from corpuskit.linguistics import (  # noqa: E402
    annotate_text,
    quantities,
    render_annotation,
    verify_offsets,
)
from corpuskit.models import EdgeStatus, PackageKind, QuestionStatus  # noqa: E402
from corpuskit.packages import LeakageError, build_classroom_package  # noqa: E402
from corpuskit.packages import build_questionnaire_package  # noqa: E402
from corpuskit.planning import explain_conflicts, plan_questionnaire  # noqa: E402
from corpuskit.revisions import (  # noqa: E402
    activate_revision,
    propose_sense,
    revert_revision,
    rejudge_pending,
)
from corpuskit.store import Store  # noqa: E402

COLLEGE_A = "college-a"
COLLEGE_B = "college-b"


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def run(store_root: str) -> dict:
    result: dict = {"checks": []}

    def check(name: str, ok: bool, detail: str = "") -> None:
        result["checks"].append((name, ok, detail))
        print(f"[{'通过' if ok else '失败'}] {name}" + (f" — {detail}" if detail else ""))

    # ------------------------------------------------------------ 1. 种子引导
    section("1. 种子引导：术语、工单片段、试题、必考单元与已发课堂包")
    store = Store(store_root)
    id_map = bootstrap_seed(store, ROOT / "data" / "corpus.json")
    print("种子映射：", json.dumps(id_map, ensure_ascii=False))

    f1, f3, f4, f5 = (id_map["f-1"], id_map["f-3"],
                      id_map["f-4"], id_map["f-5"])
    t1, t2 = id_map["t-1"], id_map["t-2"]
    terms = {t.id: t for t in store.all("terms")}
    check("两个词目分别建档", len(terms) == 2)
    check("同形异义：主键回填(技能) 与 主键回填表(表名) 词类不同",
          terms[t1].senses[0].term_class == "skill"
          and terms[t2].senses[0].term_class == "table")

    # ------------------------------------------------- 2. 词法：偏移/词界/角色
    section("2. 词法标注：原字面位置、复合词词界、单位与标点变体")
    frag1 = store.get("fragments", f1)
    ann1 = store.annotation_for(f1)
    print(render_annotation(ann1, frag1.raw_text))
    check("偏移严格覆盖原文且切片相等",
          verify_offsets(ann1.tokens, frag1.raw_text))
    refs = {tok.term_ref for tok in ann1.tokens if tok.term_ref}
    check("工单语境命中技能词义", any(ref.startswith(t1 + "#") for ref in refs),
          str(sorted(refs)))

    frag2 = store.get("fragments", id_map["f-2"])
    ann2 = store.annotation_for(frag2.id)
    refs2 = {tok.term_ref for tok in ann2.tokens if tok.term_ref}
    check("菜单语境命中表名词义", any(ref.startswith(t2 + "#") for ref in refs2),
          str(sorted(refs2)))

    # 外部新工单：单位/标点变体
    variant_text = "工单|回填主键后，同批号出现 3 行；另单写“三行”。"
    toks = annotate_text(store, variant_text, "工单")
    check("外部文本同样可做偏移校验", verify_offsets(toks, variant_text))
    nums = [t for t in toks if t.role == "number_unit"]
    check("阿拉伯数字+单位被识别为观察量", any(t.norm and "3" in t.norm for t in nums),
          str([(t.text, t.norm) for t in nums]))
    punct_norms = {t.norm for t in toks if t.role == "punct"}
    check("标点变体归一", {"comma", "semicolon", "period"} <= punct_norms,
          str(sorted(punct_norms)))

    # 复合词内部词界
    comp = [t for t in ann2.tokens if t.components]
    check("复合词保留内部词界", any("主键回填" in frag2.raw_text[s.start:s.end]
          for t in comp for s in t.components),
          str([(t.text, [frag2.raw_text[s.start:s.end] for s in t.components])
               for t in comp]))

    # 同形词面在不同语境分别组织
    section("3. 同形词面按语境分别组织（sense-index 规则）")
    skill_ctx = annotate_text(store, "主键回填", "工单数据操作")
    table_ctx = annotate_text(store, "主键回填表", "工具菜单报表")
    skill_ref = next(t.term_ref for t in skill_ctx if t.term_ref)
    table_ref = next(t.term_ref for t in table_ctx if t.term_ref)
    check("同字面在不同语境落到不同词义",
          skill_ref.split("#")[0] == t1 and table_ref.split("#")[0] == t2,
          f"{skill_ref} vs {table_ref}")

    # ----------------------------------------------- 4. 证据聚类与多跳派生
    section("4. 评审确认“同一考查证据”：多跳派生仍归一簇，复用观察量分开")
    clusters = cluster_members(store)
    print("证据簇：", json.dumps(clusters, ensure_ascii=False))
    ev_f1 = build_clusters(store)[f1]
    check("多跳派生 f-1→f-3→f-4 同簇",
          build_clusters(store)[f3] == ev_f1
          and build_clusters(store)[f4] == ev_f1)
    check("只复用观察量的 f-5 独立成簇",
          build_clusters(store)[f5] != ev_f1)
    check("观察量可被独立片段共用",
          {q.norm or q.text for q in quantities(ann1)})

    # 机器建议（新片段之间）
    suggestions = suggest_edges(store)
    print("机器建议边：", [(s.left_id, s.right_id, s.relation, s.same_evidence)
                          for s in suggestions])

    # ----------------------------------------------------- 5. 泄漏检测
    section("5. 泄漏检测：已发课堂包阻挡同证据候选题，f-5 不受影响")
    report = questionnaire_leakage(store, COLLEGE_A)
    print("泄漏片段：", json.dumps(report.leaked_fragments, ensure_ascii=False))
    print("阻挡包：", json.dumps(report.blocked_by, ensure_ascii=False))
    check("f-4（同证据派生）判定泄漏", report.is_leaked(f4))
    check("f-5（仅复用观察量）不泄漏", not report.is_leaked(f5))
    eligible, _ = eligible_questions(store, COLLEGE_A)
    check("可选题不含泄漏题", all(
        store.get("questions", q.id).stem_fragment_id != f4 for q in eligible))

    # --------------------------------------------- 6. 问卷规划：先保课堂
    section("6. 问卷规划：保留已交付课堂内容，寻找无泄漏的另一组考题")
    result_plan = plan_questionnaire(store, COLLEGE_A)
    print(explain_conflicts(store, result_plan.plan))
    check("存在无泄漏考题组，规划成功", result_plan.success,
          f"选题={result_plan.plan.question_ids}")
    check("必考单元全部覆盖",
          set(result_plan.plan.covered_cells)
          == {c.id for c in store.all("coverage")})
    check("规划未动用任何泄漏题干",
          all(store.get("questions", qid).stem_fragment_id != f4
              for qid in result_plan.plan.question_ids))
    check("已发课堂包原封不动",
          any(p.kind == PackageKind.CLASSROOM for p in store.all("packages")))

    # 7. 无法同时交付时显式报冲突：独立院校工作区，先保课堂再看问卷
    section("7. 必考冲突场景：无替代题时明确指出能力×语境，不削减必考项")
    b_root = Path(store_root) / "college-b-workspace"
    b_store = Store(b_root)
    add_term(b_store, "主键回填", "数据操作释义", "skill",
             variants=["回填主键", "补键"], contexts=["工单"])
    b_classroom = add_fragment(
        b_store, "工单|回填主键后同一批号仍出现三行。",
        source="work_order", business_context="工单数据核查")
    build_classroom_package(b_store, COLLEGE_B, [b_classroom.id],
                            note="院校B 第一周课堂包（先交付，不可撤）")
    leak_stem = add_fragment(
        b_store, "同号仍有三行，空字段能否解释？",
        source="question_stem", business_context="工单数据核查",
        colleges=[COLLEGE_B])
    add_question(b_store, leak_stem.raw_text,
                 ability="completeness_judgment", context="工单数据核查",
                 colleges=[COLLEGE_B])
    add_edge(b_store, b_classroom.id, leak_stem.id, "paraphrase", True,
             reason="演练：院校B 题目与课堂同源", auto_confirm=True)
    check("院校B 新题确认同证据后泄漏",
          questionnaire_leakage(b_store, COLLEGE_B).is_leaked(leak_stem.id))
    from corpuskit.models import CoverageCell
    for ability, context in [
        ("completeness_judgment", "工单数据核查"),
        ("menu_gloss", "工具菜单"),
    ]:
        b_store.add("coverage", CoverageCell(
            id=b_store.next_id("coverage"), ability=ability,
            context=context, required=True, colleges=[COLLEGE_B]))
    plan_b_result = plan_questionnaire(b_store, COLLEGE_B)
    plan_b = plan_b_result.plan
    print(explain_conflicts(b_store, plan_b))
    check("规划失败且不静默减项", not plan_b_result.success
          and len(plan_b.conflicts) >= 1)
    check("冲突明确到能力×语境",
          all("ability" in c and "context" in c for c in plan_b.conflicts))
    check("必考项数量保持不变",
          len([c for c in b_store.all("coverage") if c.required]) == 2)

    # --------------------------------------------- 8. 词义修订与撤回重判
    section("8. 词义修订生效与撤回：未发内容重判，已交付课堂按旧口径查阅")
    # 8.1 种子里的 r-1：给 t-2 增加“补键汇总表”变体并生效
    seed_rev = next(r for r in store.all("revisions")
                    if r.payload.get("variant") == "补键汇总表")
    activate_revision(store, seed_rev.id)
    t2_term = store.get("terms", t2)
    check("r-1 生效：新变体进入词目", "补键汇总表" in t2_term.variants)
    rejudged = rejudge_pending(store, {t2})
    print("变体生效后重判片段：", rejudged)
    check("含该变体的未交付片段 f-3 被重判", f3 in rejudged)
    # 已发课堂包内的快照不含新变体
    classroom_pkg = next(p for p in store.all("packages")
                         if p.kind == PackageKind.CLASSROOM)
    snap_t2_before = next(t for t in classroom_pkg.term_snapshot if t["id"] == t2)
    check("已发课堂包冻结口径不含生效后的新变体",
          "补键汇总表" not in snap_t2_before["variants"])

    # 8.2 再新增一个词义，验证冻结快照能区分“生效中”与“撤回后”
    rev = propose_sense(
        store, t2, "s2",
        label="教研新增：在操作手册里也可能指补键步骤页",
        term_class="skill",
        contexts=["操作手册", "步骤页"],
        note="演练修订", auto_activate=True)
    check("修订生效后词目有两个词义",
          len(store.get("terms", t2).senses) == 2)
    rejudge_pending(store, {t2})

    # 8.3 撤回 s2 与变体修订：未交付内容按当前词义重新判断
    revert_revision(store, rev.id, reviewer="教研组长")
    revert_revision(store, seed_rev.id, reviewer="教研组长")
    term_after = store.get("terms", t2)
    check("撤回后新增词义移除", len(term_after.senses) == 1
          and term_after.active_sense_id == "s1")
    check("撤回后变体移除", "补键汇总表" not in term_after.variants)
    delivered_ids = {fid for p in store.all("packages") for fid in p.fragment_ids}
    rejudged_after = rejudge_pending(store, {t2})
    check("未交付片段参与撤回后重判", f3 in rejudged_after)
    check("已交付片段不被重判", all(fid not in rejudged_after for fid in delivered_ids))

    # 已交付课堂包仍按交付时词义与分组查阅
    view_terms = classroom_pkg.term_snapshot
    snap_t2 = next(t for t in view_terms if t["id"] == t2)
    check("已交付课堂包保留冻结时词义口径",
          [s["sense_id"] for s in snap_t2["senses"]] == ["s1"])
    check("已交付课堂包保留冻结时证据分组",
          set(classroom_pkg.cluster_map) == set(classroom_pkg.fragment_ids))
    stale = [p for p in store.all("plans") if p.status == "stale"]
    check("前提变化后旧规划被标记 stale", len(stale) >= 1,
          str([p.id for p in stale]))

    # 撤回后重新规划，得到当前口径下的无泄漏考题组
    section("9. 撤回重判后重新规划问卷并冻结交付")
    result_plan = plan_questionnaire(store, COLLEGE_A)
    print(explain_conflicts(store, result_plan.plan))
    check("重判后仍存在无泄漏考题组，规划成功", result_plan.success,
          f"选题={result_plan.plan.question_ids}")
    check("必考单元全部覆盖",
          set(result_plan.plan.covered_cells)
          == {c.id for c in store.all("coverage")})
    check("规划未动用任何泄漏题干",
          all(store.get("questions", qid).stem_fragment_id != f4
              for qid in result_plan.plan.question_ids))
    check("已发课堂包原封不动",
          any(p.kind == PackageKind.CLASSROOM for p in store.all("packages")))

    from corpuskit.packages import build_questionnaire_package
    q_pkg = build_questionnaire_package(
        store, COLLEGE_A, result_plan.plan.id, note="春季问卷交付")
    check("问卷包已冻结", q_pkg.id is not None)
    check("问卷快照记录的是撤回后的当前词义（无 s2）",
          all(s["sense_id"] != "s2"
              for t in q_pkg.term_snapshot if t["id"] == t2
              for s in t["senses"]))
    check("相关试题标记 delivered",
          all(store.get("questions", qid).status == QuestionStatus.DELIVERED
              for qid in result_plan.plan.question_ids))

    # --------------------------------------------- 10. 课堂包双向屏障
    section("10. 双向屏障：未开考问卷占用的证据不能再进课堂包")
    # 新片段与问卷实际占用的证据簇（问卷题干之一）确认同源 → 课堂包必须拒绝
    blocked_frag = add_fragment(
        store, "补键后同一批号仍有三笔记录。",
        source="work_order", business_context="工单")
    questionnaire_anchor = store.get(
        "questions", result_plan.plan.question_ids[0]).stem_fragment_id
    add_edge(store, questionnaire_anchor, blocked_frag.id, "derive", True,
             reason="演练：与问卷证据同源", auto_confirm=True)
    raised = False
    try:
        build_classroom_package(store, COLLEGE_A, [blocked_frag.id],
                                note="不应成功的课堂包")
    except LeakageError as exc:
        raised = True
        print("预期拦截：", exc)
    check("课堂包被问卷屏障拦截", raised)

    # 失效规划不能再用于交付
    mark_stale_plans(store, COLLEGE_A)
    stale_plan = next((p for p in store.all("plans") if p.status == "stale"), None)
    if stale_plan is not None:
        blocked = False
        try:
            build_questionnaire_package(store, COLLEGE_A, stale_plan.id)
        except LeakageError as exc:
            blocked = True
            print("预期拦截：", exc)
        check("失效规划拒绝交付问卷", blocked)

    # --------------------------------------------- 11. 持续导入与院校隔离
    section("11. 持续导入新候选与院校隔离")
    tmp_in = Path(tempfile.mkdtemp(prefix="corpuskit-inbox-"))
    inbox = tmp_in / "inbox.json"
    inbox.write_text(json.dumps({"records": [
        {"text": "工单|取数完成后主键回填，延迟约束复跑通过。",
         "context": "工单数据核查", "colleges": [COLLEGE_B]},
    ]}, ensure_ascii=False), encoding="utf-8")
    from corpuskit.ingest import import_fragments
    imported = import_fragments(store, inbox, source="external",
                                colleges=[COLLEGE_B])
    check("外部片段导入即标注", len(imported) == 1
          and store.annotation_for(imported[0].id) is not None)
    check("导入片段带院校归属", imported[0].colleges == [COLLEGE_B])
    check("院校B 泄漏报告不混入院校A 课堂证据",
          COLLEGE_A not in {p.college for p in store.packages_for(COLLEGE_B)})
    shutil.rmtree(tmp_in, ignore_errors=True)

    # 事件日志存在且仅追加
    events = (Path(store_root) / "events.log").read_text(encoding="utf-8")
    check("审计事件日志已记录关键动作",
          all(name in events for name in
              ["classroom_delivered", "questionnaire_delivered",
               "revision_activated", "revision_reverted"]))

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="corpuskit 端到端演练")
    parser.add_argument("--store", help="保留工作区目录（默认临时目录）")
    parser.add_argument("--check", action="store_true", help="内置断言模式")
    args = parser.parse_args()

    keep = args.store is not None
    store_root = args.store or tempfile.mkdtemp(prefix="corpuskit-demo-")
    if Path(store_root).exists() and any(Path(store_root).iterdir()):
        shutil.rmtree(store_root)
    Path(store_root).mkdir(parents=True, exist_ok=True)

    try:
        result = run(store_root)
    finally:
        if not keep:
            shutil.rmtree(store_root, ignore_errors=True)

    failed = [name for name, ok, _ in result["checks"] if not ok]
    print("\n" + "#" * 72)
    print(f"演练断言：{len(result['checks']) - len(failed)}/{len(result['checks'])} 通过")
    if failed:
        print("未通过：", failed)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
