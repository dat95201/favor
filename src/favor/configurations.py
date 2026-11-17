"""Table 5: the twelve configurations. Each changes one component relative to
a neighbor, so every RQ4 contribution is measured by a single comparison."""

from __future__ import annotations

from .config import LoopConfig, Protocol


def table5(proto: Protocol) -> dict[str, LoopConfig]:
    t = proto.temperature
    m = proto.m
    rows = [
        # name            regime         memory  selection persist  m  vs        temp
        LoopConfig("IS", "independent", "none", "none", False, 1, "none", t),
        LoopConfig("IS+VS", "independent", "none", "none", False, m, "prob", t),
        LoopConfig("CR", "loop", "none", "none", False, 1, "none", t),
        LoopConfig("CR-hot", "loop", "none", "none", False, 1, "none", proto.hot_temperature),
        LoopConfig("CR+Mem", "loop", "full", "all", True, 1, "none", t),
        LoopConfig("CR+Rand", "loop", "full", "random", True, 1, "none", t),
        LoopConfig("CR+TopK", "loop", "full", "topk", True, 1, "none", t),
        LoopConfig("CR+TopK-reset", "loop", "full", "topk", False, 1, "none", t),
        LoopConfig("CR+VS", "loop", "none", "none", False, m, "prob", t),
        LoopConfig("CR+VS-noProb", "loop", "none", "none", False, m, "noprob", t),
        LoopConfig("CR+Mem+VS", "loop", "full", "all", True, m, "prob", t),
        LoopConfig("FaVoR", "loop", "full", "topk", True, m, "prob", t),
    ]
    return {c.name: c for c in rows}


CONFIRMATORY = ("CR", "FaVoR")   # Section 5.8: the confirmatory comparison
REFERENCE = ("IS",)              # context for RQ1/RQ3
