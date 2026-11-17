"""Patch validation (Section 5.5, local protocol).

Each run gets its own checkout of the buggy version. Validating a patch means:
replace the buggy method's span with the candidate, `defects4j compile`, then
`defects4j test -r` (every relevant test — the L4 class needs the complete set
of failing tests), with a timeout derived from the fixed version's run time.
The original file is restored after every candidate, so the method span stays
valid for the whole run."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import d4j
from .config import Protocol
from .failure_class import (
    PASS,
    TIMEOUT,
    FailureClass,
    FailureSignature,
    class_for_compile_error,
    class_for_failures,
    normalize_compiler_error,
    parse_failing_tests,
)
from .records import BugRecord


@dataclass
class Outcome:
    status: str                      # "plausible" | "fail" | "compile_error" | "timeout"
    cls: FailureClass
    signatures: list[FailureSignature] = field(default_factory=list)
    compile_error: str | None = None
    compile_s: float = 0.0
    test_s: float = 0.0

    @property
    def total_s(self) -> float:
        return self.compile_s + self.test_s

    def still_fails_trigger(self, trigger_ids: set[str]) -> bool:
        return any(s.test_id in trigger_ids for s in self.signatures)


class Validator:
    def __init__(self, bug: BugRecord, proto: Protocol, workdir: str | Path):
        self.bug = bug
        self.proto = proto
        self.workdir = Path(workdir)
        self._original: bytes | None = None
        self.test_timeout = max(
            proto.test_timeout_min_s, proto.test_timeout_factor * bug.fixed_test_time_s
        )

    # -- lifecycle ---------------------------------------------------------------

    def prepare(self) -> None:
        ok = d4j.checkout(self.bug.project, self.bug.bug_id, "b", self.workdir)
        if not ok:
            raise RuntimeError(f"checkout failed for {self.bug.key}")
        src = self.file_path.read_bytes()
        span = src[self.bug.start_byte : self.bug.end_byte].decode("utf8", "replace")
        if span.strip() != self.bug.method_src.strip():
            raise RuntimeError(
                f"{self.bug.key}: method span does not match the recorded buggy method; "
                "re-run `favor select-bugs` for this bug"
            )
        self._original = src

    @property
    def file_path(self) -> Path:
        return self.workdir / self.bug.file_rel

    def restore(self) -> None:
        if self._original is not None:
            self.file_path.write_bytes(self._original)

    def cleanup(self) -> None:
        d4j.cleanup(self.workdir)

    # -- validation ---------------------------------------------------------------

    def apply(self, method_code: str) -> None:
        assert self._original is not None, "prepare() first"
        patched = (
            self._original[: self.bug.start_byte]
            + method_code.encode("utf8")
            + self._original[self.bug.end_byte :]
        )
        self.file_path.write_bytes(patched)

    def validate(self, method_code: str) -> Outcome:
        self.apply(method_code)
        try:
            comp = d4j.compile_project(self.workdir)
            if not comp.ok:
                return Outcome(
                    status="compile_error",
                    cls=class_for_compile_error(comp.error_text),
                    compile_error=normalize_compiler_error(comp.error_text),
                    compile_s=comp.wall_s,
                )
            tr = d4j.run_tests(self.workdir, relevant_only=True, timeout=self.test_timeout)
            if tr.timed_out:
                return Outcome(
                    status="timeout", cls=TIMEOUT, compile_s=comp.wall_s, test_s=tr.wall_s
                )
            if not tr.ok:
                # the test build broke: treat as a compile-class failure of the test phase
                return Outcome(
                    status="compile_error",
                    cls=class_for_compile_error(tr.failing_tests_text or "test build failed"),
                    compile_error="test build failed",
                    compile_s=comp.wall_s,
                    test_s=tr.wall_s,
                )
            if tr.n_failing == 0:
                return Outcome(
                    status="plausible", cls=PASS, compile_s=comp.wall_s, test_s=tr.wall_s
                )
            sigs = parse_failing_tests(
                tr.failing_tests_text, stack_frames=self.proto.signature_stack_frames
            )
            return Outcome(
                status="fail",
                cls=class_for_failures(sigs),
                signatures=sigs,
                compile_s=comp.wall_s,
                test_s=tr.wall_s,
            )
        finally:
            self.restore()
