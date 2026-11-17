"""Parse LLM responses into candidate patches (Section 4.5).

A response yields at most m methods, in the order the model lists them. A
block that does not parse into exactly one Java method has no class, costs no
validation and does not count toward the budget. Verbalized probabilities are
logged but never used to choose or order patches."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .java_ast import extract_single_method

_FENCE = re.compile(r"```(?:java)?\s*\n(.*?)```", re.S | re.I)
_PROB = re.compile(r"probability\s*[:=]?\s*(?P<p>[0-9]*\.?[0-9]+)\s*%?", re.I)


@dataclass(frozen=True)
class ParsedPatch:
    code: str                 # the method text (parse-verified)
    raw_block: str            # the fenced block as returned
    probability: float | None
    parse_ok: bool


def _probability_near(text: str, block_end: int, next_block_start: int) -> float | None:
    """Probability stated between this block and the next one (or shortly after)."""
    window = text[block_end : next_block_start if next_block_start >= 0 else block_end + 200]
    m = _PROB.search(window)
    if not m:
        return None
    try:
        p = float(m.group("p"))
    except ValueError:
        return None
    if "%" in window[m.start() : m.end() + 1] or p > 1.0:
        p = p / 100.0
    return p if 0.0 <= p <= 1.0 else None


def parse_response(text: str, m: int) -> list[ParsedPatch]:
    """Extract up to m candidate methods, in model order."""
    out: list[ParsedPatch] = []
    matches = list(_FENCE.finditer(text))
    if not matches:
        # no fences: try the whole reply as one method
        method = extract_single_method(text)
        if method is not None:
            out.append(ParsedPatch(code=method, raw_block=text, probability=None, parse_ok=True))
        else:
            out.append(ParsedPatch(code="", raw_block=text, probability=None, parse_ok=False))
        return out[:m]

    for i, match in enumerate(matches):
        if len([p for p in out if p.parse_ok]) >= m:
            break
        block = match.group(1)
        next_start = matches[i + 1].start() if i + 1 < len(matches) else -1
        prob = _probability_near(text, match.end(), next_start)
        method = extract_single_method(block)
        if method is None:
            out.append(ParsedPatch(code="", raw_block=block, probability=prob, parse_ok=False))
        else:
            out.append(ParsedPatch(code=method, raw_block=block, probability=prob, parse_ok=True))
    return out
