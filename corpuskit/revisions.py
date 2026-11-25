"""词义修订生命周期与撤回重判。

- 修订（新增词义/改释义线索/加变体）先提案、再生效，生效时改写词目；
- 撤回修订时回滚词目到修订前状态，然后对**尚未交付**的片段重新标注，
  并把机器建议边（suggested）重新生成；
- 已交付包内的词义快照与证据分组不动，仍可按当时口径查阅；
- 已确认的人工证据边代表评审事实，不因词义撤回而失效。
"""
from __future__ import annotations

from .evidence import add_suggested_edges
from .leakage import mark_stale_plans
from .linguistics import build_annotation
from .models import (
    QuestionStatus,
    RevisionStatus,
    Sense,
    SenseRevision,
    now_iso,
)


# ------------------------------------------------------------- 提案与生效
def propose_sense(store, term_id: str, sense_id: str, label: str,
                  term_class: str, contexts: list[str] | None = None,
                  role: str | None = None, note: str = "",
                  auto_activate: bool = False) -> SenseRevision:
    term = store.get("terms", term_id)
    if any(s.sense_id == sense_id for s in term.senses):
        raise ValueError(f"{term_id} 已存在词义 {sense_id}")
    revision = SenseRevision(
        id=store.next_id("revisions"),
        term_id=term_id,
        sense_id=sense_id,
        kind="add_sense",
        payload={
            "sense": {
                "sense_id": sense_id, "label": label,
                "term_class": term_class, "contexts": contexts or [],
                "role": role,
            }
        },
        note=note,
        created_at=now_iso(),
    )
    store.add("revisions", revision, event="revision_proposed")
    if auto_activate:
        activate_revision(store, revision.id)
    return revision


def propose_variant(store, term_id: str, variant: str,
                    sense_id: str | None = None, note: str = "",
                    auto_activate: bool = False) -> SenseRevision:
    term = store.get("terms", term_id)
    target_sense = sense_id or term.active_sense_id
    if target_sense is None:
        raise ValueError(f"{term_id} 尚无词义，无法挂接变体")
    term.sense(target_sense)  # 校验存在
    revision = SenseRevision(
        id=store.next_id("revisions"),
        term_id=term_id,
        sense_id=target_sense,
        kind="add_variant",
        payload={"variant": variant},
        note=note,
        created_at=now_iso(),
    )
    store.add("revisions", revision, event="revision_proposed")
    if auto_activate:
        activate_revision(store, revision.id)
    return revision


def activate_revision(store, revision_id: str) -> SenseRevision:
    revision = store.get("revisions", revision_id)
    if revision.status != RevisionStatus.PROPOSED:
        raise ValueError(f"修订 {revision_id} 状态为 {revision.status}，不能生效")
    term = store.get("terms", revision.term_id)

    if revision.kind == "add_sense":
        data = revision.payload["sense"]
        term.senses.append(Sense(**data))
        if term.active_sense_id is None:
            term.active_sense_id = data["sense_id"]
    elif revision.kind == "add_variant":
        variant = revision.payload["variant"]
        if variant not in term.variants:
            term.variants.append(variant)
    else:
        raise ValueError(f"未知修订类型 {revision.kind}")

    revision.status = RevisionStatus.ACTIVE
    store.upsert("terms", term)
    store.upsert("revisions", revision)
    store.log_event("revision_activated",
                    {"id": revision.id, "term_id": term.id})
    return revision


# ------------------------------------------------------------- 撤回与重判
def revert_revision(store, revision_id: str,
                    reviewer: str = "") -> SenseRevision:
    revision = store.get("revisions", revision_id)
    if revision.status != RevisionStatus.ACTIVE:
        raise ValueError(f"仅生效中的修订可撤回，当前 {revision.status}")
    term = store.get("terms", revision.term_id)

    if revision.kind == "add_sense":
        before = len(term.senses)
        term.senses = [s for s in term.senses if s.sense_id != revision.sense_id]
        if len(term.senses) == before:
            raise ValueError("回滚失败：未找到对应词义")
        if term.active_sense_id == revision.sense_id:
            term.active_sense_id = term.senses[0].sense_id if term.senses else None
    elif revision.kind == "add_variant":
        variant = revision.payload["variant"]
        term.variants = [v for v in term.variants if v != variant]

    revision.status = RevisionStatus.REVERTED
    revision.reverted_at = now_iso()
    store.upsert("terms", term)
    store.upsert("revisions", revision)
    store.log_event("revision_reverted",
                    {"id": revision.id, "term_id": term.id,
                     "reviewer": reviewer})

    rejudged = rejudge_pending(store, {revision.term_id})
    store.log_event("pending_rejudged",
                    {"revision_id": revision.id, "fragments": rejudged})
    return revision


def _delivered_fragment_ids(store) -> set[str]:
    out: set[str] = set()
    for pkg in store.all("packages"):
        out.update(pkg.fragment_ids)
    return out


def _delivered_question_ids(store) -> set[str]:
    return {
        q.id for q in store.all("questions")
        if q.status == QuestionStatus.DELIVERED
    }


def rejudge_pending(store, term_ids: set[str] | None = None) -> list[str]:
    """按当前词目重新标注未交付片段，并刷新机器建议与规划陈旧标记。

    已交付片段的标注保留（其冻结快照在交付包内），不受重判影响。
    判断“受影响”时同时考虑：

    - 当前词目的词面（生效中的变体/词头）；
    - 已撤回修订曾经引入的词面（撤回后该词面已不在词目中，
      但含它的未交付片段仍需按当前口径重判）。
    """
    delivered = _delivered_fragment_ids(store)
    surfaces: set[str] = set()
    for term in store.all("terms"):
        if term_ids is None or term.id in term_ids:
            surfaces.update(term.all_surfaces())
    # 已撤回修订涉及的词面同样视为受影响线索
    for revision in store.all("revisions"):
        if revision.status != RevisionStatus.REVERTED:
            continue
        if term_ids is not None and revision.term_id not in term_ids:
            continue
        if revision.kind == "add_variant":
            surfaces.add(revision.payload["variant"])
        else:
            surfaces.add(revision.sense_id)

    rejudged: list[str] = []
    for fragment in store.all("fragments"):
        if fragment.id in delivered:
            continue
        ann = store.annotation_for(fragment.id)
        if ann is None:
            continue
        touched = term_ids is None or any(
            (tok.term_ref or "").split("#")[0] in term_ids
            for tok in ann.tokens
        ) or any(surface in fragment.raw_text for surface in surfaces)
        if touched:
            build_annotation(store, fragment, revision_basis=None)
            rejudged.append(fragment.id)

    # 机器建议边重算：删除现存 suggested，重新生成；confirmed/rejected 不动
    suggested = store.find("edges", lambda e: e.status == "suggested")
    if suggested:
        store.replace_all(
            "edges",
            [e for e in store.all("edges") if e.status != "suggested"],
        )
    add_suggested_edges(store)

    colleges = {pkg.college for pkg in store.all("packages")}
    for question in store.all("questions"):
        colleges.update(question.colleges)
    # 只要发生了重判，既有 draft 规划所依据的标注口径就已变化，
    # 统一标记 stale，要求重新规划；泄漏类失效仍由 mark_stale_plans 兜底
    if rejudged:
        for plan in store.find("plans", lambda p: p.status == "draft"):
            plan.status = "stale"
            store.upsert("plans", plan)
        store.log_event("plans_marked_stale",
                        {"reason": "rejudge", "fragments": rejudged})
    for college in sorted(colleges):
        mark_stale_plans(store, college)
    return sorted(set(rejudged))
