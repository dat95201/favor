"""Single-function bug detection and BugRecord construction (Section 5.2).

A bug qualifies when the developer fix changes exactly one method: every
changed line of the modified source file falls inside one method in the buggy
version and one method of the same name and arity in the fixed version.
Changes outside any method body (imports, fields, javadoc) disqualify the bug.
As in most LLM-based studies we assume perfect fault localization: the buggy
method is given."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from . import d4j
from .failure_class import parse_failing_tests
from .java_ast import MethodInfo, method_containing_line, methods_in_source
from .records import BugRecord, TriggerTest


@dataclass
class Rejection(Exception):
    step: str      # Table-4 step that removed the bug
    reason: str

    def __str__(self) -> str:
        return f"[{self.step}] {self.reason}"


def _changed_lines(a: list[str], b: list[str]) -> tuple[set[int], set[int]]:
    """0-based changed line indexes in a (buggy) and b (fixed); blank lines are
    ignored; for pure insertions the anchoring buggy line is included."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ca: set[int] = set()
    cb: set[int] = set()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        ca.update(i for i in range(i1, i2) if a[i].strip())
        cb.update(j for j in range(j1, j2) if b[j].strip())
        if tag == "insert" and i1 > 0:
            ca.add(i1 - 1)
    return ca, cb


def _single_changed_method(
    buggy_src: str, fixed_src: str
) -> tuple[MethodInfo, MethodInfo]:
    ca, cb = _changed_lines(buggy_src.splitlines(), fixed_src.splitlines())
    if not ca and not cb:
        raise Rejection("single-function", "no source change found")
    b_methods = methods_in_source(buggy_src)
    f_methods = methods_in_source(fixed_src)

    hit_b: dict[int, MethodInfo] = {}
    for line in ca:
        m = method_containing_line(b_methods, line)
        if m is None:
            raise Rejection("single-function", f"buggy change outside any method (line {line + 1})")
        hit_b[m.start_byte] = m
    hit_f: dict[int, MethodInfo] = {}
    for line in cb:
        m = method_containing_line(f_methods, line)
        if m is None:
            raise Rejection("single-function", f"fixed change outside any method (line {line + 1})")
        hit_f[m.start_byte] = m

    if len(hit_b) != 1 or len(hit_f) != 1:
        raise Rejection(
            "single-function",
            f"fix touches {sorted(m.signature for m in hit_b.values())} in buggy "
            f"and {sorted(m.signature for m in hit_f.values())} in fixed",
        )
    mb = next(iter(hit_b.values()))
    mf = next(iter(hit_f.values()))
    if mb.signature != mf.signature:
        raise Rejection(
            "single-function", f"method identity changed: {mb.signature} -> {mf.signature}"
        )
    return mb, mf


def _class_source_path(workdir: Path, fqcn: str) -> tuple[str, Path]:
    src_dir = d4j.export(workdir, "dir.src.classes")
    rel = f"{src_dir}/{fqcn.replace('.', '/')}.java"
    return rel, workdir / rel


def _trigger_details(
    workdir_buggy: Path, pid: str, bid: int, stack_frames: int
) -> list[TriggerTest]:
    """Trigger tests with error message, location and the failing test line's
    source, parsed from the Defects4J metadata (same format as failing_tests)."""
    meta = d4j.trigger_tests_meta(pid, bid)
    meta_file = d4j.P.D4J_PROJECTS / pid / "trigger_tests" / str(bid)
    text = meta_file.read_text(errors="replace") if meta_file.exists() else ""
    sigs = {s.test_id: s for s in parse_failing_tests(text, stack_frames=stack_frames)}
    test_dir = d4j.export(workdir_buggy, "dir.src.tests")

    out: list[TriggerTest] = []
    for test_id, error_line in meta:
        sig = sigs.get(test_id)
        location = sig.location if sig else None
        failing_src = d4j.test_line_source(workdir_buggy, test_dir, test_id, location)
        out.append(
            TriggerTest(
                test_id=test_id,
                error_message=error_line,
                failing_line_src=failing_src,
                location=location,
            )
        )
    return out


