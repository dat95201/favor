"""The bug-selection pipeline (Section 5.2, Table 4) and the dev/test split.

Walks every active Defects4J bug, applies the screens in order, and records
one status JSON per bug so an interrupted run resumes where it stopped. The
Table-4 counts are re-derived from the status files."""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

from . import d4j
from . import paths as P
from .config import Protocol
from .extraction import Rejection, analyze_bug
from .records import BugRecord
from .storage import read_json, write_json

STEPS = ["single-function", "build", "fixed-fails", "flaky", "slow-suite", "error"]


def _status_dir() -> Path:
    return P.DATA / "selection" / "status"


def _status_path(pid: str, bid: int) -> Path:
    return _status_dir() / f"{pid}-{bid}.json"


def screen_bug(pid: str, bid: int, proto: Protocol, force: bool = False) -> dict:
    """Screen one bug; cache the verdict under data/selection/status/."""
    sp = _status_path(pid, bid)
    if sp.exists() and not force:
        return read_json(sp)
    try:
        rec = analyze_bug(
            pid,
            bid,
            workbase=P.WORK / "selection",
            stack_frames=proto.signature_stack_frames,
            slow_suite_limit_s=proto.slow_suite_limit_s,
            flaky_runs=proto.flaky_runs,
        )
        status = {"kept": True, "record": rec.to_json()}
    except Rejection as r:
        status = {"kept": False, "step": r.step, "reason": r.reason}
    except Exception as e:  # noqa: BLE001 — a broken bug must not stop the sweep
        status = {"kept": False, "step": "error", "reason": f"{type(e).__name__}: {e}"}
    write_json(sp, status)
    return status


def run_selection(
    proto: Protocol,
    projects: list[str] | None = None,
    limit: int | None = None,
    force: bool = False,
) -> None:
    assert d4j.available(), f"Defects4J: {d4j.status()}"
    todo: list[tuple[str, int]] = []
    for pid in projects or d4j.projects():
        for bid in d4j.active_bugs(pid):
            todo.append((pid, bid))
    if limit:
        todo = todo[:limit]
    kept = 0
    for i, (pid, bid) in enumerate(todo, 1):
        status = screen_bug(pid, bid, proto, force=force)
        kept += 1 if status.get("kept") else 0
        verdict = "kept" if status.get("kept") else f"dropped ({status.get('step')})"
        print(f"[{i}/{len(todo)}] {pid}-{bid}: {verdict}   (kept so far: {kept})")
    write_outputs(proto)


def write_outputs(proto: Protocol) -> None:
    """data/bugs.jsonl (kept bugs), data/selection/table4.json (step counts),
    data/split.json (stratified dev/test, fixed seed)."""
    bugs: list[BugRecord] = []
    counts: Counter[str] = Counter()
    total = 0
    for sp in sorted(_status_dir().glob("*.json")):
        status = read_json(sp)
        total += 1
        if status.get("kept"):
            bugs.append(BugRecord.from_json(status["record"]))
        else:
            counts[status.get("step", "error")] += 1

    out = P.DATA / "bugs.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf8") as fh:
        for b in sorted(bugs, key=lambda b: (b.project, b.bug_id)):
            fh.write(json.dumps(b.to_json(), ensure_ascii=False) + "\n")

    # Table 4, as nested remaining counts (screens apply in order)
    table4 = {"screened": total}
    remaining = total
    for step in ("single-function", "build", "fixed-fails", "flaky", "slow-suite", "error"):
        remaining -= counts.get(step, 0)
        table4[f"after {step} (-{counts.get(step, 0)})"] = remaining
    table4["selected"] = len(bugs)
    write_json(P.DATA / "selection" / "table4.json", table4)

    # stratified dev/test split (Section 5.2): per project, fixed seed
    rng = random.Random(proto.seed)
    dev: list[str] = []
    test: list[str] = []
    by_project: dict[str, list[str]] = {}
    for b in bugs:
        by_project.setdefault(b.project, []).append(b.key)
    for pid in sorted(by_project):
        keys = sorted(by_project[pid])
        rng.shuffle(keys)
        n_dev = max(1, round(proto.dev_fraction * len(keys))) if len(keys) > 1 else 0
        dev += keys[:n_dev]
        test += keys[n_dev:]
    write_json(
        P.DATA / "split.json",
        {"seed": proto.seed, "dev_fraction": proto.dev_fraction,
         "dev": sorted(dev), "test": sorted(test)},
    )
    print(f"selected {len(bugs)} bugs -> {out}")
    print(f"split: {len(dev)} dev / {len(test)} test -> {P.DATA / 'split.json'}")


def load_bugs(split: str | None = None, keys: list[str] | None = None) -> list[BugRecord]:
    from .storage import read_jsonl

    bugs = [BugRecord.from_json(d) for d in read_jsonl(P.DATA / "bugs.jsonl")]
    if keys:
        want = set(keys)
        bugs = [b for b in bugs if b.key in want]
    elif split:
        sp = read_json(P.DATA / "split.json")
        want = set(sp[split])
        bugs = [b for b in bugs if b.key in want]
    return bugs
