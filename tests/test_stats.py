from favor.stats import cohens_kappa, noninferiority


def test_noninferiority_clear_case():
    base = {f"b{i}": 0.5 for i in range(40)}
    new = {f"b{i}": 0.5 for i in range(40)}
    res = noninferiority(new, base, delta=0.03, n_boot=2000, seed=1)
    assert res.non_inferior
    assert not res.superior
    assert abs(res.diff) < 1e-9


def test_noninferiority_rejects_big_loss():
    base = {f"b{i}": 1.0 for i in range(40)}
    new = {f"b{i}": (0.0 if i % 2 else 1.0) for i in range(40)}
    res = noninferiority(new, base, delta=0.03, n_boot=2000, seed=1)
    assert not res.non_inferior


def test_superiority_detected():
    base = {f"b{i}": 0.2 for i in range(40)}
    new = {f"b{i}": (1.0 if i % 2 else 0.4) for i in range(40)}
    res = noninferiority(new, base, delta=0.03, n_boot=2000, seed=1)
    assert res.non_inferior and res.superior


def test_kappa_perfect_and_chance():
    assert cohens_kappa(["c", "i", "c"], ["c", "i", "c"]) == 1.0
    k = cohens_kappa(["c", "c", "i", "i"], ["c", "i", "c", "i"])
    assert abs(k) < 1e-9
