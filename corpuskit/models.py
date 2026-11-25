"""领域数据模型。

所有实体均为可 JSON 序列化的 dataclass，嵌套实体通过 ``from_dict`` 重建。
ID 采用 ``<前缀>-<自增序号>`` 形式，由 Store 统一发放。
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    """UTC 时间戳，供事件日志与冻结包使用。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 枚举（以字符串常量落盘，避免枚举值与 JSON 之间反复转换）
# ---------------------------------------------------------------------------

class TermClass:
    SKILL = "skill"          # 技能/操作，如“主键回填”
    TABLE = "table"          # 表名/报表名，如“主键回填表”
    METRIC = "metric"        # 观察量
    BUSINESS = "business"    # 普通业务名词


class TokenRole:
    TERM = "term"            # 命中术语词目
    GENERIC = "generic"      # 通用业务词
    NUMBER_UNIT = "number_unit"
    PUNCT = "punct"
    WORD = "word"            # 普通词/字
    WHITESPACE = "whitespace"


class RelationType:
    SPLIT = "split"            # 派生练习拼接两个旧片段
    REUSE_OBS = "reuse_obs"    # 只复用观察量、更换问法（非同一证据）
    PARAPHRASE = "paraphrase"  # 同义改写，源题证据进入新材料
    DERIVE = "derive"          # 一般多跳派生


class EdgeStatus:
    SUGGESTED = "suggested"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class PackageKind:
    CLASSROOM = "classroom"
    QUESTIONNAIRE = "questionnaire"


class QuestionStatus:
    CANDIDATE = "candidate"
    PLANNED = "planned"
    DELIVERED = "delivered"


class RevisionStatus:
    PROPOSED = "proposed"
    ACTIVE = "active"
    REVERTED = "reverted"


class PlanStatus:
    DRAFT = "draft"
    STALE = "stale"  # 之后的交付/重判使泄漏前提发生变化


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class Span:
    """原字面中的半开区间 [start, end)，任何归一化都不改动它。"""
    start: int
    end: int

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Span":
        return cls(start=d["start"], end=d["end"])


@dataclass
class Token:
    text: str                 # 原字面切片 text[span.start:span.end]
    span: Span
    role: str
    norm: str | None = None             # 单位/标点的归一形
    term_ref: str | None = None         # “词目ID#词义ID”
    components: list[Span] = field(default_factory=list)  # 复合词内部词界

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "text": self.text,
            "span": self.span.to_dict(),
            "role": self.role,
        }
        if self.norm is not None:
            out["norm"] = self.norm
        if self.term_ref is not None:
            out["term_ref"] = self.term_ref
        if self.components:
            out["components"] = [s.to_dict() for s in self.components]
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Token":
        return cls(
            text=d["text"],
            span=Span.from_dict(d["span"]),
            role=d["role"],
            norm=d.get("norm"),
            term_ref=d.get("term_ref"),
            components=[Span.from_dict(s) for s in d.get("components", [])],
        )


@dataclass
class Sense:
    sense_id: str             # 词目内唯一，如 s1
    label: str                # 短释义
    term_class: str           # TermClass 取值
    contexts: list[str] = field(default_factory=list)   # 消歧线索
    role: str | None = None   # 该语境下的词角色描述


