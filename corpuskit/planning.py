"""学期问卷规划：必考能力 × 语境覆盖。

规则：

1. 只从“未泄漏、未交付、院校适用”的候选题中选题；
2. 每个必考单元（CoverageCell，required=True）必须被至少一道
   ``(ability, context)`` 完全匹配的题覆盖；
3. 若某必考单元在排除泄漏后无题可覆盖，显式记录冲突，
   绝不删除必考项、不撤换已发课堂包；
4. 用回溯做集合覆盖，优先选“一维多盖”的题，最小化题量；
5. 规划结果落库为 Plan；后续泄漏前提变化时由 leakage 模块标记 stale。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .leakage import eligible_questions
from .models import Plan, PlanStatus, now_iso


@dataclass
class PlanningResult:
    plan: Plan
    eligible_question_ids: list[str] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)  # 题ID -> 排除原因

    @property
    def success(self) -> bool:
        return self.plan.success


def _cells_for_college(store, college: str):
    return [
        cell for cell in store.all("coverage")
        if cell.required and (not cell.colleges or college in cell.colleges)
    ]


def _covers(question, cell) -> bool:
    return question.ability == cell.ability and question.context == cell.context


def _backtrack(questions, cells, chosen: list[str], covered: frozenset[int]):
    """返回覆盖全部 cells 的最小题ID列表；无解返回 None。"""
    if len(covered) == len(cells):
        return list(chosen)

    # 选“未覆盖单元中可选题最少”的单元（MRV 启发），剪枝更快
    pending = [idx for idx in range(len(cells)) if idx not in covered]
    target = min(
        pending,
        key=lambda idx: sum(1 for q in questions if _covers(q, cells[idx])),
    )
    candidates = [q for q in questions if _covers(q, cells[target])]
    if not candidates:
        return None

    # 先试覆盖未覆盖单元更多的题
    candidates.sort(
        key=lambda q: sum(
            1 for idx, cell in enumerate(cells)
            if idx not in covered and _covers(q, cell)
        ),
        reverse=True,
    )
    best = None
    for q in candidates:
        if q.id in chosen:
            continue
        new_covered = frozenset(covered | {
            idx for idx, cell in enumerate(cells) if _covers(q, cell)
        })
        result = _backtrack(questions, cells, chosen + [q.id], new_covered)
        if result is not None and (best is None or len(result) < len(best)):
            best = result
    return best


def plan_questionnaire(store, college: str, note: str = "") -> PlanningResult:
    cells = _cells_for_college(store, college)
    eligible, report = eligible_questions(store, college)

    excluded: dict[str, str] = {}
    for q in store.all("questions"):
        if q.colleges and college not in q.colleges:
            excluded[q.id] = "院校不适用"
        elif q.status == "delivered":
            excluded[q.id] = "已交付"
        elif report.is_leaked(q.stem_fragment_id):
            blockers = report.blocked_by.get(q.stem_fragment_id, [])
            excluded[q.id] = f"证据已进入课堂包 {blockers}"

    conflicts: list[dict] = []
    for cell in cells:
        usable = [q for q in eligible if _covers(q, cell)]
        if not usable:
            # 区分“根本没有这个考点的题”与“题都被泄漏挡掉”
            all_matching = [
                q for q in store.all("questions")
                if _covers(q, cell) and (not q.colleges or college in q.colleges)
            ]
            if not all_matching:
                reason = "题库中没有覆盖该能力×语境的候选题"
            else:
                reason = "候选题证据均已泄漏：" + "；".join(
                    f"{q.id}（{excluded.get(q.id, '泄漏')}）" for q in all_matching
                )
            conflicts.append({
                "cell_id": cell.id,
                "ability": cell.ability,
                "context": cell.context,
                "reason": reason,
            })

    chosen = _backtrack(eligible, cells, [], frozenset()) if not conflicts else None
    success = chosen is not None
    covered_cells: list[str] = []
    if success:
        chosen_set = set(chosen)
        chosen_questions = [q for q in eligible if q.id in chosen_set]
        covered_cells = [
            cell.id for cell in cells
            if any(_covers(q, cell) for q in chosen_questions)
        ]

    plan = Plan(
        id=store.next_id("plans"),
        college=college,
        question_ids=chosen or [],
        covered_cells=covered_cells,
        conflicts=conflicts,
        success=success,
        status=PlanStatus.DRAFT,
        created_at=now_iso(),
    )
    store.add("plans", plan, event="questionnaire_planned")
    return PlanningResult(
        plan=plan,
        eligible_question_ids=[q.id for q in eligible],
        excluded=excluded,
    )


def explain_conflicts(store, plan: Plan) -> str:
    if plan.success:
        return f"规划 {plan.id} 成功：选题 {plan.question_ids}，覆盖 {plan.covered_cells}"
    lines = [f"规划 {plan.id} 无法同时交付以下必考单元（未削减任何必考项）："]
    for c in plan.conflicts:
        lines.append(
            f"  - 能力「{c['ability']}」× 语境「{c['context']}」：{c['reason']}"
        )
    return "\n".join(lines)