def analyze_bug(
    pid: str,
    bid: int,
    workbase: Path,
    stack_frames: int = 1,
    slow_suite_limit_s: float = 600.0,
    flaky_runs: int = 5,
) -> BugRecord:
    """Run the Table-4 screens on one bug; raise Rejection with the step that
    removed it, or return the BugRecord. Expensive: checks out and builds both
    versions and runs the relevant tests (flaky_runs+1) times."""
    wb = workbase / f"{pid}-{bid}b"
    wf = workbase / f"{pid}-{bid}f"
    try:
        # fast metadata screen first: a multi-file patch is never single-function
        if d4j.patch_file_count(pid, bid) > 1 or len(d4j.modified_classes(pid, bid)) != 1:
            raise Rejection("single-function", "fix touches more than one class")
        fqcn = d4j.modified_classes(pid, bid)[0]

        if not d4j.checkout(pid, bid, "b", wb):
            raise Rejection("build", "buggy checkout failed")
        if not d4j.checkout(pid, bid, "f", wf):
            raise Rejection("build", "fixed checkout failed")

        rel, path_b = _class_source_path(wb, fqcn)
        _, path_f = _class_source_path(wf, fqcn)
        if not path_b.exists() or not path_f.exists():
            raise Rejection("single-function", f"source file not found: {rel}")
        buggy_src = path_b.read_text(errors="replace")
        fixed_src = path_f.read_text(errors="replace")
        mb, mf = _single_changed_method(buggy_src, fixed_src)

        comp_b = d4j.compile_project(wb)
        if not comp_b.ok:
            raise Rejection("build", f"buggy version does not compile: {comp_b.error_text[:200]}")
        comp_f = d4j.compile_project(wf)
        if not comp_f.ok:
            raise Rejection("build", f"fixed version does not compile: {comp_f.error_text[:200]}")

        # fixed version must pass its relevant tests, within the slow-suite limit
        tr_f = d4j.run_tests(wf, relevant_only=True, timeout=max(1800.0, slow_suite_limit_s * 2))
        if tr_f.timed_out or not tr_f.ok:
            raise Rejection("fixed-fails", "relevant tests did not run to completion on the fixed version")
        if tr_f.n_failing != 0:
            raise Rejection("fixed-fails", f"{tr_f.n_failing} relevant test(s) fail on the fixed version")
        if tr_f.wall_s > slow_suite_limit_s:
            raise Rejection("slow-suite", f"relevant tests take {tr_f.wall_s:.0f}s on the fixed version")

        # flakiness screen: identical failing set on the buggy version, all-pass
        # on the fixed version, across `flaky_runs` runs (Section 5.2)
        timeout = max(1800.0, 5 * tr_f.wall_s)
        baseline_failing: frozenset[str] | None = None
        for i in range(flaky_runs):
            tr_b = d4j.run_tests(wb, relevant_only=True, timeout=timeout)
            if tr_b.timed_out or not tr_b.ok:
                raise Rejection("flaky", f"buggy run {i + 1} did not complete")
            failing = frozenset(
                s.test_id for s in parse_failing_tests(tr_b.failing_tests_text)
            )
            if not failing:
                raise Rejection("flaky", f"buggy run {i + 1} had no failing relevant test")
            if baseline_failing is None:
                baseline_failing = failing
            elif failing != baseline_failing:
                raise Rejection("flaky", "failing set differs across buggy runs")
            if i > 0:  # first fixed run already done above
                tr_f2 = d4j.run_tests(wf, relevant_only=True, timeout=timeout)
                if tr_f2.timed_out or not tr_f2.ok or tr_f2.n_failing != 0:
                    raise Rejection("flaky", f"fixed run {i + 1} failed or did not complete")

        triggers = _trigger_details(wb, pid, bid, stack_frames)
        if not triggers:
            raise Rejection("single-function", "no trigger test metadata")

        return BugRecord(
            project=pid,
            bug_id=bid,
            modified_class=fqcn,
            file_rel=rel,
            method_name=mb.name,
            method_params=mb.params,
            n_params=mb.n_params,
            start_byte=mb.start_byte,
            end_byte=mb.end_byte,
            start_line=mb.start_line,
            end_line=mb.end_line,
            method_src=mb.src,
            fixed_method_src=mf.src,
            trigger_tests=tuple(triggers),
            fixed_test_time_s=round(tr_f.wall_s, 2),
            buggy_compile_s=round(comp_b.wall_s, 2),
        )
    finally:
        d4j.cleanup(wb)
        d4j.cleanup(wf)
