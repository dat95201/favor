"""Offline end-to-end smoke run: the full Algorithm-1 loop over several
Table-5 configurations with a scripted fake model and a mock validator, then
the analysis pipeline over the produced logs. No API, no Defects4J. Asserts the
loop's invariants; exits non-zero on any violation."""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import ExperimentConfig, Protocol
from .configurations import table5
from .llm import FakeChatClient
from .loop import run_repair
from .mock import MockValidator, mock_bug
from .storage import JsonlWriter, read_jsonl, write_json

A = """public int add(int a, int b) {
    return a - b;
}"""
# whitespace/comment variant of A: same L1, same L2 -> must be filtered
A2 = """public int add(int a, int b) {
        // try subtracting
        return a   -   b;
}"""
# renamed-local variant of B: different L1, same L2 under alpha renaming
B = """public int add(int a, int b) {
    int result = b - a;
    return result;
}"""
B2 = """public int add(int a, int b) {
    int total = b - a;
    return total;
}"""
C = """public int add(int a, int b) {
    return a * b;
}"""
E = """public int add(int a, int b) {
    int x = COMPILE_ERROR;
    return 0;
}"""
FIX = """public int add(int a, int b) {
    return a + b;
}"""

POOL = [A, A2, B, B2, C, E, FIX]


def script(messages: list[dict], idx: int) -> str:
    last = messages[-1]["content"]
    if "propose" in last:  # a Verbalized Sampling turn: m fixes with probabilities
        chunk = POOL[idx * 3 : idx * 3 + 3] or [FIX]
        parts = []
        for i, code in enumerate(chunk):
            parts.append(f"Fix {i + 1}:\n```java\n{code}\n```\nProbability: 0.{3 + i}")
        return "\n\n".join(parts)
    code = POOL[min(idx, len(POOL) - 1)]
    return f"Here is a possible fix:\n```java\n{code}\n```"


def run_smoke(out_dir: str | Path) -> int:
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    proto = Protocol(budget_b=8, runs_per_bug=1, k=2, m=3, flaky_runs=1)
    confs = table5(proto)
    chosen = ["IS", "IS+VS", "CR", "CR+TopK", "CR+TopK-reset", "CR+VS", "FaVoR"]
    exp = ExperimentConfig(protocol=proto, configurations=tuple(chosen))
    bug = mock_bug()
    write_json(out / "experiment.json", {**exp.dump(), "model": "fake", "bugs": [bug.key]})

    patch_log = JsonlWriter(out / "patches.jsonl")
    call_log = JsonlWriter(out / "calls.jsonl")
    run_log = JsonlWriter(out / "runs.jsonl")

    failures: list[str] = []
    print(f"{'config':<16} {'plausible':<10} {'validated':<10} {'generated':<10} "
          f"{'repeats':<8} {'turns':<6} convs")
    for name in chosen:
        cfg = confs[name]
        llm = FakeChatClient(script)
        validator = MockValidator()
        rec = run_repair(bug, cfg, proto, llm, validator, run_idx=0,
                         patch_log=patch_log, call_log=call_log)
        run_log.write(rec.to_json())
        patches = [p for p in read_jsonl(out / "patches.jsonl")
                   if p["config"] == name]
        repeats = sum(1 for p in patches if p["status"] == "repeat")
        print(f"{name:<16} {str(rec.plausible_found):<10} {rec.validated_count:<10} "
              f"{rec.generated_count:<10} {repeats:<8} {rec.turns:<6} {rec.conversations}")

        # ---- invariants -------------------------------------------------------
        if rec.validated_count > proto.budget_b:
            failures.append(f"{name}: budget exceeded ({rec.validated_count} > {proto.budget_b})")
        if not rec.plausible_found:
            failures.append(f"{name}: scripted fix never found")
        # A2 duplicates A (L1) and B2 duplicates B (L2): single-patch loop
        # configurations must have filtered at least one of each kind
        if cfg.m == 1 and repeats < 1:
            failures.append(f"{name}: no L2 repeat was filtered")
        if any(p["status"] == "plausible" and p["patch_index"] != rec.first_plausible_patch_index
               for p in patches):
            failures.append(f"{name}: plausible patch index mismatch")
        validated_after_plausible = [
            p for p in patches
            if rec.first_plausible_patch_index is not None
            and p["patch_index"] > rec.first_plausible_patch_index and p["validated"]
        ]
        if validated_after_plausible:
            failures.append(f"{name}: validation continued after the first plausible patch")
        if cfg.regime == "loop" and cfg.memory == "full" and cfg.persist:
            if rec.conversations > 1:
                post_restart = [p for p in patches if p["conv_id"] > 0]
                if post_restart and not any(p["memory_classes_all"] for p in post_restart):
                    failures.append(f"{name}: memory did not persist across the restart")
        if cfg.name == "CR+TopK-reset" and rec.conversations > 1:
            first_of_conv1 = next((p for p in patches if p["conv_id"] == 1), None)
            if first_of_conv1 and first_of_conv1["memory_classes_all"]:
                failures.append("CR+TopK-reset: memory survived a restart")
        if cfg.vs == "prob":
            with_prob = [p for p in patches if p.get("probability") is not None]
            if not with_prob:
                failures.append(f"{name}: no verbalized probability was parsed")

    patch_log.close()
    call_log.close()
    run_log.close()

    print()
    from .analyze import analyze

    analyze(out, out / "analysis")

    if failures:
        print("\nSMOKE FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nsmoke: all invariants hold")
    return 0
