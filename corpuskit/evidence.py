"""证据关系与“同一考查证据”聚类。

评审确认的证据边构成无向图，本模块用并查集求传递闭包：
无论经过多少跳派生（split/paraphrase/derive），同一连通块即同一考查证据。
``reuse_obs`` 边表示“只共用业务名词或数值、无需读同一观察”，
``same_evidence=False``，不参与合并。
"""
from __future__ import annotations

from dataclasses import dataclass

from .linguistics import quantities, term_tokens
from .models import (
    EdgeStatus,
    EvidenceEdge,
    RelationType,
    now_iso,
)


class UnionFind:
    def __init__(self, nodes: list[str]):
        self.parent = {n: n for n in nodes}

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # 路径压缩
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # 稳定排序：保留字典序最小的代表，便于复现
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for node in self.parent:
            out.setdefault(self.find(node), []).append(node)
        return {root: sorted(members) for root, members in sorted(out.items())}


def confirmed_edges(store, college: str | None = None) -> list[EvidenceEdge]:
    edges = store.find(
        "edges",
        lambda e: e.status == EdgeStatus.CONFIRMED and e.same_evidence,
    )
    if college is not None:
        edges = [e for e in edges if not e.colleges or college in e.colleges]
    return edges


def build_clusters(store, college: str | None = None) -> dict[str, str]:
    """返回 fragment_id -> 证据簇ID（簇ID 取最小成员ID，稳定可复现）。"""
    nodes = [f.id for f in store.all("fragments")]
    uf = UnionFind(nodes)
    for edge in confirmed_edges(store, college):
        if edge.left_id in uf.parent and edge.right_id in uf.parent:
            uf.union(edge.left_id, edge.right_id)
    groups = uf.groups()
    cluster_map: dict[str, str] = {}
    for root, members in groups.items():
        cluster_id = f"ev-{min(members, key=lambda x: (len(x), x))}"
        for member in members:
            cluster_map[member] = cluster_id
    return cluster_map


def cluster_members(store, college: str | None = None) -> dict[str, list[str]]:
    cluster_map = build_clusters(store, college)
    out: dict[str, list[str]] = {}
    for fragment_id, cluster_id in cluster_map.items():
        out.setdefault(cluster_id, []).append(fragment_id)
    return {k: sorted(v) for k, v in sorted(out.items())}


def fragment_cluster(store, fragment_id: str,
                     college: str | None = None) -> str:
    return build_clusters(store, college).get(fragment_id, f"ev-{fragment_id}")


# ------------------------------------------------------------- 候选边建议
def _char_bigrams(text: str) -> set[str]:
    # 仅保留中文/字母数字字符，去掉标点与空白
    cleaned = [ch for ch in text if ch.strip() and not _is_punct(ch)]
    return {cleaned[i] + cleaned[i + 1] for i in range(len(cleaned) - 1)}


def _is_punct(ch: str) -> bool:
    return ch in "，。；：、？！（）()“”\"‘’'《》【】—–-…·|.,;:?!·"


def bigram_jaccard(a: str, b: str) -> float:
    ga, gb = _char_bigrams(a), _char_bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def shared_terms(store, frag_a_id: str, frag_b_id: str) -> set[str]:
    ann_a = store.annotation_for(frag_a_id)
    ann_b = store.annotation_for(frag_b_id)
    if ann_a is None or ann_b is None:
        return set()
    refs_a = {t.term_ref for t in term_tokens(ann_a)}
    refs_b = {t.term_ref for t in term_tokens(ann_b)}
    return refs_a & refs_b


def shared_quantities(store, frag_a_id: str, frag_b_id: str) -> set[str]:
    ann_a = store.annotation_for(frag_a_id)
    ann_b = store.annotation_for(frag_b_id)
    if ann_a is None or ann_b is None:
        return set()
    qa = {t.norm or t.text for t in quantities(ann_a)}
    qb = {t.norm or t.text for t in quantities(ann_b)}
    return qa & qb


