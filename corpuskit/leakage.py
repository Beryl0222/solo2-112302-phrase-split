"""泄漏检测：同一考查证据不得横跨课堂资料与未开考问卷。

屏障是双向的：

- 规划问卷时，凡证据簇已进入任一已交付**课堂包**的候选题一律剔除；
- 构建课堂包时，凡证据簇已出现在已交付且仍未开考的**问卷包**中的片段拒绝装入。

只共用业务名词或数值（``reuse_obs`` 边，``same_evidence=False``）不会把两个
片段划入同一证据簇，因此可以分属课堂与问卷。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .evidence import build_clusters
from .models import PackageKind, Question, QuestionStatus


@dataclass
class LeakageReport:
    college: str
    leaked_fragments: dict[str, list[str]] = field(default_factory=dict)
    # fragment_id -> 阻挡它的已交付包ID列表
    blocked_by: dict[str, list[str]] = field(default_factory=dict)

    @property
    def leaked_ids(self) -> list[str]:
        return sorted(self.leaked_fragments)

    def is_leaked(self, fragment_id: str) -> bool:
        return fragment_id in self.leaked_fragments


def _college_match(package_college: str, college: str) -> bool:
    return package_college == college


def _classroom_packages(store, college: str):
    return [p for p in store.all("packages")
            if p.kind == PackageKind.CLASSROOM and _college_match(p.college, college)]


def _questionnaire_packages(store, college: str):
    return [p for p in store.all("packages")
            if p.kind == PackageKind.QUESTIONNAIRE and _college_match(p.college, college)]


def classroom_clusters(store, college: str) -> dict[str, list[str]]:
    """该院校已交付课堂包占用的证据簇 -> 包内片段。"""
    out: dict[str, list[str]] = {}
    cluster_map = build_clusters(store)
    for pkg in _classroom_packages(store, college):
        for fid in pkg.fragment_ids:
            cluster_id = pkg.cluster_map.get(fid) or cluster_map.get(fid)
            if cluster_id is None:
                continue
            out.setdefault(cluster_id, [])
            if fid not in out[cluster_id]:
                out[cluster_id].append(fid)
    return out


def questionnaire_clusters(store, college: str) -> dict[str, list[str]]:
    """该院校已交付问卷包（未开考）占用的证据簇 -> 包内片段。"""
    out: dict[str, list[str]] = {}
    cluster_map = build_clusters(store)
    for pkg in _questionnaire_packages(store, college):
        for fid in pkg.fragment_ids:
            cluster_id = pkg.cluster_map.get(fid) or cluster_map.get(fid)
            if cluster_id is None:
                continue
            out.setdefault(cluster_id, [])
            if fid not in out[cluster_id]:
                out[cluster_id].append(fid)
    return out


def questionnaire_leakage(store, college: str) -> LeakageReport:
    """面向问卷规划：返回被已交付课堂包阻挡的片段。"""
    report = LeakageReport(college=college)
    occupied = classroom_clusters(store, college)
    cluster_map = build_clusters(store)
    classroom_pkgs = _classroom_packages(store, college)
    for fid, cluster_id in cluster_map.items():
        if cluster_id in occupied:
            report.leaked_fragments[fid] = sorted(occupied[cluster_id])
            blockers = [
                pkg.id for pkg in classroom_pkgs
                if any(pkg.cluster_map.get(x) == cluster_id
                       or cluster_map.get(x) == cluster_id
                       for x in pkg.fragment_ids)
            ]
            report.blocked_by[fid] = sorted(set(blockers))
    return report


def classroom_blockers(store, college: str, fragment_ids: list[str]) -> dict[str, list[str]]:
    """面向课堂包构建：返回被未开考问卷包阻挡的片段及阻挡包。"""
    occupied = questionnaire_clusters(store, college)
    cluster_map = build_clusters(store)
    questionnaire_pkgs = _questionnaire_packages(store, college)
    blockers: dict[str, list[str]] = {}
    for fid in fragment_ids:
        cluster_id = cluster_map.get(fid)
        if cluster_id is None or cluster_id not in occupied:
            continue
        bad = [
            pkg.id for pkg in questionnaire_pkgs
            if any(pkg.cluster_map.get(x) == cluster_id
                   or cluster_map.get(x) == cluster_id
                   for x in pkg.fragment_ids)
        ]
        if bad:
            blockers[fid] = sorted(set(bad))
    return blockers


def question_is_leaked(store, college: str, question: Question,
                       report: LeakageReport | None = None) -> bool:
    if question.status == QuestionStatus.DELIVERED:
        return False
    report = report or questionnaire_leakage(store, college)
    return report.is_leaked(question.stem_fragment_id)


def eligible_questions(store, college: str) -> tuple[list[Question], LeakageReport]:
    report = questionnaire_leakage(store, college)
    questions = [
        q for q in store.all("questions")
        if (not q.colleges or college in q.colleges)
        and q.status != QuestionStatus.DELIVERED
        and not report.is_leaked(q.stem_fragment_id)
    ]
    return questions, report


def mark_stale_plans(store, college: str) -> list[str]:
    """新交付或重判后重算泄漏，失效的既有问卷规划标记为 stale。

    触发条件（任一即可）：

    - 规划选题中出现新的泄漏题干；
    - 规划选题被删除或不再院校适用；
    - 规划声称覆盖的必考单元已不存在（必考口径调整）。
    """
    report = questionnaire_leakage(store, college)
    cell_ids = {c.id for c in store.all("coverage")}
    stale: list[str] = []
    for plan in store.find("plans", lambda p: p.college == college
                           and p.status == "draft"):
        invalid = any(
            not _exists(store, qid)
            or report.is_leaked(store.get("questions", qid).stem_fragment_id)
            for qid in plan.question_ids
        ) or any(cid not in cell_ids for cid in plan.covered_cells)
        if invalid:
            plan.status = "stale"
            store.upsert("plans", plan)
            stale.append(plan.id)
    if stale:
        store.log_event("plans_marked_stale", {"college": college, "plans": stale})
    return stale


def _exists(store, question_id: str) -> bool:
    try:
        store.get("questions", question_id)
        return True
    except KeyError:
        return False
