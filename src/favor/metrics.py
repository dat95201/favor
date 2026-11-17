"""Metrics of Section 5.7: duplicate rates DR_L(n) and accumulation curves
S_L(n), wasted-validation shares W_L, and the cost to the first plausible patch
(censored at B). All metrics are re-derived from the per-patch JSONL logs.

L1/L2 are computed over generated patches (parse-verified ones), L4 over
validated patches; an L2-filtered repeat inherits the L4 class of the patch it
duplicates, so every generated patch has all levels defined. L3 requires a
generated test suite T_gen and is reported only when a T_gen adapter has
produced `l3` keys (see semantics.py)."""

from __future__ import annotations

from collections import defaultdict

GENERATED = ("plausible", "fail", "compile_error", "timeout", "repeat")
VALIDATED = ("plausible", "fail", "compile_error", "timeout")


def runs_from_patches(patches: list[dict]) -> dict[tuple, list[dict]]:
    """Group patch records by run and order them by patch_index."""
    runs: dict[tuple, list[dict]] = defaultdict(list)
    for p in patches:
        runs[(p["bug"], p["config"], p["run_idx"])].append(p)
    for key in runs:
        runs[key].sort(key=lambda p: p["patch_index"])
    return runs


def level_sequence(run_patches: list[dict], level: str) -> list[str]:
    """The ordered class keys of one run at a level ('l1', 'l2', 'l4')."""
    keys: list[str] = []
    for p in run_patches:
        if level in ("l1", "l2"):
            if p["status"] in GENERATED and p.get(level):
                keys.append(p[level])
        elif level == "l4":
            if p["status"] in VALIDATED and p.get("class_hash"):
                keys.append(p["class_hash"])
        elif level == "l3":
            if p["status"] in VALIDATED and p.get("l3"):
                keys.append(p["l3"])
    return keys


def s_curve(keys: list[str]) -> list[int]:
    """S_L(n): distinct classes among the first n patches, for n=1..len."""
    seen: set[str] = set()
    out: list[int] = []
    for k in keys:
        seen.add(k)
        out.append(len(seen))
    return out


def duplicate_rate(keys: list[str]) -> float | None:
    """DR_L(n) = 1 - S_L(n)/n at n = len(keys)."""
    if not keys:
        return None
    return 1.0 - len(set(keys)) / len(keys)


def duplicate_flags(keys: list[str]) -> list[bool]:
    seen: set[str] = set()
    flags: list[bool] = []
    for k in keys:
        flags.append(k in seen)
        seen.add(k)
    return flags


def wasted_validation(run_patches: list[dict], level: str = "l4") -> float | None:
    """W_L: share of validation time spent on patches that repeat an earlier
    class at the level. Defined over validated patches (oracle waste for L4)."""
    validated = [p for p in run_patches if p["status"] in VALIDATED]
    if not validated:
        return None
    if level == "l4":
        keys = [p.get("class_hash") or "" for p in validated]
    else:
        keys = [p.get(level) or "" for p in validated]
    flags = duplicate_flags(keys)
    costs = [p.get("compile_s", 0.0) + p.get("test_s", 0.0) for p in validated]
    total = sum(costs)
    if total <= 0:
        return None
    return sum(c for c, f in zip(costs, flags) if f) / total


def cost_to_first_plausible(run_record: dict, budget_b: int) -> tuple[int, bool]:
    """(validated patches to first plausible, censored?). A run without a
    plausible patch is censored at B (Section 5.7)."""
    if run_record["plausible_found"]:
        return run_record["validated_to_first_plausible"], False
    return budget_b, True


def survival_curve(run_records: list[dict], budget_b: int) -> list[float]:
    """Fraction of runs with a plausible patch within b validated patches,
    for b = 1..B."""
    n = len(run_records)
    if n == 0:
        return [0.0] * budget_b
    out = []
    for b in range(1, budget_b + 1):
        hit = sum(
            1
            for r in run_records
            if r["plausible_found"] and r["validated_to_first_plausible"] <= b
        )
        out.append(hit / n)
    return out


def mean_s_curve(curves: list[list[int]], horizon: int) -> list[float]:
    """Average S_L(n) over runs up to a common horizon; a run shorter than the
    horizon holds its final value (its class count no longer grows)."""
    if not curves:
        return []
    out = []
    for n in range(horizon):
        vals = [c[n] if n < len(c) else (c[-1] if c else 0) for c in curves]
        out.append(sum(vals) / len(vals))
    return out


def config_summary(
    run_records: list[dict], patches_by_run: dict[tuple, list[dict]], budget_b: int
) -> dict:
    """Headline numbers for one configuration (RQ2/RQ4)."""
    import statistics as st

    by_bug: dict[str, list[dict]] = defaultdict(list)
    for r in run_records:
        by_bug[r["bug"]].append(r)
    repair_per_bug = {
        bug: sum(1 for r in rs if r["plausible_found"]) / len(rs)
        for bug, rs in by_bug.items()
    }
    costs = [cost_to_first_plausible(r, budget_b)[0] for r in run_records]
    censored = sum(1 for r in run_records if not r["plausible_found"])

    dr = {}
    wasted = []
    for key, pts in patches_by_run.items():
        for level in ("l1", "l2", "l4"):
            seq = level_sequence(pts, level)
            d = duplicate_rate(seq)
            if d is not None:
                dr.setdefault(level, []).append(d)
        w = wasted_validation(pts, "l4")
        if w is not None:
            wasted.append(w)

    repaired_bugs = sum(1 for v in repair_per_bug.values() if v > 0)
    total_tokens = sum(r["prompt_tokens"] + r["completion_tokens"] for r in run_records)
    total_calls = sum(r["llm_calls"] for r in run_records)
    total_validations = sum(r["validated_count"] for r in run_records)

    return {
        "runs": len(run_records),
        "bugs": len(by_bug),
        "repair_rate_plausible": round(st.mean(repair_per_bug.values()), 4) if repair_per_bug else None,
        "bugs_with_plausible": repaired_bugs,
        "mean_cost_to_first_plausible": round(st.mean(costs), 3) if costs else None,
        "censored_runs": censored,
        "dr_l1": round(st.mean(dr["l1"]), 4) if dr.get("l1") else None,
        "dr_l2": round(st.mean(dr["l2"]), 4) if dr.get("l2") else None,
        "dr_l4": round(st.mean(dr["l4"]), 4) if dr.get("l4") else None,
        "wasted_validation_l4": round(st.mean(wasted), 4) if wasted else None,
        "tokens_per_repaired_bug": round(total_tokens / repaired_bugs) if repaired_bugs else None,
        "calls_per_repaired_bug": round(total_calls / repaired_bugs, 1) if repaired_bugs else None,
        "validations_per_repaired_bug": round(total_validations / repaired_bugs, 1) if repaired_bugs else None,
    }
