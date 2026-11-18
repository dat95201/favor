from favor.metrics import (
    cost_to_first_plausible,
    duplicate_rate,
    duplicate_rate_at,
    mean_s_curve,
    runs_from_patches,
    s_curve,
    survival_curve,
    wasted_validation,
)


def test_s_curve_and_duplicate_rate():
    keys = ["a", "b", "a", "c", "a"]
    assert s_curve(keys) == [1, 2, 2, 3, 3]
    assert duplicate_rate(keys) == 1 - 3 / 5
    assert duplicate_rate([]) is None


def test_duplicate_rate_at_equal_n():
    keys = ["a", "b", "a", "c"]
    assert duplicate_rate_at(keys, 2) == 0.0
    assert duplicate_rate_at(keys, 3) == 1 - 2 / 3
    # a run that stopped before n is excluded, not padded
    assert duplicate_rate_at(keys, 5) is None


def test_wasted_validation_counts_repeat_cost():
    patches = [
        {"status": "fail", "class_hash": "x", "compile_s": 1, "test_s": 1},
        {"status": "fail", "class_hash": "y", "compile_s": 1, "test_s": 1},
        {"status": "fail", "class_hash": "x", "compile_s": 1, "test_s": 3},  # repeat, cost 4
        {"status": "repeat", "class_hash": "x", "compile_s": 0, "test_s": 0},  # filtered: no cost
    ]
    assert wasted_validation(patches, "l4") == 4 / 8


def test_cost_censoring():
    assert cost_to_first_plausible(
        {"plausible_found": True, "validated_to_first_plausible": 3}, 20
    ) == (3, False)
    assert cost_to_first_plausible({"plausible_found": False}, 20) == (20, True)


def test_survival_curve_averages_over_bugs_not_runs():
    runs = [
        # bug A: two runs, one quick success
        {"bug": "A", "plausible_found": True, "validated_to_first_plausible": 1},
        {"bug": "A", "plausible_found": False},
        # bug B: one run, success at 2
        {"bug": "B", "plausible_found": True, "validated_to_first_plausible": 2},
    ]
    # b=1: A -> 1/2, B -> 0 => 0.25 ; b=2: A -> 1/2, B -> 1 => 0.75
    assert survival_curve(runs, 2) == [0.25, 0.75]


def test_mean_s_curve_drops_finished_runs():
    # the short run must NOT hold its final value at n=2 (that would count a
    # fast success as duplicating)
    assert mean_s_curve([[1, 2], [1]], 3) == [1.0, 2.0]
    assert mean_s_curve([], 3) == []


def test_runs_from_patches_keeps_last_record_per_index():
    # a crashed run re-executed on resume appends the same patch_index twice
    p = lambda i, status: {  # noqa: E731
        "bug": "B-1", "config": "CR", "run_idx": 0, "patch_index": i, "status": status,
    }
    patches = [p(0, "fail"), p(1, "fail"), p(0, "fail"), p(1, "plausible")]
    runs = runs_from_patches(patches)
    seq = runs[("B-1", "CR", 0)]
    assert len(seq) == 2
    assert seq[1]["status"] == "plausible"
