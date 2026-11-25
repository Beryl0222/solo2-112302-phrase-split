"""词法标注：词界、词角色、业务语境与原字面位置。

设计要点：

- 所有 Token 携带半开区间 ``Span``，且 ``text == raw_text[start:end]``；
  归一化只写入 ``norm``，永不改写原文与偏移。
- 同一字面若对应多个词目/词义（如“主键回填”技能与“主键回填表”表名），
  依据片段业务语境与词义线索消歧，分别打上不同 ``term_ref``。
- 复合词内部再扫术语表，保留子词界（如“主键回填表”内含“主键回填”）。
- 单位、标点变体写入归一形，供同义识别时使用。
"""
from __future__ import annotations

import re

from .models import (
    Annotation,
    Fragment,
    Span,
    Term,
    Token,
    TokenRole,
    now_iso,
)

# 标点变体 -> 归一形
PUNCT_MAP: dict[str, str] = {
    "，": "comma", ",": "comma",
    "。": "period", ".": "period",
    "；": "semicolon", ";": "semicolon",
    "：": "colon", ":": "colon",
    "、": "enumeration",
    "？": "question", "?": "question",
    "！": "exclaim", "!": "exclaim",
    "（": "lparen", "(": "lparen",
    "）": "rparen", ")": "rparen",
    "“": "quote", "”": "quote", '"': "quote",
    "‘": "sqquote", "’": "sqquote", "'": "sqquote",
    "《": "book", "》": "book",
    "【": "bracket", "】": "bracket",
    "—": "dash", "–": "dash", "-": "dash",
    "…": "ellipsis",
    "·": "middle_dot", "|": "pipe",
}

# 计量单位（含常见变体归一）
UNITS: dict[str, str] = {
    "行": "行", "笔": "笔", "条": "条", "批": "批", "个": "个",
    "次": "次", "天": "天", "小时": "小时", "分钟": "分钟",
    "米": "米", "千克": "千克",
}

# 通用业务词（非术语词目，仅用于词界与角色标注）
GENERIC_LEXICON: list[str] = [
    "完整性约束", "工单", "批号", "样本表", "字段", "取数", "复跑",
    "核查时间", "核查", "表头", "菜单", "报表", "空值", "中间表",
    "数据库", "同步", "汇总", "题干", "问法", "观察量", "数据",
    "约束", "校验", "推迟", "现象", "前提", "说明", "工具",
]

_WHITESPACE = re.compile(r"\s+")
_LATIN_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CJK_DIGITS = "零〇一二三四五六七八九两"
# 数值（阿拉伯或中文数字，允许中间空白）后接可选计量单位
_NUM_PART = r"(?:\d+(?:\.\d+)?|[" + _CJK_DIGITS + r"十百千万]+)"
_UNIT_PART = "|".join(sorted(UNITS, key=len, reverse=True))
_QUANTITY = re.compile(_NUM_PART + r"(?:\s*(?:" + _UNIT_PART + r"))?")


def _normalize_digits(text: str) -> str:
    table = {
        "零": "0", "〇": "0",
        "一": "1", "二": "2", "两": "2", "三": "3", "四": "4",
        "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
    }
    return "".join(table.get(ch, ch) for ch in text)


def annotate_text(store, text: str, business_context: str = "") -> list[Token]:
    """对一段文本做规则标注，返回保留原偏移的 Token 序列。"""
    tokens: list[Token] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]

        # 1. 空白
        m = _WHITESPACE.match(text, i)
        if m:
            tokens.append(Token(text=m.group(0), span=Span(i, m.end()),
                                role=TokenRole.WHITESPACE))
            i = m.end()
            continue

        # 2. 标点（单字符）
        if ch in PUNCT_MAP:
            tokens.append(Token(text=ch, span=Span(i, i + 1),
                                role=TokenRole.PUNCT, norm=PUNCT_MAP[ch]))
            i += 1
            continue

        # 3. 拉丁标识符
        m = _LATIN_ID.match(text, i)
        if m:
            tokens.append(Token(text=m.group(0), span=Span(i, m.end()),
                                role=TokenRole.WORD))
            i = m.end()
            continue

        # 4. 数值（含中文数字）+ 可选单位，中间允许空白
        m = _QUANTITY.match(text, i)
        if m and m.group(0).strip():
            end = m.end()
            norm = _normalize_digits(re.sub(r"\s+", "", text[i:end]))
            tokens.append(Token(text=text[i:end], span=Span(i, end),
                                role=TokenRole.NUMBER_UNIT, norm=norm))
            i = end
            continue

        # 5. 术语词目（最长匹配，同形按语境消歧）
        term_hit = _match_term(store, text, i, business_context)
        if term_hit is not None:
            term, sense_id, end = term_hit
            tokens.append(_term_token(store, term, sense_id, text, i, end))
            i = end
            continue

        # 6. 通用业务词（最长匹配）
        generic = _match_generic(text, i)
        if generic is not None:
            end, word = generic
            tokens.append(Token(text=word, span=Span(i, end),
                                role=TokenRole.GENERIC))
            i = end
            continue

        # 7. 兜底：单字成词
        tokens.append(Token(text=ch, span=Span(i, i + 1), role=TokenRole.WORD))
        i += 1

    return tokens


