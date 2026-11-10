"""Scoring for masking and restore (baseline build 2026-11-09).

align(original, received): splits ``received`` (the text the provider got) into literal segments and gateway
placeholders ``<<TYPE_N>>``. Literal segments must appear in ``original`` in the same order; the text of ``original``
between two matched literals is what the placeholder(s) replaced. Returns the replaced character ranges of
``original`` with the placeholder types, or None if the literals cannot be matched in order (reported as
"unaligned", never guessed).

score_entities: per planted entity, "masked" (every character inside a replaced range), "partial" (some characters)
or "leaked" (none). Over-masking: replaced ranges that touch no planted entity.

restore_ok: the final answer equals the original text exactly.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

PLACEHOLDER_RE = re.compile(r"<<([A-Z][A-Z_]{1,15})_(\d{1,5})>>")
# any placeholder-like token left in a final answer (the gateway's own and the tolerated variants)
RESIDUAL_RE = re.compile(r"<<?[A-Z][A-Z_]{1,15}_\d{1,5}>>?|\[[A-Z][A-Z_]{1,15}_\d{1,5}\]|\b[A-Z][A-Z_]{1,15}_\d{1,5}\b")


@dataclass(frozen=True)
class Replaced:
    start: int
    end: int
    types: tuple[str, ...]   # placeholder types in this range (normally one)
    tokens: tuple[str, ...] = ()   # the placeholder tokens themselves, e.g. ("<<PERSON_1>>",)


def align(original: str, received: str) -> list[Replaced] | None:
    pieces: list[tuple[str, str]] = []   # (literal before, placeholder type)
    toks: list[str] = []
    pos = 0
    for m in PLACEHOLDER_RE.finditer(received):
        pieces.append((received[pos:m.start()], m.group(1)))
        toks.append(m.group(0))
        pos = m.end()
    tail = received[pos:]
    if not pieces:
        return [] if received == original else None
    # literals: L0, (P1) L1, (P2) L2, ... (Pk) tail
    literals = [p[0] for p in pieces] + [tail]
    types = [p[1] for p in pieces]
    if not original.startswith(literals[0]) or not original.endswith(tail):
        return None
    out: list[Replaced] = []
    cursor = len(literals[0])
    pending: list[str] = [types[0]]
    pending_tok: list[str] = [toks[0]]
    for k in range(1, len(literals)):
        lit = literals[k]
        if k == len(literals) - 1:
            idx = len(original) - len(lit)
            if idx < cursor:
                return None
        elif lit == "":
            pending.append(types[k])   # adjacent placeholders share one replaced range
            pending_tok.append(toks[k])
            continue
        else:
            idx = original.find(lit, cursor)
            if idx < 0:
                return None
        out.append(Replaced(cursor, idx, tuple(pending), tuple(pending_tok)))
        cursor = idx + len(lit)
        if k < len(types):
            pending = [types[k]]
            pending_tok = [toks[k]]
    return out


@dataclass(frozen=True)
class EntityScore:
    type: str
    fmt: str
    status: str            # masked | partial | leaked | unaligned
    placeholder_types: tuple[str, ...]
    placeholders: tuple[str, ...] = ()


def score_entities(original: str, received: str, entities: Sequence) -> tuple[list[EntityScore], list[Replaced], bool]:
    """Returns (per-entity scores, over-masked ranges, aligned?). ``entities`` have type, fmt, value, start, end."""
    ranges = align(original, received)
    if ranges is None:
        scores = [EntityScore(e.type, e.fmt, "leaked" if e.value in received else "unaligned", ()) for e in entities]
        return scores, [], False
    scores = []
    for e in entities:
        covered = 0
        ptypes: list[str] = []
        ptoks: list[str] = []
        for r in ranges:
            lo, hi = max(r.start, e.start), min(r.end, e.end)
            if hi > lo:
                covered += hi - lo
                ptypes.extend(r.types)
                ptoks.extend(r.tokens)
        length = e.end - e.start
        status = "masked" if covered >= length else ("partial" if covered > 0 else "leaked")
        scores.append(EntityScore(e.type, e.fmt, status, tuple(ptypes), tuple(ptoks)))
    over = [r for r in ranges if not any(min(r.end, e.end) > max(r.start, e.start) for e in entities)]
    return scores, over, True


def restore_ok(original: str, final: str) -> bool:
    return final == original


def residual_placeholders(text: str) -> list[str]:
    return RESIDUAL_RE.findall(text)


def to_variant(text: str, variant: str) -> str:
    """How the mock upstream rewrites gateway placeholders before answering (restore variants)."""
    if variant in ("exact", "stream_split"):
        return text
    fmt = {"single": "<{}_{}>", "brackets": "[{}_{}]", "bare": "{}_{}"}[variant]
    return PLACEHOLDER_RE.sub(lambda m: fmt.format(m.group(1), m.group(2)), text)
