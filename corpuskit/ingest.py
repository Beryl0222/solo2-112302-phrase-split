"""语料导入：种子引导与持续外部导入。

``bootstrap_seed`` 读取项目自带的合成记录（data/corpus.json），把文字记录
映射为可运算实体；``import_fragments`` 供教研把新加入的术语候选、工单片段、
试题修订持续导入工作区。导入的外部文件是不受信数据，调用方应以绝对路径传参
并以 ``python -I`` 运行（见 README）。
"""
from __future__ import annotations

import json
from pathlib import Path

from .evidence import add_edge
from .linguistics import build_annotation
from .models import (
    CoverageCell,
    DeliveryPackage,
    Fragment,
    PackageKind,
    Question,
    QuestionStatus,
    RelationType,
    RevisionStatus,
    Sense,
    SenseRevision,
    Term,
    now_iso,
)


def _split_pipe(text: str) -> tuple[str, str]:
    if "|" in text:
        head, body = text.split("|", 1)
        return head.strip(), body.strip()
    return "", text.strip()


def _new_fragment(store, raw_text: str, source: str, business_context: str,
                  note: str = "", colleges: list[str] | None = None) -> Fragment:
    fragment = Fragment(
        id=store.next_id("fragments"),
        raw_text=raw_text,
        source=source,
        business_context=business_context,
        colleges=colleges or [],
        note=note,
        created_at=now_iso(),
    )
    store.add("fragments", fragment, event="fragment_added")
    build_annotation(store, fragment)
    return fragment


def add_fragment(store, raw_text: str, source: str = "work_order",
                 business_context: str = "", note: str = "",
                 colleges: list[str] | None = None) -> Fragment:
    """对外入口：新增片段并立即标注，保留原字面位置。"""
    return _new_fragment(store, raw_text, source, business_context, note, colleges)


def add_question(store, stem_text: str, ability: str, context: str,
                 business_context: str = "", rubric: str = "",
                 colleges: list[str] | None = None,
                 note: str = "",
                 stem_fragment_id: str | None = None) -> tuple[Question, Fragment]:
    """新增试题：题干先落成独立片段（或复用同文片段），再挂题面属性。"""
    if stem_fragment_id is not None:
        fragment = store.get("fragments", stem_fragment_id)
    else:
        same = store.find(
            "fragments",
            lambda f: f.raw_text == stem_text
            and (not colleges or set(f.colleges) <= set(colleges)),
        )
        if same:
            fragment = same[0]
        else:
            fragment = _new_fragment(
                store, stem_text, source="question_stem",
                business_context=business_context or context, note=note,
                colleges=colleges,
            )
    question = Question(
        id=store.next_id("questions"),
        stem_fragment_id=fragment.id,
        ability=ability,
        context=context,
        rubric=rubric,
        colleges=colleges or [],
        status=QuestionStatus.CANDIDATE,
        created_at=now_iso(),
    )
    store.add("questions", question, event="question_added")
    return question, fragment


def add_term(store, headword: str, sense_label: str, term_class: str,
             variants: list[str] | None = None,
             contexts: list[str] | None = None, role: str | None = None) -> Term:
    term = Term(
        id=store.next_id("terms"),
        headword=headword,
        variants=variants or [],
        senses=[Sense(sense_id="s1", label=sense_label, term_class=term_class,
                      contexts=contexts or [], role=role)],
        active_sense_id="s1",
        created_at=now_iso(),
    )
    store.add("terms", term, event="term_added")
    return term


