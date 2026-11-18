"""Typed records for bugs, patches, LLM calls and runs. Everything is stored
as JSONL so every analysis re-derives from the raw logs (Section 5.5: per-patch
generation and validation logs are part of the release)."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class TriggerTest:
    test_id: str                      # package.Class::method
    error_message: str                # first line of the trace
    failing_line_src: str | None      # source line in the test, when located
    location: str | None              # "ClassTest.java:42"

    @staticmethod
    def from_json(d: dict) -> "TriggerTest":
        return TriggerTest(**d)


@dataclass(frozen=True)
class BugRecord:
    project: str
    bug_id: int
    modified_class: str
    file_rel: str                     # buggy source file, relative to checkout
    method_name: str
    method_params: str
    n_params: int
    start_byte: int                   # method span in the buggy file
    end_byte: int
    start_line: int
    end_line: int
    method_src: str                   # buggy method (perfect fault localization)
    fixed_method_src: str             # developer fix (correctness labeling aid)
    trigger_tests: tuple[TriggerTest, ...]
    fixed_test_time_s: float          # relevant-test wall time on the fixed version
    buggy_compile_s: float
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.project}-{self.bug_id}"

    def to_json(self) -> dict:
        d = asdict(self)
        d["trigger_tests"] = [asdict(t) for t in self.trigger_tests]
        return d

    @staticmethod
    def from_json(d: dict) -> "BugRecord":
        d = dict(d)
        d["trigger_tests"] = tuple(TriggerTest.from_json(t) for t in d["trigger_tests"])
        return BugRecord(**d)


@dataclass
class PatchRecord:
    """One generated patch (including L2 repeats, unparseable blocks and
    budget-exhausted leftovers), in generation order within a run."""

    bug: str
    config: str
    run_idx: int
    patch_index: int                  # order within the run
    turn: int                         # LLM call number within the run (1-based)
    conv_id: int                      # conversation number (increments at restart)
    within_call_index: int
    parse_ok: bool
    code: str
    probability: float | None
    l1: str | None
    l2: str | None
    l2_duplicate_of: int | None       # patch_index of the first same-L2 patch
    validated: bool
    status: str                       # plausible|fail|compile_error|timeout|unparseable|repeat|budget_exhausted
    l4: dict | None                   # FailureClass.to_json()
    class_hash: str | None
    signatures: list[dict] = field(default_factory=list)
    compile_error: str | None = None
    compile_s: float = 0.0
    test_s: float = 0.0
    budget_used_after: int = 0
    # origin-analysis context, captured at generation time (Section 5.7)
    context_patch_indexes: list[int] = field(default_factory=list)
    feedback_named_tests: list[str] = field(default_factory=list)  # filled after feedback
    feedback_line: str | None = None
    memory_classes_all: list[str] = field(default_factory=list)
    memory_classes_selected: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class CallRecord:
    bug: str
    config: str
    run_idx: int
    turn: int
    conv_id: int
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    cached: bool
    n_parsed: int
    n_parse_ok: int

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class RunRecord:
    bug: str
    config: str
    run_idx: int
    seed: int
    plausible_found: bool
    first_plausible_patch_index: int | None
    plausible_code: str | None
    stopped_reason: str               # "plausible" | "budget" | "call_cap"
    validated_count: int              # patches that consumed budget
    validated_to_first_plausible: int | None  # == validated_count on success
    generated_count: int
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    gen_latency_s: float
    compile_s_total: float
    test_s_total: float
    turns: int
    conversations: int                # 1 + restarts
    wall_s: float

    def to_json(self) -> dict:
        return asdict(self)
