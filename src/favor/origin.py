"""Origin analysis of L4 (test-equivalent) repeats (Section 5.7).

For each L4 repeat p_i in a conversational run, with p_j the earliest earlier
patch of the same class:

  1. in_context_reported   — p_j is in the current conversation and its
                             feedback names a failing test of the class (for
                             compile-error and timeout classes, the feedback
                             line describes the class, which counts as
                             reported);
  2. in_context_unreported — p_j is in the current conversation but its
                             feedback names none of the class's failing tests;
  3. lost_at_restart       — p_j belongs to an earlier conversation. For
                             configurations with a memory this splits into
                             in_memory_selected / in_memory_not_selected /
                             not_in_memory (the last arises when the memory was
                             cleared by the reset ablation).

H1 predicts origins 2-3 dominate in the baseline; H2 predicts origin 1
persists. Origins are undefined for independent sampling."""

from __future__ import annotations

from collections import Counter

ORIGINS = (
    "in_context_reported",
    "in_context_unreported",
    "lost_at_restart",
    "in_memory_selected",
    "in_memory_not_selected",
    "not_in_memory",
)


def _class_test_ids(l4: dict | None) -> set[str]:
    if not l4 or l4.get("kind") != "tests":
        return set()
    return {sig[0] for sig in l4.get("detail", [])}


def classify_run(run_patches: list[dict], has_memory: bool) -> list[dict]:
    """One record per L4 repeat: {patch_index, earliest_index, origin, split}."""
    first_by_class: dict[str, dict] = {}
    out: list[dict] = []
    for p in sorted(run_patches, key=lambda p: p["patch_index"]):
        ch = p.get("class_hash")
        if not ch:
            continue
        earliest = first_by_class.get(ch)
        if earliest is None:
            first_by_class[ch] = p
            continue

        in_context = earliest["patch_index"] in p.get("context_patch_indexes", [])
        if in_context:
            class_tests = _class_test_ids(p.get("l4"))
            named = set(earliest.get("feedback_named_tests") or [])
            if class_tests:
                reported = bool(class_tests & named)
            else:
                # compile-error / timeout class: the feedback line, when present,
                # states the class itself
                reported = bool(earliest.get("feedback_line"))
            origin = "in_context_reported" if reported else "in_context_unreported"
        else:
            origin = "lost_at_restart"

        split = None
        if origin == "lost_at_restart" and has_memory:
            if ch in (p.get("memory_classes_selected") or []):
                split = "in_memory_selected"
            elif ch in (p.get("memory_classes_all") or []):
                split = "in_memory_not_selected"
            else:
                split = "not_in_memory"

        out.append(
            {
                "bug": p["bug"],
                "config": p["config"],
                "run_idx": p["run_idx"],
                "patch_index": p["patch_index"],
                "earliest_index": earliest["patch_index"],
                "origin": origin,
                "memory_split": split,
            }
        )
    return out


def opportunity_events(run_patches: list[dict]) -> list[dict]:
    """Secondary analysis: origin shares normalized by opportunity. After a
    few restarts most prior classes are hidden, so hidden origins dominate
    even if visibility has no effect; this instrument conditions on how many
    distinct prior classes were visible vs hidden when each patch was
    generated. One event per classed patch generated while at least one prior
    class existed."""
    occurrences: dict[str, list[int]] = {}
    events: list[dict] = []
    for p in sorted(run_patches, key=lambda p: p["patch_index"]):
        ch = p.get("class_hash")
        if not ch:
            continue
        if occurrences:
            ctx = set(p.get("context_patch_indexes") or [])
            visible = {c for c, idxs in occurrences.items() if any(i in ctx for i in idxs)}
            hidden = set(occurrences) - visible
            events.append(
                {
                    "n_visible": len(visible),
                    "n_hidden": len(hidden),
                    "repeat": ch in occurrences,
                    "hit_visible": ch in visible,
                    "hit_hidden": ch in hidden,
                }
            )
        occurrences.setdefault(ch, []).append(p["patch_index"])
    return events


def opportunity_table(events: list[dict]) -> dict:
    repeats = [e for e in events if e["repeat"]]
    mixed = [e for e in repeats if e["n_visible"] and e["n_hidden"]]
    expected = (
        sum(e["n_visible"] / (e["n_visible"] + e["n_hidden"]) for e in repeats) / len(repeats)
        if repeats
        else None
    )
    return {
        "events": len(events),
        "repeats": len(repeats),
        "repeats_on_visible": sum(1 for e in repeats if e["hit_visible"]),
        "repeats_on_hidden": sum(1 for e in repeats if e["hit_hidden"]),
        "observed_visible_share": round(
            sum(1 for e in repeats if e["hit_visible"]) / len(repeats), 4
        ) if repeats else None,
        "opportunity_visible_share": round(expected, 4) if expected is not None else None,
        "repeats_with_both_available": len(mixed),
    }


def origin_table(repeat_records: list[dict]) -> dict:
    """Counts and shares per origin (and memory split) for one configuration."""
    n = len(repeat_records)
    counts = Counter(r["origin"] for r in repeat_records)
    splits = Counter(r["memory_split"] for r in repeat_records if r["memory_split"])
    table = {"l4_repeats": n}
    for o in ("in_context_reported", "in_context_unreported", "lost_at_restart"):
        table[o] = counts.get(o, 0)
        table[o + "_share"] = round(counts.get(o, 0) / n, 4) if n else None
    for s in ("in_memory_selected", "in_memory_not_selected", "not_in_memory"):
        if splits.get(s):
            table[s] = splits[s]
    return table
