"""corpuskit 命令行入口。

通用参数：``--store PATH`` 指定工作区目录（默认 ./corpus_store）。
所有列表/报告类命令支持 ``--json`` 输出机器可读结果。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .store import Store


# ---------------------------------------------------------------- 输出工具
def _emit(data, human: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print(human)


def _get_store(args) -> Store:
    return Store(args.store)


# ---------------------------------------------------------------- 子命令
def cmd_init(args) -> None:
    store = Store(args.store)
    if args.seed:
        from .ingest import bootstrap_seed
        id_map = bootstrap_seed(store, Path(args.seed))
        _emit(id_map, f"已从种子引导工作区 {args.store}，实体映射：\n"
              + "\n".join(f"  {k} -> {v}" for k, v in id_map.items()), args.json)
    else:
        _emit({"store": args.store}, f"已初始化空工作区 {args.store}", args.json)


def cmd_term_add(args) -> None:
    from .ingest import add_term
    store = _get_store(args)
    term = add_term(store, args.headword, args.label, args.term_class,
                    variants=args.variant or [], contexts=args.context or [],
                    role=args.role)
    _emit(term.to_dict(), f"已建词目 {term.id}：{term.headword}（{args.term_class}）",
          args.json)


def cmd_term_list(args) -> None:
    store = _get_store(args)
    terms = store.all("terms")
    human = []
    for term in terms:
        human.append(f"{term.id}  {term.headword}  变体={term.variants}")
        for sense in term.senses:
            active = "（当前）" if sense.sense_id == term.active_sense_id else ""
            human.append(f"    {sense.sense_id}{active} [{sense.term_class}] "
                         f"{sense.label}  线索={sense.contexts}")
    _emit([t.to_dict() for t in terms], "\n".join(human) or "（无词目）", args.json)


def cmd_sense_index(args) -> None:
    """展示每个词面在不同语境下落到哪个词义：同形异义分别组织。"""
    from .linguistics import annotate_text
    store = _get_store(args)
    rows = []
    for surface in args.surface:
        per_context = {}
        for ctx in args.context or ["", "工单", "工具菜单"]:
            tokens = annotate_text(store, surface, ctx)
            refs = [t.term_ref for t in tokens if t.term_ref]
            per_context[ctx or "（无语境）"] = refs
        rows.append({"surface": surface, "by_context": per_context})
    human = []
    for row in rows:
        human.append(f"词面「{row['surface']}」：")
        for ctx, refs in row["by_context"].items():
            human.append(f"  语境 {ctx} -> {refs or '未命中'}")
    _emit(rows, "\n".join(human), args.json)


def cmd_revision_propose(args) -> None:
    from .revisions import propose_sense, propose_variant
    store = _get_store(args)
    if args.kind == "add_sense":
        rev = propose_sense(store, args.term, args.sense_id, args.label,
                            args.term_class, contexts=args.context or [],
                            role=args.role, note=args.note,
                            auto_activate=args.activate)
    else:
        rev = propose_variant(store, args.term, args.variant,
                              sense_id=args.sense_id, note=args.note,
                              auto_activate=args.activate)
    _emit(rev.__dict__, f"已登记修订 {rev.id}（{rev.status}）", args.json)


def cmd_revision_activate(args) -> None:
    from .revisions import activate_revision
    store = _get_store(args)
    rev = activate_revision(store, args.revision)
    _emit(rev.__dict__, f"修订 {rev.id} 已生效，词目已更新", args.json)


def cmd_revision_revert(args) -> None:
    from .revisions import revert_revision
    store = _get_store(args)
    rev = revert_revision(store, args.revision, reviewer=args.reviewer)
    _emit(rev.__dict__,
          f"修订 {rev.id} 已撤回；未交付内容已按当前词义重判，已交付包不变",
          args.json)


def cmd_revision_list(args) -> None:
    store = _get_store(args)
    revs = store.all("revisions")
    human = [f"{r.id}  term={r.term_id}  {r.kind}  {r.status}  {r.note}"
             for r in revs]
    _emit([r.__dict__ for r in revs], "\n".join(human) or "（无修订）", args.json)


def cmd_fragment_add(args) -> None:
    from .ingest import add_fragment
    store = _get_store(args)
    fragment = add_fragment(store, args.text, source=args.source,
                            business_context=args.context, note=args.note,
                            colleges=args.college)
    _emit(fragment.to_dict(), f"已新增片段 {fragment.id} 并完成标注", args.json)


def cmd_fragment_import(args) -> None:
    from .ingest import import_fragments
    store = _get_store(args)
    fragments = import_fragments(store, args.path, source=args.source,
                                 colleges=args.college)
    ids = [f.id for f in fragments]
    _emit(ids, f"已导入并标注 {len(ids)} 条片段：{ids}", args.json)


def cmd_fragment_show(args) -> None:
    from .linguistics import render_annotation
    store = _get_store(args)
    fragment = store.get("fragments", args.fragment)
    ann = store.annotation_for(fragment.id)
    if ann is None:
        raise SystemExit(f"片段 {args.fragment} 尚无标注")
    _emit(ann.to_dict(), render_annotation(ann, fragment.raw_text), args.json)


def cmd_fragment_list(args) -> None:
    store = _get_store(args)
    fragments = store.all("fragments")
    human = [f"{f.id}  [{f.source}/{f.business_context}]  {f.raw_text}"
             for f in fragments]
    _emit([f.to_dict() for f in fragments],
          "\n".join(human) or "（无片段）", args.json)


def cmd_question_add(args) -> None:
    from .ingest import add_question
    store = _get_store(args)
    question, fragment = add_question(
        store, args.stem, args.ability, args.context,
        rubric=args.rubric, colleges=args.college, note=args.note)
    _emit({"question": question.to_dict(), "fragment": fragment.id},
          f"已新增试题 {question.id}（题干片段 {fragment.id}）", args.json)


def cmd_question_list(args) -> None:
    store = _get_store(args)
    questions = store.all("questions")
    human = [f"{q.id}  {q.status:<9} 能力={q.ability} 语境={q.context}  "
             f"题干={q.stem_fragment_id}  院校={q.colleges or '全部'}"
             for q in questions]
    _emit([q.to_dict() for q in questions],
          "\n".join(human) or "（无试题）", args.json)


def cmd_edge_suggest(args) -> None:
    from .evidence import add_suggested_edges
    store = _get_store(args)
    edges = add_suggested_edges(store, threshold=args.threshold)
    human = [f"{e.id}  {e.left_id}~{e.right_id}  {e.relation} "
             f"same_evidence={e.same_evidence}  {e.reason}" for e in edges]
    _emit([e.to_dict() for e in edges],
          "\n".join(human) or "（无新建议）", args.json)


def cmd_edge_add(args) -> None:
    from .evidence import add_edge
    store = _get_store(args)
    same = args.relation != "reuse_obs" if args.same is None else args.same
    edge = add_edge(store, args.left, args.right, args.relation, same,
                    reason=args.reason, reviewer=args.reviewer,
                    colleges=args.college, auto_confirm=args.confirm)
    _emit(edge.to_dict(), f"已登记证据边 {edge.id}（{edge.status}）", args.json)


def cmd_edge_decide(args) -> None:
    from .evidence import decide_edge
    store = _get_store(args)
    edge = decide_edge(store, args.edge, args.decision == "confirm",
                       reviewer=args.reviewer)
    _emit(edge.to_dict(),
          f"证据边 {edge.id} 评审结果：{edge.status}，"
          f"同一证据={edge.same_evidence}", args.json)


def cmd_edge_list(args) -> None:
    store = _get_store(args)
    edges = store.all("edges")
    human = [f"{e.id}  {e.left_id}~{e.right_id}  {e.relation}  "
             f"same={e.same_evidence}  {e.status}  {e.reason}" for e in edges]
    _emit([e.to_dict() for e in edges],
          "\n".join(human) or "（无边）", args.json)


def cmd_evidence_clusters(args) -> None:
    from .evidence import cluster_members
    store = _get_store(args)
    clusters = cluster_members(store, args.college)
    human = [f"{cid}: {members}" for cid, members in clusters.items()]
    _emit(clusters, "\n".join(human) or "（无聚类）", args.json)


def cmd_leakage(args) -> None:
    from .leakage import questionnaire_leakage
    store = _get_store(args)
    report = questionnaire_leakage(store, args.college)
    payload = {
        "college": report.college,
        "leaked": report.leaked_fragments,
        "blocked_by": report.blocked_by,
    }
    if not report.leaked_ids:
        human = f"院校 {args.college} 当前无问卷泄漏"
    else:
        human = [f"院校 {args.college} 泄漏片段："]
        for fid in report.leaked_ids:
            human.append(f"  {fid} -> 课堂证据 {report.leaked_fragments[fid]}，"
                         f"阻挡包 {report.blocked_by.get(fid)}")
        human = "\n".join(human)
    _emit(payload, human, args.json)


def cmd_plan_make(args) -> None:
    from .planning import explain_conflicts, plan_questionnaire
    store = _get_store(args)
    result = plan_questionnaire(store, args.college, note=args.note)
    payload = {
        "plan": result.plan.to_dict(),
        "eligible": result.eligible_question_ids,
        "excluded": result.excluded,
    }
    _emit(payload, explain_conflicts(store, result.plan), args.json)


def cmd_plan_list(args) -> None:
    store = _get_store(args)
    plans = store.find("plans", lambda p: p.college == args.college)
    human = [f"{p.id}  {p.status:<6} success={p.success}  题={p.question_ids}  "
             f"冲突={len(p.conflicts)}" for p in plans]
    _emit([p.to_dict() for p in plans],
          "\n".join(human) or "（无规划）", args.json)


def cmd_package_classroom(args) -> None:
    from .packages import build_classroom_package
    store = _get_store(args)
    pkg = build_classroom_package(store, args.college, args.fragment,
                                  note=args.note)
    _emit(pkg.to_dict(),
          f"课堂包 {pkg.id} 已冻结交付（院校 {args.college}，片段 {pkg.fragment_ids}）",
          args.json)


def cmd_package_questionnaire(args) -> None:
    from .packages import build_questionnaire_package
    store = _get_store(args)
    pkg = build_questionnaire_package(store, args.college, args.plan,
                                      note=args.note)
    _emit(pkg.to_dict(),
          f"问卷包 {pkg.id} 已冻结交付（院校 {args.college}，试题 {pkg.question_ids}）",
          args.json)


def cmd_package_list(args) -> None:
    from .packages import list_packages
    store = _get_store(args)
    pkgs = list_packages(store, args.college, args.kind)
    human = [f"{p.id}  {p.kind:<13} {p.college}  片段={p.fragment_ids}  "
             f"题={p.question_ids}  {p.note[:24]}" for p in pkgs]
    _emit([p.to_dict() for p in pkgs],
          "\n".join(human) or "（无交付包）", args.json)


def cmd_package_show(args) -> None:
    from .packages import view_package
    store = _get_store(args)
    view = view_package(store, args.package)
    pkg = view.package
    lines = [f"交付包 {pkg.id}（{pkg.kind}，院校 {pkg.college}，{pkg.created_at}）",
             f"备注：{pkg.note}",
             "冻结时词义口径："]
    for term in pkg.term_snapshot:
        for sense in term["senses"]:
            lines.append(f"  {term['id']}#{sense['sense_id']} "
                         f"[{sense['term_class']}] {sense['label']} "
                         f"变体={term['variants']}")
    lines.append("冻结时证据分组：")
    for fid, cid in sorted(pkg.cluster_map.items()):
        lines.append(f"  {fid} -> {cid}")
    lines.append("冻结时标注（节选 term_ref/norm）：")
    for ann in pkg.annotation_snapshot:
        for tok in ann["tokens"]:
            if tok["role"] in ("term", "number_unit", "punct"):
                lines.append(f"  {ann['fragment_id']} {tok['span']} "
                             f"{tok['text']!r} {tok['role']} "
                             f"{tok.get('term_ref') or tok.get('norm') or ''}")
    _emit(pkg.to_dict(), "\n".join(lines), args.json)


def cmd_rejudge(args) -> None:
    from .revisions import rejudge_pending
    store = _get_store(args)
    term_ids = set(args.term) if args.term else None
    rejudged = rejudge_pending(store, term_ids)
    _emit({"fragments": rejudged},
          f"已重判未交付片段 {rejudged}；已交付包保持冻结口径", args.json)


def cmd_events(args) -> None:
    path = Path(args.store) / "events.log"
    if not path.exists():
        _emit([], "（暂无事件日志）", args.json)
        return
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line) for line in lines if line]
    if args.json:
        print(json.dumps(events, ensure_ascii=False, indent=2))
    else:
        for event in events:
            print(f"{event['at']}  {event['event']}  "
                  + json.dumps({k: v for k, v in event.items()
                                if k not in ("at", "event")},
                               ensure_ascii=False))


# ---------------------------------------------------------------- 参数装配
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corpuskit",
        description="术语候选、工单证据与院校交付包管理（标准库实现）",
    )
    parser.add_argument("--store", default="corpus_store", help="工作区目录")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_parser(name: str, **kwargs) -> argparse.ArgumentParser:
        p = sub.add_parser(name, **kwargs)
        p.add_argument("--json", action="store_true", help="机器可读输出")
        return p

    p = add_parser("init", help="初始化工作区，可带种子引导")
    p.add_argument("--seed", help="种子语料 JSON 路径（如 data/corpus.json）")
    p.set_defaults(func=cmd_init)

    p = add_parser("term-add", help="新增术语词目")
    p.add_argument("headword")
    p.add_argument("--label", required=True)
    p.add_argument("--term-class", required=True,
                   choices=["skill", "table", "metric", "business"])
    p.add_argument("--variant", action="append")
    p.add_argument("--context", action="append")
    p.add_argument("--role")
    p.set_defaults(func=cmd_term_add)

    p = add_parser("term-list", help="列出词目与词义")
    p.set_defaults(func=cmd_term_list)

    p = add_parser("sense-index", help="查看词面在不同语境下的词义归属")
    p.add_argument("surface", nargs="+")
    p.add_argument("--context", action="append")
    p.set_defaults(func=cmd_sense_index)

    p = add_parser("revision-propose", help="登记词义修订提案")
    p.add_argument("--term", required=True)
    p.add_argument("--kind", choices=["add_sense", "add_variant"],
                   default="add_sense")
    p.add_argument("--sense-id")
    p.add_argument("--label")
    p.add_argument("--term-class", choices=["skill", "table", "metric",
                                            "business"])
    p.add_argument("--variant")
    p.add_argument("--context", action="append")
    p.add_argument("--role")
    p.add_argument("--note", default="")
    p.add_argument("--activate", action="store_true", help="提案后立即生效")
    p.set_defaults(func=cmd_revision_propose)

    p = add_parser("revision-activate", help="生效修订")
    p.add_argument("revision")
    p.set_defaults(func=cmd_revision_activate)

    p = add_parser("revision-revert", help="撤回修订并重判未交付内容")
    p.add_argument("revision")
    p.add_argument("--reviewer", default="")
    p.set_defaults(func=cmd_revision_revert)

    p = add_parser("revision-list", help="列出修订")
    p.set_defaults(func=cmd_revision_list)

    p = add_parser("fragment-add", help="新增工单/题干片段并标注")
    p.add_argument("text")
    p.add_argument("--source", default="work_order")
    p.add_argument("--context", default="")
    p.add_argument("--note", default="")
    p.add_argument("--college", action="append")
    p.set_defaults(func=cmd_fragment_add)

    p = add_parser("fragment-import", help="从外部 JSON 持续导入片段")
    p.add_argument("path")
    p.add_argument("--source", default="external")
    p.add_argument("--college", action="append")
    p.set_defaults(func=cmd_fragment_import)

    p = add_parser("fragment-show", help="查看带偏移的标注")
    p.add_argument("fragment")
    p.set_defaults(func=cmd_fragment_show)

    p = add_parser("fragment-list", help="列出片段")
    p.set_defaults(func=cmd_fragment_list)

    p = add_parser("question-add", help="新增试题")
    p.add_argument("stem")
    p.add_argument("--ability", required=True)
    p.add_argument("--context", required=True)
    p.add_argument("--rubric", default="")
    p.add_argument("--college", action="append")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_question_add)

    p = add_parser("question-list", help="列出试题")
    p.set_defaults(func=cmd_question_list)

    p = add_parser("edge-suggest", help="机器生成证据关系建议")
    p.add_argument("--threshold", type=float, default=0.34)
    p.set_defaults(func=cmd_edge_suggest)

    p = add_parser("edge-add", help="手工登记证据边")
    p.add_argument("left")
    p.add_argument("right")
    p.add_argument("--relation", required=True,
                   choices=["split", "reuse_obs", "paraphrase", "derive"])
    p.add_argument("--same", dest="same", action="store_true", default=None)
    p.add_argument("--not-same", dest="same", action="store_false")
    p.add_argument("--reason", default="")
    p.add_argument("--reviewer", default="")
    p.add_argument("--college", action="append")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_edge_add)

    p = add_parser("edge-decide", help="评审证据边")
    p.add_argument("edge")
    p.add_argument("decision", choices=["confirm", "reject"])
    p.add_argument("--reviewer", default="")
    p.set_defaults(func=cmd_edge_decide)

    p = add_parser("edge-list", help="列出证据边")
    p.set_defaults(func=cmd_edge_list)

    p = add_parser("evidence-clusters", help="查看同一考查证据聚类")
    p.add_argument("--college")
    p.set_defaults(func=cmd_evidence_clusters)

    p = add_parser("leakage", help="问卷泄漏报告")
    p.add_argument("--college", required=True)
    p.set_defaults(func=cmd_leakage)

    p = add_parser("plan-make", help="规划问卷（必考能力×语境）")
    p.add_argument("--college", required=True)
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_plan_make)

    p = add_parser("plan-list", help="列出规划")
    p.add_argument("--college", required=True)
    p.set_defaults(func=cmd_plan_list)

    p = add_parser("package-classroom", help="冻结课堂资料包")
    p.add_argument("--college", required=True)
    p.add_argument("--fragment", action="append", required=True)
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_package_classroom)

    p = add_parser("package-questionnaire", help="按规划冻结问卷包")
    p.add_argument("--college", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_package_questionnaire)

    p = add_parser("package-list", help="列出交付包")
    p.add_argument("--college")
    p.add_argument("--kind", choices=["classroom", "questionnaire"])
    p.set_defaults(func=cmd_package_list)

    p = add_parser("package-show", help="按交付时口径查阅交付包")
    p.add_argument("package")
    p.set_defaults(func=cmd_package_show)

    p = add_parser("rejudge", help="按当前词义重判未交付内容")
    p.add_argument("--term", action="append")
    p.set_defaults(func=cmd_rejudge)

    p = add_parser("events", help="查看审计事件日志")
    p.set_defaults(func=cmd_events)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # 子解析器的 --json 默认值会覆盖全局同名参数，这里先取全局值再合并
    argv = list(sys.argv[1:] if argv is None else argv)
    global_json = "--json" in argv
    args = parser.parse_args(argv)
    args.json = global_json or bool(getattr(args, "json", False))
    try:
        args.func(args)
    except (KeyError, ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
