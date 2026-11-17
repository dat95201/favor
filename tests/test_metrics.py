from favor.metrics import (
    cost_to_first_plausible,
    duplicate_rate,
    mean_s_curve,
    s_curve,
    survival_curve,
    wasted_validation,
)


def test_s_curve_and_duplicate_rate():
    keys = ["a", "b", "a", "c", "a"]
    assert s_curve(keys) == [1, 2, 2, 3, 3]
    assert duplicate_rate(keys) == 1 - 3 / 5
    assert duplicate_rate([]) is None


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


def test_survival_curve():
    runs = [
        {"plausible_found": True, "validated_to_first_plausible": 1},
        {"plausible_found": True, "validated_to_first_plausible": 3},
        {"plausible_found": False},
    ]
    assert survival_curve(runs, 3) == [1 / 3, 1 / 3, 2 / 3]


def test_mean_s_curve_holds_final_value():
    assert mean_s_curve([[1, 2], [1, 1, 2]], 3) == [1.0, 1.5, 2.0]