@dataclass
class Suggestion:
    left_id: str
    right_id: str
    relation: str
    same_evidence: bool
    reason: str
    score: float


def suggest_edges(store, threshold: float = 0.34) -> list[Suggestion]:
    """对尚无任何边的片段对给出机器建议，供教研评审。

    - 高字面重合（同义改写/拼接）→ same_evidence 候选；
    - 只共用术语或观察量 → reuse_obs，明确不是同一证据。
    """
    fragments = store.all("fragments")
    existing: set[tuple[str, str]] = set()
    for edge in store.all("edges"):
        existing.add(tuple(sorted((edge.left_id, edge.right_id))))

    out: list[Suggestion] = []
    for i, a in enumerate(fragments):
        for b in fragments[i + 1:]:
            key = tuple(sorted((a.id, b.id)))
            if key in existing:
                continue
            score = bigram_jaccard(a.raw_text, b.raw_text)
            terms = shared_terms(store, a.id, b.id)
            quants = shared_quantities(store, a.id, b.id)
            if score >= threshold:
                relation = (RelationType.PARAPHRASE if score >= 0.55
                            else RelationType.SPLIT)
                out.append(Suggestion(
                    a.id, b.id, relation, True,
                    f"字面重合度 {score:.2f}，疑似同义改写/拼接", score,
                ))
            elif terms or quants:
                bits = []
                if terms:
                    bits.append(f"共用术语 {sorted(terms)}")
                if quants:
                    bits.append(f"共用观察量 {sorted(quants)}")
                out.append(Suggestion(
                    a.id, b.id, RelationType.REUSE_OBS, False,
                    "；".join(bits) + "，无需读同一观察", score,
                ))
    out.sort(key=lambda s: s.score, reverse=True)
    return out


def add_suggested_edges(store, threshold: float = 0.34) -> list[EvidenceEdge]:
    """把机器建议落为 suggested 边（仍需人工 confirm 才参与聚类）。"""
    created: list[EvidenceEdge] = []
    for sug in suggest_edges(store, threshold):
        edge = EvidenceEdge(
            id=store.next_id("edges"),
            left_id=sug.left_id,
            right_id=sug.right_id,
            relation=sug.relation,
            same_evidence=sug.same_evidence,
            status=EdgeStatus.SUGGESTED,
            reason=sug.reason,
            created_at=now_iso(),
        )
        store.add("edges", edge, event="edge_suggested")
        created.append(edge)
    return created


# ------------------------------------------------------------- 评审决定
def decide_edge(store, edge_id: str, confirm: bool,
                reviewer: str, relation: str | None = None,
                same_evidence: bool | None = None) -> EvidenceEdge:
    def mutate(edge: EvidenceEdge) -> None:
        edge.status = EdgeStatus.CONFIRMED if confirm else EdgeStatus.REJECTED
        edge.reviewer = reviewer
        edge.decided_at = now_iso()
        if relation is not None:
            edge.relation = relation
        if same_evidence is not None:
            edge.same_evidence = same_evidence

    return store.update(
        "edges", edge_id, mutate,
        event="edge_confirmed" if confirm else "edge_rejected",
    )


def add_edge(store, left_id: str, right_id: str, relation: str,
             same_evidence: bool, reason: str = "",
             reviewer: str = "", colleges: list[str] | None = None,
             auto_confirm: bool = False) -> EvidenceEdge:
    edge = EvidenceEdge(
        id=store.next_id("edges"),
        left_id=left_id,
        right_id=right_id,
        relation=relation,
        same_evidence=same_evidence,
        status=EdgeStatus.CONFIRMED if auto_confirm else EdgeStatus.SUGGESTED,
        reason=reason,
        reviewer=reviewer if auto_confirm else "",
        colleges=colleges or [],
        created_at=now_iso(),
        decided_at=now_iso() if auto_confirm else None,
    )
    store.add("edges", edge,
              event="edge_confirmed" if auto_confirm else "edge_suggested")
    return edge
