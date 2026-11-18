"""Worker isolation and checkout lifecycle in the sweep runner.

These cover the two properties the parallel path depends on: distinct workers
never share a working copy (a shared one lets a task's restore() race another's
compile), and checkouts are reused within a worker but bounded and released.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from favor import runner
from favor.config import ExperimentConfig
from favor.mock import mock_bug


class FakeValidator:
    """Records its lifecycle instead of touching Defects4J."""

    def __init__(self, bug, proto, workdir):
        self.bug = bug
        self.workdir = Path(workdir)
        self.prepared = 0
        self.cleaned = 0

    def prepare(self) -> None:
        self.prepared += 1

    def cleanup(self) -> None:
        self.cleaned += 1


class FakeRun:
    plausible_found = False
    validated_count = 1

    def to_json(self) -> dict:
        return {"bug": "Mock-1", "config": "CR", "run_idx": 0}


@pytest.fixture
def sweep(tmp_path, monkeypatch):
    """run_experiment with Defects4J, the LLM and the repair loop stubbed out.

    Returns a callable; each call yields the FakeValidators that were built.
    """
    built: list[FakeValidator] = []
    seen_workdirs: list[tuple[int, Path]] = []
    lock = threading.Lock()

    def make_validator(bug, proto, workdir):
        v = FakeValidator(bug, proto, workdir)
        with lock:
            built.append(v)
            seen_workdirs.append((threading.get_ident(), Path(workdir)))
        return v

    def fake_repair(bug, cfg, proto, llm, validator, run_idx, **kw):
        assert validator.prepared == 1, "a reused checkout must not be re-prepared"
        return FakeRun()

    monkeypatch.setattr(runner, "Validator", make_validator)
    monkeypatch.setattr(runner, "run_repair", fake_repair)
    monkeypatch.setattr(runner, "ChatClient", lambda *a, **kw: object())
    monkeypatch.setattr(runner.P, "WORK", tmp_path / "work")

    def go(bugs, *, workers=1, configs=("CR",), runs_per_bug=1, **kw):
        exp = ExperimentConfig.load("configs/experiment.yaml")
        proto = type(exp.protocol)(**{**vars(exp.protocol), "runs_per_bug": runs_per_bug})
        exp = type(exp)(protocol=proto, configurations=tuple(configs))
        model = type("M", (), {"name": "fake", "dump": lambda s: {}})()
        runner.run_experiment(exp, model, list(bugs), tmp_path / "out",
                              workers=workers, resume=False, **kw)
        return built, seen_workdirs

    return go


def test_workers_never_share_a_working_copy(sweep):
    bugs = [mock_bug(f"Mock-{i}") for i in range(4)]
    built, seen = sweep(bugs, workers=4, runs_per_bug=3)

    by_thread: dict[int, set[Path]] = {}
    for tid, wd in seen:
        by_thread.setdefault(tid, set()).add(wd)

    # no working copy is handed to two different threads
    for a_tid, a_dirs in by_thread.items():
        for b_tid, b_dirs in by_thread.items():
            if a_tid != b_tid:
                assert not (a_dirs & b_dirs), "two workers shared a checkout"


def test_worker_ids_are_a_dense_counter(sweep):
    """The collision risk in `get_ident() % 10000` came from drawing ids out of
    a sparse 10k space. A counter closes it by construction, so assert the
    construction: ids must be 0..n-1, not arbitrary thread identities.
    """
    bugs = [mock_bug(f"Mock-{i}") for i in range(4)]
    _, seen = sweep(bugs, workers=4, runs_per_bug=3)

    wids = {int(wd.name.rsplit("-w", 1)[1]) for _, wd in seen}
    assert wids == set(range(len(wids))), f"worker ids are not a dense counter: {wids}"


def test_checkout_is_reused_across_a_bugs_runs(sweep):
    """Three runs of one bug on one worker: one checkout, one prepare()."""
    built, _ = sweep([mock_bug("Mock-1")], workers=1, runs_per_bug=3)
    assert len(built) == 1, "re-prepared a checkout that was already live"
    assert built[0].prepared == 1


def test_cache_evicts_and_releases_the_oldest(sweep):
    """More bugs than the cache holds: the overflow is cleaned, not leaked."""
    bugs = [mock_bug(f"Mock-{i}") for i in range(5)]
    built, _ = sweep(bugs, workers=1, runs_per_bug=1, checkout_cache=2)
    assert len(built) == 5
    assert sum(v.cleaned for v in built) == 5, "every checkout released exactly once"


def test_sweep_end_cleans_up_and_keep_work_does_not(sweep, tmp_path):
    bugs = [mock_bug(f"Mock-{i}") for i in range(2)]
    built, _ = sweep(bugs, workers=1, runs_per_bug=1)
    assert all(v.cleaned == 1 for v in built), "checkouts leaked at sweep end"


def test_keep_work_leaves_checkouts_in_place(sweep):
    bugs = [mock_bug(f"Mock-{i}") for i in range(2)]
    built, _ = sweep(bugs, workers=1, runs_per_bug=1, keep_work=True)
    assert all(v.cleaned == 0 for v in built)
