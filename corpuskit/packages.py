"""交付包构建与历史查阅。

交付包一旦生成即不可变：词目（含词义/变体）、标注、证据簇映射全部快照。
构建时执行双向屏障检查；交付后相关试题状态转为 delivered，
既有问卷规划若前提变化由 leakage 模块标记 stale。
"""
from __future__ import annotations

from dataclasses import dataclass

from .evidence import build_clusters
from .leakage import classroom_blockers, mark_stale_plans
from .models import (
    DeliveryPackage,
    PackageKind,
    Plan,
    QuestionStatus,
    now_iso,
)


class LeakageError(RuntimeError):
    pass


@dataclass
class FrozenView:
    """按交付时刻口径还原的只读视图。"""
    package: DeliveryPackage

    def term_snapshot(self) -> list[dict]:
        return self.package.term_snapshot

    def annotation_of(self, fragment_id: str) -> dict | None:
        for ann in self.package.annotation_snapshot:
            if ann["fragment_id"] == fragment_id:
                return ann
        return None

    def cluster_of(self, fragment_id: str) -> str | None:
        return self.package.cluster_map.get(fragment_id)


def _snapshot_terms(store) -> list[dict]:
    return [term.to_dict() for term in store.all("terms")]


def _snapshot_annotations(store, fragment_ids: list[str]) -> list[dict]:
    out = []
    for fid in fragment_ids:
        ann = store.annotation_for(fid)
        if ann is not None:
            out.append(ann.to_dict())
    return out


def build_classroom_package(store, college: str, fragment_ids: list[str],
                            note: str = "") -> DeliveryPackage:
    """构建课堂资料包；与未开考问卷冲突的片段拒绝装入。"""
    blockers = classroom_blockers(store, college, fragment_ids)
    if blockers:
        details = "; ".join(f"{fid} 被问卷包 {pids}"
                            for fid, pids in sorted(blockers.items()))
        raise LeakageError(f"课堂包与未开考问卷存在同一证据冲突：{details}")

    cluster_map = {
        fid: cid for fid, cid in build_clusters(store, college).items()
        if fid in fragment_ids
    }
    pkg = DeliveryPackage(
        id=store.next_id("packages"),
        kind=PackageKind.CLASSROOM,
        college=college,
        fragment_ids=list(fragment_ids),
        question_ids=[],
        term_snapshot=_snapshot_terms(store),
        annotation_snapshot=_snapshot_annotations(store, fragment_ids),
        cluster_map=cluster_map,
        plan_id=None,
        note=note,
        created_at=now_iso(),
    )
    store.add("packages", pkg, event="classroom_delivered")
    mark_stale_plans(store, college)
    return pkg


def build_questionnaire_package(store, college: str, plan_id: str,
                                note: str = "") -> DeliveryPackage:
    """按成功规划冻结问卷包；再次复核泄漏，防止规划后出现新课堂交付。"""
    plan: Plan = store.get("plans", plan_id)
    if plan.college != college:
        raise ValueError(f"规划 {plan_id} 不属于院校 {college}")
    if plan.status == "stale":
        raise LeakageError(
            f"规划 {plan_id} 已因后续交付或重判失效（stale），请重新规划"
        )
    if not plan.success:
        raise LeakageError("规划存在未解决冲突，不能交付问卷")

    questions = [store.get("questions", qid) for qid in plan.question_ids]
    fragment_ids = [q.stem_fragment_id for q in questions]

    # 二次复核：规划之后若又交付了课堂包，这里必须拦下
    from .leakage import questionnaire_leakage
    report = questionnaire_leakage(store, college)
    leaked = [fid for fid in fragment_ids if report.is_leaked(fid)]
    if leaked:
        raise LeakageError(
            f"规划后出现新泄漏，片段 {sorted(leaked)} 已进入课堂包；"
            "请重新规划，不得撤换已发课堂内容"
        )

    cluster_map = {
        fid: cid for fid, cid in build_clusters(store, college).items()
        if fid in fragment_ids
    }
    pkg = DeliveryPackage(
        id=store.next_id("packages"),
        kind=PackageKind.QUESTIONNAIRE,
        college=college,
        fragment_ids=fragment_ids,
        question_ids=list(plan.question_ids),
        term_snapshot=_snapshot_terms(store),
        annotation_snapshot=_snapshot_annotations(store, fragment_ids),
        cluster_map=cluster_map,
        plan_id=plan.id,
        note=note,
        created_at=now_iso(),
    )
    store.add("packages", pkg, event="questionnaire_delivered")
    for q in questions:
        q.status = QuestionStatus.DELIVERED
        store.upsert("questions", q)
    return pkg


def view_package(store, package_id: str) -> FrozenView:
    return FrozenView(store.get("packages", package_id))


def list_packages(store, college: str | None = None,
                  kind: str | None = None) -> list[DeliveryPackage]:
    return store.find(
        "packages",
        lambda p: (college is None or p.college == college)
        and (kind is None or p.kind == kind),
    )
