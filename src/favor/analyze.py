"""Analysis: turn runs/<exp>/{runs,patches}.jsonl into the RQ1-RQ4 tables and
figures (Sections 5.7-5.8). Everything is re-derived from the raw logs; bugs
are the unit of aggregation; decisions (non-inferiority / superiority) are
emitted only for the confirmatory comparison (FaVoR vs CR, Section 5.8) —
every other contrast is exploratory and reported with CIs only."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .configurations import CONFIRMATORY, table5
from .java_ast import l2_key
from .metrics import (
    config_summary,
    duplicate_rate_at,
    level_sequence,
    mean_s_curve,
    runs_from_patches,
    s_curve,
    survival_curve,
)
from .origin import classify_run, opportunity_events, opportunity_table, origin_table
from .stats import (
    bootstrap_diff_ci,
    cost_per_bug,
    factorial_effects,
    noninferiority,
    paired,
    repair_rate_per_bug,
)
from .storage import read_json, read_jsonl, write_json

FACTORIAL_ARMS = ("CR", "CR+TopK", "CR+VS", "FaVoR")  # 2x2: memory x VS


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", newline="", encoding="utf8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def load_labels(labels_path: Path | None) -> dict[tuple[str, str], str]:
    """(bug, l2) -> 'correct' | 'incorrect' after resolution. The key includes
    the bug: trivial method bodies can collide across bugs at L2."""
    if not labels_path or not Path(labels_path).exists():
        return {}
    resolved: dict[tuple[str, str], str] = {}
    for rec in read_jsonl(labels_path):
        if rec.get("resolved"):
            resolved[(rec["bug"], rec["l2"])] = rec["label"]
    return resolved


def _latest_runs(runs_path: Path) -> list[dict]:
    """One record per (bug, config, run_idx); the last write wins (a crashed
    and re-executed run, or a --no-resume redo, appends)."""
    latest: dict[tuple, dict] = {}
    for r in read_jsonl(runs_path):
        latest[(r["bug"], r["config"], r["run_idx"])] = r
    return list(latest.values())


def analyze(runs_dir: str | Path, out_dir: str | Path, labels_path=None) -> None:
    runs_dir, out = Path(runs_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    exp_meta = read_json(runs_dir / "experiment.json")
    proto_raw = dict(exp_meta["protocol"])
    budget_b = proto_raw["budget_b"]
    delta = proto_raw["delta"]
    n_boot = proto_raw.get("n_bootstrap", 10000)
    seed = proto_raw.get("seed", 0)
    alpha_rename = proto_raw.get("l2_alpha_rename", True)

    runs = _latest_runs(runs_dir / "runs.jsonl")
    patches = list(read_jsonl(runs_dir / "patches.jsonl"))
    by_config_runs: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_config_runs[r["config"]].append(r)
    patches_by_run = runs_from_patches(patches)
    by_config_patchruns: dict[str, dict[tuple, list[dict]]] = defaultdict(dict)
    for key, pts in patches_by_run.items():
        by_config_patchruns[key[1]][key] = pts

    labels = load_labels(labels_path)
    dev_fix_l2 = _developer_fix_l2(alpha_rename)

    def run_correct(r: dict) -> bool:
        code = r.get("plausible_code")
        if not (r["plausible_found"] and code):
            return False
        return labels.get((r["bug"], l2_key(code, alpha_rename))) == "correct"

    # --- headline summary per configuration (RQ2/RQ4) --------------------------
    summary_rows = []
    for cfg, rs in sorted(by_config_runs.items()):
        row = {"config": cfg}
        row.update(config_summary(rs, by_config_patchruns.get(cfg, {}), budget_b))
        if labels:
            correct_pb = _per_bug_rate(rs, run_correct)
            row["repair_rate_correct"] = (
                round(sum(correct_pb.values()) / len(correct_pb), 4) if correct_pb else None
            )
            row["bugs_with_correct"] = sum(1 for v in correct_pb.values() if v > 0)
        if dev_fix_l2:
            matches, plausibles = 0, 0
            for r in rs:
                code = r.get("plausible_code")
                if r["plausible_found"] and code:
                    plausibles += 1
                    if l2_key(code, alpha_rename) == dev_fix_l2.get(r["bug"]):
                        matches += 1
            row["plausible_matches_dev_fix"] = (
                round(matches / plausibles, 4) if plausibles else None
            )  # memorization proxy (Section 5.2 threat)
        summary_rows.append(row)
    _write_csv(out / "summary.csv", summary_rows)

    # --- RQ1: duplication at equal n, accumulation curves, origins --------------
    dr_rows = []
    ns = sorted({min(5, budget_b), min(10, budget_b), budget_b})
    for cfg, pruns in sorted(by_config_patchruns.items()):
        for level in ("l1", "l2", "l4"):
            seqs = [level_sequence(p, level) for p in pruns.values()]
            for n in ns:
                drs = [d for d in (duplicate_rate_at(s, n) for s in seqs) if d is not None]
                dr_rows.append({
                    "config": cfg, "level": level, "n": n,
                    "dr": round(sum(drs) / len(drs), 4) if drs else None,
                    "runs_reaching_n": len(drs),
                })
    _write_csv(out / "rq1_dr.csv", dr_rows)

    s_rows = []
    for cfg, pruns in sorted(by_config_patchruns.items()):
        for level in ("l1", "l2", "l4"):
            curves = [s_curve(level_sequence(p, level)) for p in pruns.values()]
            curves = [c for c in curves if c]
            mean = mean_s_curve(curves, budget_b)
            s_rows.append({"config": cfg, "level": level,
                           **{f"n{i+1}": round(v, 2) for i, v in enumerate(mean)}})
    _write_csv(out / "rq1_s_curves.csv", s_rows)

    loop_configs = {name for name, c in table5_safe(exp_meta).items() if c["regime"] == "loop"}
    origin_rows = []
    repeat_rows = []
    opportunity_rows = []
    for cfg, pruns in sorted(by_config_patchruns.items()):
        if cfg not in loop_configs:
            continue
        has_memory = any(p.get("memory_classes_all") for pts in pruns.values() for p in pts)
        repeats = []
        events = []
        for pts in pruns.values():
            repeats.extend(classify_run(pts, has_memory))
            events.extend(opportunity_events(pts))
        repeat_rows.extend(repeats)
        origin_rows.append({"config": cfg, **origin_table(repeats)})
        opportunity_rows.append({"config": cfg, **opportunity_table(events)})
    _write_csv(out / "rq1_origin.csv", origin_rows)
    _write_csv(out / "rq1_opportunity.csv", opportunity_rows)
    _write_csv(out / "rq1_repeats.csv", repeat_rows)

    # --- RQ2: cost to first plausible vs CR (confirmatory: FaVoR, superiority) --
    rq2_rows = []
    if "CR" in by_config_runs:
        base_cost = cost_per_bug(by_config_runs["CR"], budget_b)
        for cfg, rs in sorted(by_config_runs.items()):
            if cfg == "CR":
                continue
            new_cost = cost_per_bug(rs, budget_b)
            bugs, xs, ys = paired(new_cost, base_cost)
            if not bugs:
                continue
            diff, lo, hi = bootstrap_diff_ci(xs, ys, n_boot=n_boot, seed=seed)
            rq2_rows.append({
                "config": cfg, "vs": "CR", "bugs": len(bugs),
                "mean_cost": round(sum(xs) / len(xs), 3),
                "cr_mean_cost": round(sum(ys) / len(ys), 3),
                "diff": round(diff, 3),
                "ci95_lo": round(lo, 3), "ci95_hi": round(hi, 3),
                "confirmatory": cfg == "FaVoR",
            })
    _write_csv(out / "rq2_cost.csv", rq2_rows)

    # --- RQ3: repair-rate non-inferiority; a decision only for FaVoR vs CR ------
    rq3_rows = []
    if "CR" in by_config_runs:
        base_rate = repair_rate_per_bug(by_config_runs["CR"])
        for cfg, rs in sorted(by_config_runs.items()):
            if cfg == "CR":
                continue
            res = noninferiority(
                repair_rate_per_bug(rs), base_rate, delta=delta,
                n_boot=n_boot, seed=seed,
            )
            confirmatory = cfg == "FaVoR" and set(CONFIRMATORY) == {"CR", "FaVoR"}
            rq3_rows.append({
                "config": cfg, "vs": "CR",
                "rate": res.new_rate, "cr_rate": res.base_rate,
                "diff": res.diff, "one_sided_95_lower": res.lower_bound,
                "delta": res.delta,
                # exploratory rows carry the interval, never a decision (5.8)
                "non_inferior": res.non_inferior if confirmatory else "",
                "superior": res.superior if confirmatory else "",
                "confirmatory": confirmatory,
            })
    _write_csv(out / "rq3_noninferiority.csv", rq3_rows)

    # --- RQ4: 2x2 factorial over the mechanism arms + survival curves -----------
    factorial_rows = []
    if all(a in by_config_runs for a in FACTORIAL_ARMS):
        arms_cost = [cost_per_bug(by_config_runs[a], budget_b) for a in FACTORIAL_ARMS]
        factorial_rows.append({"endpoint": "cost_to_first_plausible",
                               **factorial_effects(*arms_cost, n_boot=n_boot, seed=seed)})
        arms_rate = [repair_rate_per_bug(by_config_runs[a]) for a in FACTORIAL_ARMS]
        factorial_rows.append({"endpoint": "repair_rate_plausible",
                               **factorial_effects(*arms_rate, n_boot=n_boot, seed=seed)})
        if labels:
            arms_correct = [_per_bug_rate(by_config_runs[a], run_correct) for a in FACTORIAL_ARMS]
            if all(arms_correct):
                factorial_rows.append({"endpoint": "repair_rate_correct",
                                       **factorial_effects(*arms_correct, n_boot=n_boot, seed=seed)})
    _write_csv(out / "rq4_factorial.csv", factorial_rows)

    surv_rows = []
    for cfg, rs in sorted(by_config_runs.items()):
        curve = survival_curve(rs, budget_b)
        surv_rows.append({"config": cfg,
                          **{f"b{i+1}": round(v, 4) for i, v in enumerate(curve)}})
    _write_csv(out / "rq4_survival.csv", surv_rows)

    _plot_curves(out, s_rows, surv_rows, budget_b)

    write_json(out / "meta.json", {
        "runs_dir": str(runs_dir), "runs": len(runs), "patches": len(patches),
        "budget_b": budget_b, "delta": delta, "labels": len(labels),
    })
    print(f"analysis written to {out}")


def _per_bug_rate(run_records: list[dict], pred) -> dict[str, float]:
    by_bug: dict[str, list[dict]] = defaultdict(list)
    for r in run_records:
        by_bug[r["bug"]].append(r)
    return {bug: sum(1 for r in rs if pred(r)) / len(rs) for bug, rs in by_bug.items()}


def _developer_fix_l2(alpha_rename: bool) -> dict[str, str]:
    """bug -> L2 key of the developer fix (memorization proxy). Empty when the
    selection outputs are absent (e.g. the offline smoke run)."""
    try:
        from .selection import load_bugs

        return {b.key: l2_key(b.fixed_method_src, alpha_rename) for b in load_bugs()}
    except Exception:  # noqa: BLE001 — proxy column is best-effort
        return {}


def table5_safe(exp_meta: dict) -> dict[str, dict]:
    """Table-5 rows as dicts, tolerant of metadata written by older runs."""
    from .config import Protocol

    proto_raw = {
        k: v for k, v in exp_meta["protocol"].items()
        if k in Protocol.__dataclass_fields__
    }
    proto_raw["score_weights"] = tuple(proto_raw.get("score_weights", (1, 1, 1)))
    proto = Protocol(**proto_raw)
    return {name: vars(cfg) for name, cfg in table5(proto).items()}


def _plot_curves(out: Path, s_rows: list[dict], surv_rows: list[dict], budget_b: int) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001 — plotting is optional
        return

    fig, ax = plt.subplots(figsize=(7, 4.5))
    for row in s_rows:
        if row["level"] != "l4":
            continue
        ys = [row.get(f"n{i+1}") for i in range(budget_b)]
        ys = [y for y in ys if y is not None]
        ax.plot(range(1, len(ys) + 1), ys, label=row["config"], alpha=0.85)
    ax.set_xlabel("validated patches n")
    ax.set_ylabel("distinct L4 classes  S_L4(n)")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "s_l4_curves.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    for row in surv_rows:
        ys = [row.get(f"b{i+1}") for i in range(budget_b)]
        ys = [y for y in ys if y is not None]
        ax.plot(range(1, len(ys) + 1), ys, label=row["config"], alpha=0.85)
    ax.set_xlabel("validation budget spent b")
    ax.set_ylabel("share of a bug's runs with a plausible patch")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "survival.png", dpi=150)
    plt.close(fig)
