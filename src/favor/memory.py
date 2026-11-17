"""The failure memory M of Sections 4.3-4.4.

One entry per failure class: e = (c, a, n, t_first, t_last) where `a` is a
representative attempt — the unified diff between the buggy method and the
first patch that fell into c, truncated — and n counts every patch of the
class, including L2 duplicates removed before validation.

Selection renders the k highest-scoring entries:

    score(e) = w1*z(log(1+n_e)) + w2*z(1/(1+t-t_last)) + w3*z(absent(e))

standardized over the current memory; absent(e)=1 when no attempt of class e
appears in the current conversation. Ties break by earlier t_first.
"""

from __future__ import annotations

import difflib
import math
import random
from dataclasses import dataclass, field

from .failure_class import FailureClass


@dataclass
class MemoryEntry:
    cls: FailureClass
    representative_diff: str
    result_line: str          # one-line description of how the class fails
    n: int
    t_first: int
    t_last: int


def make_diff(buggy_method: str, patch: str, max_lines: int) -> str:
    diff = list(
        difflib.unified_diff(
            buggy_method.splitlines(),
            patch.splitlines(),
            lineterm="",
            n=1,
        )
    )
    # drop the ---/+++ header, keep hunks
    diff = [d for d in diff if not d.startswith(("---", "+++"))]
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"... ({len(diff) - max_lines} more diff lines)"]
    return "\n".join(diff)


def result_line_for(cls: FailureClass) -> str:
    if cls.kind == "compile_error":
        return f"does not compile: {cls.detail[0]}"
    if cls.kind == "timeout":
        return "tests time out"
    if cls.kind == "tests":
        from .failure_class import FailureSignature

        sigs = [FailureSignature(*key) for key in cls.detail]
        first = sigs[0].summary()
        if len(sigs) > 1:
            return f"{first} (and {len(sigs) - 1} other failing test(s))"
        return first
    return "unknown outcome"


class FailureMemory:
    def __init__(self, diff_max_lines: int = 16,
                 score_weights: tuple[float, float, float] = (1.0, 1.0, 1.0)):
        self.entries: dict[tuple, MemoryEntry] = {}
        self.diff_max_lines = diff_max_lines
        self.w = score_weights

    def __len__(self) -> int:
        return len(self.entries)

    def clear(self) -> None:
        self.entries = {}

    def observe(self, cls: FailureClass, buggy_method: str, patch: str, turn: int) -> None:
        """Record one attempt of class `cls` at turn `turn`. A plausible patch
        never enters memory; unparseable output has no class (Section 4.3)."""
        if cls.kind in ("pass", "none"):
            return
        key = cls.key()
        e = self.entries.get(key)
        if e is None:
            self.entries[key] = MemoryEntry(
                cls=cls,
                representative_diff=make_diff(buggy_method, patch, self.diff_max_lines),
                result_line=result_line_for(cls),
                n=1,
                t_first=turn,
                t_last=turn,
            )
        else:
            e.n += 1
            e.t_last = max(e.t_last, turn)

    def inherit(self, cls: FailureClass, turn: int) -> None:
        """An L2 duplicate skips validation but counts toward its class."""
        key = cls.key()
        e = self.entries.get(key)
        if e is not None:
            e.n += 1
            e.t_last = max(e.t_last, turn)

    # --- selection -------------------------------------------------------------

    @staticmethod
    def _z(values: list[float]) -> list[float]:
        n = len(values)
        if n == 0:
            return []
        mean = sum(values) / n
        var = sum((v - mean) ** 2 for v in values) / n
        sd = math.sqrt(var)
        if sd == 0:
            return [0.0] * n
        return [(v - mean) / sd for v in values]

    def scored(self, turn: int, conversation_classes: set[tuple]) -> list[tuple[float, MemoryEntry]]:
        entries = list(self.entries.values())
        if not entries:
            return []
        freq = self._z([math.log(1 + e.n) for e in entries])
        rec = self._z([1.0 / (1 + turn - e.t_last) for e in entries])
        absent = self._z(
            [0.0 if e.cls.key() in conversation_classes else 1.0 for e in entries]
        )
        w1, w2, w3 = self.w
        scores = [
            w1 * f + w2 * r + w3 * a for f, r, a in zip(freq, rec, absent)
        ]
        return sorted(
            zip(scores, entries),
            key=lambda se: (-se[0], se[1].t_first),
        )

    def select(
        self,
        mode: str,
        k: int,
        turn: int,
        conversation_classes: set[tuple],
        rng: random.Random | None = None,
    ) -> list[MemoryEntry]:
        """mode: 'all' | 'topk' | 'random' (Table 5). When M holds k entries or
        fewer, all are shown."""
        if mode == "all":
            ranked = self.scored(turn, conversation_classes)
            return [e for _, e in ranked]  # every entry, ordered by score (4.4)
        if mode == "topk":
            ranked = self.scored(turn, conversation_classes)
            return [e for _, e in ranked[:k]]
        if mode == "random":
            entries = sorted(self.entries.values(), key=lambda e: e.t_first)
            if len(entries) <= k:
                return entries
            assert rng is not None, "random-k selection needs a seeded RNG"
            return rng.sample(entries, k)
        raise ValueError(f"unknown selection mode: {mode}")
