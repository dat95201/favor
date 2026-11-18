"""Statistical analysis (Sections 2.6, 5.8): paired bootstrap over bugs,
non-inferiority on the repair rate, superiority on the cost to the first
plausible patch, and Cohen's kappa for the correctness labels.

All comparisons are paired on bugs: for each bug and configuration the repair
rate is the fraction of runs with a plausible patch (resp. a correct one), and
the cost is the mean validated-patch count to the first plausible patch,
censored at B. Bootstrap resamples bugs, keeping a bug's runs together."""

from __future__ import annotations

import random
from dataclasses import dataclass


def per_bug_value(run_records: list[dict], value_fn) -> dict[str, float]:
    by_bug: dict[str, list[dict]] = {}
    for r in run_records:
        by_bug.setdefault(r["bug"], []).append(r)
    return {bug: sum(value_fn(r) for r in rs) / len(rs) for bug, rs in by_bug.items()}


def repair_rate_per_bug(run_records: list[dict]) -> dict[str, float]:
    return per_bug_value(run_records, lambda r: 1.0 if r["plausible_found"] else 0.0)


def cost_per_bug(run_records: list[dict], budget_b: int) -> dict[str, float]:
    return per_bug_value(
        run_records,
        lambda r: (
            r["validated_to_first_plausible"] if r["plausible_found"] else budget_b
        ),
    )


def paired(
    a: dict[str, float], b: dict[str, float]
) -> tuple[list[str], list[float], list[float]]:
    bugs = sorted(set(a) & set(b))
    return bugs, [a[k] for k in bugs], [b[k] for k in bugs]


def bootstrap_diff_ci(
    xs: list[float],
    ys: list[float],
    n_boot: int = 10000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Mean difference xs - ys with a percentile bootstrap CI over paired
    units. Returns (mean_diff, lo, hi) at the two-sided (1-alpha) level; the
    one-sided lower bound at level (1-alpha) is the alpha-quantile, available
    via bootstrap_lower_bound."""
    assert len(xs) == len(ys) and xs, "paired, non-empty samples required"
    n = len(xs)
    diffs = [x - y for x, y in zip(xs, ys)]
    point = sum(diffs) / n
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        s = [diffs[rng.randrange(n)] for _ in range(n)]
        boots.append(sum(s) / n)
    boots.sort()
    lo = boots[int((alpha / 2) * n_boot)]
    hi = boots[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return point, lo, hi


def bootstrap_lower_bound(
    xs: list[float],
    ys: list[float],
    n_boot: int = 10000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """(mean diff, one-sided (1-alpha) lower confidence bound of xs - ys)."""
    assert len(xs) == len(ys) and xs
    n = len(xs)
    diffs = [x - y for x, y in zip(xs, ys)]
    point = sum(diffs) / n
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        s = [diffs[rng.randrange(n)] for _ in range(n)]
        boots.append(sum(s) / n)
    boots.sort()
    return point, boots[int(alpha * n_boot)]


@dataclass(frozen=True)
class NonInferiorityResult:
    new_rate: float
    base_rate: float
    diff: float
    lower_bound: float       # one-sided 95% lower bound of (new - base)
    delta: float
    non_inferior: bool       # lower_bound > -delta
    superior: bool           # lower_bound > 0


def noninferiority(
    new_per_bug: dict[str, float],
    base_per_bug: dict[str, float],
    delta: float,
    n_boot: int = 10000,
    seed: int = 0,
    alpha: float = 0.05,
) -> NonInferiorityResult:
    """H0: pi_new - pi_base <= -delta, rejected when the one-sided (1-alpha)
    lower bound of the paired difference lies above -delta (Section 2.6)."""
    bugs, xs, ys = paired(new_per_bug, base_per_bug)
    assert bugs, "no common bugs between configurations"
    point, lb = bootstrap_lower_bound(xs, ys, n_boot=n_boot, seed=seed, alpha=alpha)
    return NonInferiorityResult(
        new_rate=round(sum(xs) / len(xs), 4),
        base_rate=round(sum(ys) / len(ys), 4),
        diff=round(point, 4),
        lower_bound=round(lb, 4),
        delta=delta,
        non_inferior=lb > -delta,
        superior=lb > 0,
    )


def factorial_effects(
    cr: dict[str, float],
    mem_only: dict[str, float],
    vs_only: dict[str, float],
    both: dict[str, float],
    n_boot: int = 10000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict:
    """2x2 factorial over the mechanism arms (CR, CR+TopK, CR+VS, FaVoR),
    paired on bugs: main effect of each mechanism and their interaction, with
    percentile bootstrap CIs. Tests whether the mechanisms are complementary
    (interaction ~ 0 or beneficial) or redundant."""
    bugs = sorted(set(cr) & set(mem_only) & set(vs_only) & set(both))
    assert bugs, "no common bugs across the four factorial arms"
    rows = [(cr[k], mem_only[k], vs_only[k], both[k]) for k in bugs]

    def effects(rs: list[tuple]) -> tuple[float, float, float]:
        n = len(rs)
        mem = sum((m + b - c - v) / 2 for c, m, v, b in rs) / n
        vs = sum((v + b - c - m) / 2 for c, m, v, b in rs) / n
        inter = sum(b - m - v + c for c, m, v, b in rs) / n
        return mem, vs, inter

    point = effects(rows)
    rng = random.Random(seed)
    boots = [[], [], []]
    n = len(rows)
    for _ in range(n_boot):
        sample = [rows[rng.randrange(n)] for _ in range(n)]
        for i, v in enumerate(effects(sample)):
            boots[i].append(v)
    out = {"n_bugs": n}
    for name, pt, bs in zip(("memory", "vs", "interaction"), point, boots):
        bs.sort()
        out[name] = round(pt, 4)
        out[f"{name}_ci_lo"] = round(bs[int((alpha / 2) * n_boot)], 4)
        out[f"{name}_ci_hi"] = round(bs[min(n_boot - 1, int((1 - alpha / 2) * n_boot))], 4)
    return out


def cohens_kappa(labels_a: list[str], labels_b: list[str]) -> float:
    """Agreement between two labelers before resolution (Section 5.9)."""
    assert len(labels_a) == len(labels_b) and labels_a
    n = len(labels_a)
    cats = sorted(set(labels_a) | set(labels_b))
    po = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    pe = sum(
        (labels_a.count(c) / n) * (labels_b.count(c) / n) for c in cats
    )
    if pe == 1.0:
        return 1.0
    return (po - pe) / (1 - pe)
