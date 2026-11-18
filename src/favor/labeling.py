"""Correctness labeling (Section 5.9): two labelers label every distinct
plausible patch independently, blind to configuration and run; a label covers
every patch in the same L2 class. Labels: correct / incorrect / unsure.
`favor analyze --labels ...` uses rows marked resolved=true; Cohen's kappa over
the two blind passes is printed here."""

from __future__ import annotations

import difflib
from pathlib import Path

from .java_ast import l2_key
from .selection import load_bugs
from .stats import cohens_kappa
from .storage import JsonlWriter, read_jsonl


def _l2_alpha(runs_dir: Path) -> bool:
    """The L2 normalization must match the run's protocol, or labels would key
    to classes the loop never produced."""
    from .storage import read_json

    exp = runs_dir / "experiment.json"
    if exp.exists():
        return read_json(exp).get("protocol", {}).get("l2_alpha_rename", True)
    return True


def _distinct_plausible(runs_dir: Path, alpha: bool) -> list[dict]:
    """One entry per (bug, L2 class) over all plausible patches, config-blind."""
    seen: dict[tuple, dict] = {}
    for r in read_jsonl(runs_dir / "runs.jsonl"):
        code = r.get("plausible_code")
        if not (r["plausible_found"] and code):
            continue
        key = (r["bug"], l2_key(code, alpha))
        if key not in seen:
            seen[key] = {"bug": r["bug"], "l2": key[1], "code": code}
    return sorted(seen.values(), key=lambda d: (d["bug"], d["l2"]))


def label_cli(runs_dir: str, labeler: str, labels_path: str) -> int:
    runs_dir = Path(runs_dir)
    labels_path = Path(labels_path)
    alpha = _l2_alpha(runs_dir)
    bugs = {b.key: b for b in load_bugs()}
    done = {
        (r["bug"], r["l2"])
        for r in read_jsonl(labels_path)
        if r.get("labeler") == labeler
    }
    todo = [d for d in _distinct_plausible(runs_dir, alpha) if (d["bug"], d["l2"]) not in done]
    if not todo:
        print("nothing to label for this labeler")
        _report_kappa(labels_path)
        return 0

    writer = JsonlWriter(labels_path)
    print(f"{len(todo)} distinct plausible patches to label (labeler: {labeler})")
    print("verdicts: c = correct, i = incorrect, u = unsure, q = quit\n")
    for i, d in enumerate(todo, 1):
        bug = bugs.get(d["bug"])
        print("=" * 72)
        print(f"[{i}/{len(todo)}] {d['bug']}  (L2 {d['l2'][:12]})")
        if bug:
            diff = difflib.unified_diff(
                bug.fixed_method_src.splitlines(), d["code"].splitlines(),
                "developer-fix", "plausible-patch", lineterm="",
            )
            print("\n".join(diff) or "(identical to the developer fix)")
        else:
            print(d["code"])
        verdict = ""
        while verdict not in ("c", "i", "u", "q"):
            verdict = input("verdict [c/i/u/q] > ").strip().lower()
        if verdict == "q":
            break
        rationale = input("rationale (one line) > ").strip()
        writer.write({
            "bug": d["bug"], "l2": d["l2"], "labeler": labeler,
            "label": {"c": "correct", "i": "incorrect", "u": "unsure"}[verdict],
            "rationale": rationale, "resolved": False,
        })
    writer.close()
    _report_kappa(labels_path)
    print(
        "\nAfter both labelers finish: resolve disagreements by discussion and "
        "append rows with resolved=true (one per l2) to the labels file."
    )
    return 0


def _report_kappa(labels_path: Path) -> None:
    rows = [r for r in read_jsonl(labels_path) if not r.get("resolved")]
    by_labeler: dict[str, dict] = {}
    for r in rows:
        by_labeler.setdefault(r["labeler"], {})[(r["bug"], r["l2"])] = r["label"]
    labelers = sorted(by_labeler)
    if len(labelers) < 2:
        return
    a, b = by_labeler[labelers[0]], by_labeler[labelers[1]]
    common = sorted(set(a) & set(b))
    if not common:
        return
    kappa = cohens_kappa([a[k] for k in common], [b[k] for k in common])
    print(f"\nCohen's kappa ({labelers[0]} vs {labelers[1]}, n={len(common)}): {kappa:.3f}")
