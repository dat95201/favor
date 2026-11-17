"""Analysis: turn runs/<exp>/{runs,patches}.jsonl into the RQ1-RQ4 tables and
figures (Sections 5.7-5.8). Everything is re-derived from the raw logs."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .config import ExperimentConfig
from .configurations import table5
from .java_ast import l2_key
from .metrics import (
    config_summary,
    cost_to_first_plausible,
    level_sequence,
    mean_s_curve,
    runs_from_patches,
    s_curve,
    survival_curve,
)
from .origin import classify_run, origin_table
from .stats import (
    bootstrap_diff_ci,
    cohens_kappa,
    cost_per_bug,
    noninferiority,
    paired,
    repair_rate_per_bug,
)
from .storage import read_json, read_jsonl, write_json


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


def load_labels(labels_path: Path | None) -> dict[str, str]:
    """l2 key -> 'correct' | 'incorrect' (after resolution). A label covers
    every patch in the same L2 class (Section 5.9)."""
    if not labels_path or not Path(labels_path).exists():
        return {}
    resolved: dict[str, str] = {}
    for rec in read_jsonl(labels_path):
        if rec.get("resolved"):
            resolved[rec["l2"]] = rec["label"]
    return resolved


def analyze(runs_dir: str | Path, out_dir: str | Path, labels_path=None) -> None:
    runs_dir, out = Path(runs_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    exp_meta = read_json(runs_dir / "experiment.json")
    proto_raw = dict(exp_meta["protocol"])
    budget_b = proto_raw["budget_b"]
    delta = proto_raw["delta"]
    n_boot = proto_raw.get("n_bootstrap", 10000)
    seed = proto_raw.get("seed", 0)

    runs = list(read_jsonl(runs_dir / "runs.jsonl"))
    patches = list(read_jsonl(runs_dir / "patches.jsonl"))
    by_config_runs: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_config_runs[r["config"]].append(r)
    patches_by_run = runs_from_patches(patches)
    by_config_patchruns: dict[str, dict[tuple, list[dict]]] = defaultdict(dict)
    for key, pts in patches_by_run.items():
        by_config_patchruns[key[1]][key] = pts

    labels = load_labels(labels_path)

    # --- headline summary per configuration (RQ2/RQ4) --------------------------
    summary_rows = []
    for cfg, rs in sorted(by_config_runs.items()):
        row = {"config": cfg}
        row.update(config_summary(rs, by_config_patchruns.get(cfg, {}), budget_b))
        if labels:
            correct_bugs = set()
            for r in rs:
                code = r.get("plausible_code")
                if r["plausible_found"] and code and labels.get(l2_key(code)) == "correct":
                    correct_bugs.add(r["bug"])
            row["bugs_with_correct"] = len(correct_bugs)
        summary_rows.append(row)
    _write_csv(out / "summary.csv", summary_rows)

    # --- RQ1: duplication levels and origins ------------------------------------
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
    for cfg, pruns in sorted(by_config_patchruns.items()):
        if cfg not in loop_configs:
            continue
        has_memory = any(p.get("memory_classes_all") for pts in pruns.values() for p in pts)
        repeats = []
        for pts in pruns.values():
            repeats.extend(classify_run(pts, has_memory))
        repeat_rows.extend(repeats)
        origin_rows.append({"config": cfg, **origin_table(repeats)})
    _write_csv(out / "rq1_origin.csv", origin_rows)
    _write_csv(out / "rq1_repeats.csv", repeat_rows)

    # --- RQ2: cost to first plausible, FaVoR vs CR (superiority) ----------------
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

    # --- RQ3: repair-rate non-inferiority vs CR ----------------------------------
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
            rq3_rows.append({
                "config": cfg, "vs": "CR",
                "rate": res.new_rate, "cr_rate": res.base_rate,
                "diff": res.diff, "one_sided_95_lower": res.lower_bound,
                "delta": res.delta, "non_inferior": res.non_inferior,
                "superior": res.superior,
                "confirmatory": cfg == "FaVoR",
            })
    _write_csv(out / "rq3_noninferiority.csv", rq3_rows)

    # --- RQ4: survival curves + per-config cost (ablation table = summary.csv) --
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


def table5_safe(exp_meta: dict) -> dict[str, dict]:
    """Table-5 rows as dicts, tolerant of metadata written by older runs."""
    from .config import Protocol

    proto_raw = dict(exp_meta["protocol"])
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
    ax.set_ylabel("runs with a plausible patch")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "survival.png", dpi=150)
    plt.close(fig)