# ------------------------------------------------------------- 种子引导
def bootstrap_seed(store, seed_path: str | Path) -> dict[str, str]:
    """把 data/corpus.json 的十条记录映射为实体，返回种子ID -> 新ID 对照。"""
    if store.all("terms") or store.all("fragments"):
        raise RuntimeError("工作区非空，禁止重复引导")
    seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    id_map: dict[str, str] = {}
    fragments_by_seed: dict[str, Fragment] = {}

    for record in seed["records"]:
        kind, seed_id, text = record["kind"], record["id"], record["text"]

        if kind == "term":
            head, body = _split_pipe(text)
            if seed_id == "t-1":
                term = add_term(
                    store, headword="主键回填",
                    sense_label="数据操作：插入后补齐自增主键",
                    term_class="skill",
                    variants=["回填主键", "补键"],
                    contexts=["工单", "补齐主键", "操作"],
                    role="操作技能",
                )
            else:
                term = add_term(
                    store, headword="主键回填表",
                    sense_label="工具菜单中的缺值中间表（表名）",
                    term_class="table",
                    # “补键汇总表”是 r-1 提案中的新增变体，提案生效前不入词目
                    variants=[],
                    contexts=["菜单", "报表", "工具", "表头"],
                    role="报表/表名",
                )
            id_map[seed_id] = term.id

        elif kind == "fragment":
            head, body = _split_pipe(text)
            fragment = _new_fragment(
                store, body,
                source="question_stem" if "试题" in head else "work_order",
                business_context=head,
                note=text,
            )
            id_map[seed_id] = fragment.id
            fragments_by_seed[seed_id] = fragment

        elif kind == "coverage":
            head, _ = _split_pipe(text)
            cells = [
                ("completeness_judgment", "补键后的完整性判断", "工单数据核查"),
                ("menu_gloss", "菜单报表释义", "工具菜单"),
            ]
            for ability, label, context in cells:
                cell = CoverageCell(
                    id=store.next_id("coverage"),
                    ability=ability,
                    context=context,
                    required=True,
                )
                store.add("coverage", cell, event="coverage_added")
            id_map[seed_id] = head

        elif kind == "delivery":
            # 已发课堂包：按当时口径直接冻结（f-1、f-2）
            f1 = fragments_by_seed["f-1"]
            f2 = fragments_by_seed["f-2"]
            ids = [f1.id, f2.id]
            from .evidence import build_clusters
            cluster_map = {
                fid: cid for fid, cid in build_clusters(store).items()
                if fid in ids
            }
            pkg = DeliveryPackage(
                id=store.next_id("packages"),
                kind=PackageKind.CLASSROOM,
                college="college-a",
                fragment_ids=ids,
                question_ids=[],
                term_snapshot=[t.to_dict() for t in store.all("terms")],
                annotation_snapshot=[
                    store.annotation_for(fid).to_dict() for fid in ids
                ],
                cluster_map=cluster_map,
                plan_id=None,
                note=text,
                created_at=now_iso(),
            )
            store.add("packages", pkg, event="classroom_delivered")
            id_map[seed_id] = pkg.id

        elif kind == "revision":
            head, body = _split_pipe(text)
            t2 = store.get("terms", id_map["t-2"])
            revision = SenseRevision(
                id=store.next_id("revisions"),
                term_id=t2.id,
                sense_id=t2.active_sense_id,
                kind="add_variant",
                payload={"variant": "补键汇总表"},
                # 种子叙事里这是“教研提案”，引导时保持待生效
                status=RevisionStatus.PROPOSED,
                note=body,
                created_at=now_iso(),
            )
            store.add("revisions", revision, event="revision_proposed")
            id_map[seed_id] = revision.id

    # 试题（种子 f-4、f-5 是题干预写）
    f4 = fragments_by_seed["f-4"]
    q_leak = Question(
        id=store.next_id("questions"),
        stem_fragment_id=f4.id,
        ability="completeness_judgment",
        context="工单数据核查",
        rubric="需结合 f-3/f-1 同一批号三行的观察作答",
        status=QuestionStatus.CANDIDATE,
        created_at=now_iso(),
    )
    store.add("questions", q_leak, event="question_added")

    f2 = fragments_by_seed["f-2"]
    # 菜单释义候选题使用独立证据的新题干（f-2 原文已在课堂包，不能直接复用）
    menu_stem = _new_fragment(
        store,
        "菜单说明|在工具菜单中打开缺值中间表，其别名“主键回填表”指什么？",
        source="question_stem",
        business_context="工具菜单",
        note="菜单释义候选题，独立于已发课堂证据",
    )
    q_menu = Question(
        id=store.next_id("questions"),
        stem_fragment_id=menu_stem.id,
        ability="menu_gloss",
        context="工具菜单",
        rubric="识别主键回填表为缺值中间表，无需读 f-2 中的核查时间观察",
        status=QuestionStatus.CANDIDATE,
        created_at=now_iso(),
    )
    store.add("questions", q_menu, event="question_added")
    # 与课堂片段只共用业务名词，明确不是同一证据
    add_edge(store, f2.id, menu_stem.id, RelationType.REUSE_OBS, False,
             reason="只共用菜单/表名业务名词，考查问法与观察均不同",
             reviewer="seed", auto_confirm=True)

    f5 = fragments_by_seed["f-5"]
    q_clean = Question(
        id=store.next_id("questions"),
        stem_fragment_id=f5.id,
        ability="completeness_judgment",
        context="工单数据核查",
        rubric="取数未完成时推迟完整性约束校验，无需读 f-1 观察",
        status=QuestionStatus.CANDIDATE,
        created_at=now_iso(),
    )
    store.add("questions", q_clean, event="question_added")

    # 证据边：多跳派生链 f-1 -> f-3 -> f-4，与仅复用观察量的 f-5
    f1 = fragments_by_seed["f-1"]
    f3 = fragments_by_seed["f-3"]
    add_edge(store, f1.id, f3.id, RelationType.SPLIT, True,
             reason="派生练习拼接 f-1、f-2 两个旧工单片段",
             reviewer="seed", auto_confirm=True)
    add_edge(store, f3.id, f4.id, RelationType.PARAPHRASE, True,
             reason="同义改写，源题 f-3 前句证据进入训练材料",
             reviewer="seed", auto_confirm=True)
    add_edge(store, f1.id, f5.id, RelationType.REUSE_OBS, False,
             reason="只共用补键业务名词，无需读同一观察即可作答",
             reviewer="seed", auto_confirm=True)

    return id_map


# ------------------------------------------------------------- 外部导入
def import_fragments(store, path: str | Path, source: str = "external",
                     colleges: list[str] | None = None) -> list[Fragment]:
    """从外部 JSON 导入新片段。

    期望格式：``{"records": [{"text": ..., "context": ...}, ...]}``。
    文件按不受信数据处理：只用绝对路径传参，以 python -I 运行。
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    created: list[Fragment] = []
    for record in payload.get("records", []):
        fragment = _new_fragment(
            store,
            raw_text=record["text"],
            source=source,
            business_context=record.get("context", ""),
            note=record.get("note", ""),
            colleges=record.get("colleges", colleges),
        )
        created.append(fragment)
    return created