@dataclass
class Term:
    id: str
    headword: str
    variants: list[str] = field(default_factory=list)
    senses: list[Sense] = field(default_factory=list)
    active_sense_id: str | None = None
    created_at: str = ""

    def sense(self, sense_id: str) -> Sense:
        for s in self.senses:
            if s.sense_id == sense_id:
                return s
        raise KeyError(f"词义 {sense_id} 不在词目 {self.id} 中")

    def all_surfaces(self) -> list[str]:
        surfaces = {self.headword, *self.variants}
        return sorted(surfaces, key=len, reverse=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "headword": self.headword,
            "variants": list(self.variants),
            "senses": [
                {
                    "sense_id": s.sense_id,
                    "label": s.label,
                    "term_class": s.term_class,
                    "contexts": list(s.contexts),
                    "role": s.role,
                }
                for s in self.senses
            ],
            "active_sense_id": self.active_sense_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Term":
        return cls(
            id=d["id"],
            headword=d["headword"],
            variants=list(d.get("variants", [])),
            senses=[Sense(**s) for s in d.get("senses", [])],
            active_sense_id=d.get("active_sense_id"),
            created_at=d.get("created_at", ""),
        )


@dataclass
class SenseRevision:
    """词义修订（新增词义/改释义/改线索/加变体）的生命周期记录。"""
    id: str
    term_id: str
    sense_id: str
    kind: str                  # add_sense / update_sense / add_variant
    payload: dict[str, Any]
    status: str = RevisionStatus.PROPOSED
    note: str = ""
    created_at: str = ""
    reverted_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "term_id": self.term_id,
            "sense_id": self.sense_id,
            "kind": self.kind,
            "payload": dict(self.payload),
            "status": self.status,
            "note": self.note,
            "created_at": self.created_at,
            "reverted_at": self.reverted_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SenseRevision":
        return cls(
            id=d["id"],
            term_id=d["term_id"],
            sense_id=d["sense_id"],
            kind=d["kind"],
            payload=dict(d.get("payload", {})),
            status=d.get("status", RevisionStatus.PROPOSED),
            note=d.get("note", ""),
            created_at=d.get("created_at", ""),
            reverted_at=d.get("reverted_at"),
        )


@dataclass
class Annotation:
    fragment_id: str
    tokens: list[Token]
    annotated_at: str
    revision_basis: str | None = None  # 标注依据的修订ID（重判后更新）

    def to_dict(self) -> dict[str, Any]:
        return {
            "fragment_id": self.fragment_id,
            "tokens": [t.to_dict() for t in self.tokens],
            "annotated_at": self.annotated_at,
            "revision_basis": self.revision_basis,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Annotation":
        return cls(
            fragment_id=d["fragment_id"],
            tokens=[Token.from_dict(t) for t in d["tokens"]],
            annotated_at=d["annotated_at"],
            revision_basis=d.get("revision_basis"),
        )


@dataclass
class Fragment:
    """工单片段、题干预写或独立文本。raw_text 永不改写。"""
    id: str
    raw_text: str
    source: str                # work_order / question_stem / note ...
    business_context: str
    colleges: list[str] = field(default_factory=list)  # 适用院校；空表示全部
    note: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "raw_text": self.raw_text,
            "source": self.source,
            "business_context": self.business_context,
            "colleges": list(self.colleges),
            "note": self.note,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Fragment":
        return cls(
            id=d["id"],
            raw_text=d["raw_text"],
            source=d["source"],
            business_context=d["business_context"],
            colleges=list(d.get("colleges", [])),
            note=d.get("note", ""),
            created_at=d.get("created_at", ""),
        )


@dataclass
class Question:
    id: str
    stem_fragment_id: str
    ability: str
    context: str
    rubric: str = ""
    colleges: list[str] = field(default_factory=list)
    status: str = QuestionStatus.CANDIDATE
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "stem_fragment_id": self.stem_fragment_id,
            "ability": self.ability,
            "context": self.context,
            "rubric": self.rubric,
            "colleges": list(self.colleges),
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Question":
        return cls(
            id=d["id"],
            stem_fragment_id=d["stem_fragment_id"],
            ability=d["ability"],
            context=d["context"],
            rubric=d.get("rubric", ""),
            colleges=list(d.get("colleges", [])),
            status=d.get("status", QuestionStatus.CANDIDATE),
            created_at=d.get("created_at", ""),
        )


@dataclass
class EvidenceEdge:
    """两个片段之间的来源关系；只有 confirmed 边参与证据聚类。"""
    id: str
    left_id: str
    right_id: str
    relation: str
    same_evidence: bool
    status: str = EdgeStatus.SUGGESTED
    reason: str = ""
    reviewer: str = ""
    colleges: list[str] = field(default_factory=list)
    created_at: str = ""
    decided_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "left_id": self.left_id,
            "right_id": self.right_id,
            "relation": self.relation,
            "same_evidence": self.same_evidence,
            "status": self.status,
            "reason": self.reason,
            "reviewer": self.reviewer,
            "colleges": list(self.colleges),
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvidenceEdge":
        return cls(
            id=d["id"],
            left_id=d["left_id"],
            right_id=d["right_id"],
            relation=d["relation"],
            same_evidence=d["same_evidence"],
            status=d.get("status", EdgeStatus.SUGGESTED),
            reason=d.get("reason", ""),
            reviewer=d.get("reviewer", ""),
            colleges=list(d.get("colleges", [])),
            created_at=d.get("created_at", ""),
            decided_at=d.get("decided_at"),
        )


@dataclass
class CoverageCell:
    """问卷必考单元：能力 × 语境。"""
    id: str
    ability: str
    context: str
    required: bool = True
    colleges: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ability": self.ability,
            "context": self.context,
            "required": self.required,
            "colleges": list(self.colleges),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CoverageCell":
        return cls(
            id=d["id"],
            ability=d["ability"],
            context=d["context"],
            required=d.get("required", True),
            colleges=list(d.get("colleges", [])),
        )


@dataclass
class Plan:
    """一次问卷规划尝试（可能成功，也可能携带未解决冲突）。"""
    id: str
    college: str
    question_ids: list[str]
    covered_cells: list[str]
    conflicts: list[dict[str, Any]]
    success: bool
    status: str = PlanStatus.DRAFT
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "college": self.college,
            "question_ids": list(self.question_ids),
            "covered_cells": list(self.covered_cells),
            "conflicts": [dict(c) for c in self.conflicts],
            "success": self.success,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Plan":
        return cls(
            id=d["id"],
            college=d["college"],
            question_ids=list(d.get("question_ids", [])),
            covered_cells=list(d.get("covered_cells", [])),
            conflicts=[dict(c) for c in d["conflicts"]],
            success=d["success"],
            status=d.get("status", PlanStatus.DRAFT),
            created_at=d.get("created_at", ""),
        )


@dataclass
class DeliveryPackage:
    """不可变交付包：冻结时的词义、标注、证据分组一并快照。"""
    id: str
    kind: str                 # PackageKind
    college: str
    fragment_ids: list[str]
    question_ids: list[str]
    term_snapshot: list[dict[str, Any]]
    annotation_snapshot: list[dict[str, Any]]
    cluster_map: dict[str, str]
    plan_id: str | None
    note: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "college": self.college,
            "fragment_ids": list(self.fragment_ids),
            "question_ids": list(self.question_ids),
            "term_snapshot": [dict(t) for t in self.term_snapshot],
            "annotation_snapshot": [dict(a) for a in self.annotation_snapshot],
            "cluster_map": dict(self.cluster_map),
            "plan_id": self.plan_id,
            "note": self.note,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DeliveryPackage":
        return cls(
            id=d["id"],
            kind=d["kind"],
            college=d["college"],
            fragment_ids=list(d["fragment_ids"]),
            question_ids=list(d.get("question_ids", [])),
            term_snapshot=[dict(t) for t in d.get("term_snapshot", [])],
            annotation_snapshot=[dict(a) for a in d.get("annotation_snapshot", [])],
            cluster_map=dict(d.get("cluster_map", {})),
            plan_id=d.get("plan_id"),
            note=d.get("note", ""),
            created_at=d["created_at"],
        )


# ---------------------------------------------------------------------------
# 通用 JSON 化辅助
# ---------------------------------------------------------------------------

def to_jsonable(obj: Any) -> Any:
    """把 dataclass/枚举以外的普通结构转成可 json.dump 的形式（备用）。"""
    if is_dataclass(obj) and hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, list):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj


def model_fields(cls: type) -> list[str]:
    return [f.name for f in fields(cls)]