def _match_term(store, text: str, start: int, business_context: str):
    """返回 (term, sense_id, end)；无命中返回 None。"""
    candidates: list[tuple[Term, str, int]] = []
    for term in store.all("terms"):
        for surface in term.all_surfaces():
            if text.startswith(surface, start):
                for sense in term.senses:
                    candidates.append((term, sense.sense_id, start + len(surface)))
    if not candidates:
        return None
    # 最长词面优先；同长度时按语境线索评分消歧
    max_end = max(c[2] for c in candidates)
    longest = [c for c in candidates if c[2] == max_end]
    if len(longest) == 1:
        return longest[0]

    window = text[start:max_end] + " " + business_context

    def score(cand: tuple[Term, str, int]) -> int:
        term, sense_id, _ = cand
        sense = term.sense(sense_id)
        gained = 0
        for cue in sense.contexts:
            if cue and cue in window:
                gained += 2
        # 词目自身的常用搭配作为弱线索
        for variant in term.variants:
            if variant and variant in window and variant != term.headword:
                gained += 1
        return gained

    scored = sorted(longest, key=lambda c: (score(c), c[0].active_sense_id == c[1]),
                    reverse=True)
    return scored[0]


def _term_token(store, term: Term, sense_id: str, text: str,
                start: int, end: int) -> Token:
    sense = term.sense(sense_id)
    token = Token(
        text=text[start:end],
        span=Span(start, end),
        role=TokenRole.TERM,
        term_ref=f"{term.id}#{sense_id}",
    )
    # 复合词内部词界：在词面内再扫其它术语词面
    for other in store.all("terms"):
        if other.id == term.id:
            continue
        for surface in other.all_surfaces():
            if len(surface) >= 2 and surface in token.text:
                rel = token.text.index(surface)
                sub = Span(start + rel, start + rel + len(surface))
                if sub not in token.components:
                    token.components.append(sub)
    token.components.sort(key=lambda s: s.start)
    # 角色描述附在 term_ref 可查；这里保持 token 精简，角色由词义决定
    _ = sense
    return token


def _match_generic(text: str, start: int):
    for word in sorted(GENERIC_LEXICON, key=len, reverse=True):
        if text.startswith(word, start):
            return start + len(word), word
    return None


def build_annotation(store, fragment: Fragment,
                     revision_basis: str | None = None) -> Annotation:
    tokens = annotate_text(store, fragment.raw_text, fragment.business_context)
    ann = Annotation(
        fragment_id=fragment.id,
        tokens=tokens,
        annotated_at=now_iso(),
        revision_basis=revision_basis,
    )
    existing = store.annotation_for(fragment.id)
    if existing is None:
        store.add("annotations", ann)
    else:
        store.upsert("annotations", ann)
    return ann


# ---------------------------------------------------------------- 读取辅助
def term_tokens(annotation: Annotation) -> list[Token]:
    return [t for t in annotation.tokens if t.role == TokenRole.TERM]


def quantities(annotation: Annotation) -> list[Token]:
    """观察量（数值+单位），用于判断“只复用观察量”的派生。"""
    return [t for t in annotation.tokens if t.role == TokenRole.NUMBER_UNIT]


def render_annotation(annotation: Annotation, raw_text: str) -> str:
    """生成带偏移与词义引用的可读标注行。"""
    lines = [f"片段 {annotation.fragment_id}（依据修订：{annotation.revision_basis or '无'}）"]
    for tok in annotation.tokens:
        if tok.role == TokenRole.WHITESPACE:
            continue
        prefix = f"  [{tok.span.start:>3}:{tok.span.end:<3}] {tok.role:<11} {tok.text!r}"
        extras = []
        if tok.norm is not None:
            extras.append(f"norm={tok.norm}")
        if tok.term_ref is not None:
            extras.append(f"term={tok.term_ref}")
        if tok.components:
            comps = [raw_text[s.start:s.end] for s in tok.components]
            extras.append(f"子词界={comps}")
        lines.append(prefix + ("  " + " ".join(extras) if extras else ""))
    return "\n".join(lines)


def verify_offsets(tokens: list[Token], raw_text: str) -> bool:
    """回归用：偏移必须严格覆盖原文、互不重叠且切片相等。"""
    cursor = 0
    for tok in tokens:
        if tok.span.start != cursor:
            return False
        if raw_text[tok.span.start:tok.span.end] != tok.text:
            return False
        cursor = tok.span.end
    return cursor == len(raw_text)
