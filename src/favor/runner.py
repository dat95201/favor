"""Experiment orchestration: (bug x configuration x run) tasks, interleaved in
a seeded random order so machine drift favors no configuration (Section 5.5),
with resume from runs.jsonl and optional thread workers (each worker keeps its
own Defects4J checkout per bug)."""

from __future__ import annotations

import itertools
import random
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import paths as P
from .config import ExperimentConfig, ModelConfig
from .configurations import table5
from .llm import ChatClient
from .loop import run_repair
from .records import BugRecord
from .storage import JsonlWriter, read_jsonl, write_json
from .validation import Validator

# How many prepared checkouts a worker keeps before evicting the oldest.
# Tasks are shuffled across bugs (5.5), so a worker revisits a bug only by
# chance; this bounds disk at (workers x CHECKOUT_CACHE) checkouts instead of
# accumulating one per (worker, bug) for the whole sweep.
CHECKOUT_CACHE = 4


def run_experiment(
    exp: ExperimentConfig,
    model_cfg: ModelConfig,
    bugs: list[BugRecord],
    out_dir: str | Path,
    workers: int = 1,
    resume: bool = True,
    keep_work: bool = False,
    checkout_cache: int = CHECKOUT_CACHE,
) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    proto = exp.protocol
    confs = table5(proto)
    unknown = [n for n in exp.configurations if n not in confs]
    assert not unknown, f"unknown configurations: {unknown} (Table 5: {list(confs)})"
    selected = [confs[n] for n in exp.configurations]

    # snapshot the protocol next to the logs (Section 5.10)
    write_json(out / "experiment.json", {**exp.dump(), "model": model_cfg.name,
                                         "bugs": [b.key for b in bugs]})

    done: set[tuple] = set()
    if resume:
        done = {
            (r["bug"], r["config"], r["run_idx"])
            for r in read_jsonl(out / "runs.jsonl")
        }

    tasks = [
        (bug, cfg, run_idx)
        for bug in bugs
        for cfg in selected
        for run_idx in range(proto.runs_per_bug)
        if (bug.key, cfg.name, run_idx) not in done
    ]
    rng = random.Random(proto.seed)
    rng.shuffle(tasks)  # interleave configurations on the same bug (5.5)
    if not tasks:
        print("nothing to do (all runs present; use --no-resume to redo)")
        return
    print(f"{len(tasks)} runs to do ({len(done)} already present)")

    patch_log = JsonlWriter(out / "patches.jsonl")
    call_log = JsonlWriter(out / "calls.jsonl")
    run_log = JsonlWriter(out / "runs.jsonl")
    llm = ChatClient(model_cfg, cache_dir=P.CACHE / model_cfg.name.replace("/", "_"),
                     log_path=out / "llm.jsonl")

    local = threading.local()
    wid_counter = itertools.count()
    wid_lock = threading.Lock()
    live: dict[int, Validator] = {}     # every prepared checkout, for final cleanup
    live_lock = threading.Lock()

    def release(v: Validator) -> None:
        with live_lock:
            live.pop(id(v), None)
        if not keep_work:
            v.cleanup()

    def validator_for(bug: BugRecord) -> Validator:
        # One checkout per (worker, bug). Worker ids come from a counter, not
        # from thread identity: `get_ident() % 10000` can collide between two
        # live pool threads, and a collision silently hands both the same
        # working copy, letting one task's restore() race the other's compile.
        cache: OrderedDict[str, Validator] | None = getattr(local, "validators", None)
        if cache is None:
            with wid_lock:
                local.wid = next(wid_counter)
            cache = local.validators = OrderedDict()
        v = cache.get(bug.key)
        if v is not None:
            # already checked out, exported and span-checked: prepare() would
            # re-run `defects4j export` (perl + JVM) for a property of the bug
            # that cannot have changed. The working copy is clean because
            # validate() restores the original file in a finally.
            cache.move_to_end(bug.key)
            return v
        v = Validator(bug, proto, P.WORK / "runs" / f"{bug.key}-w{local.wid}")
        v.prepare()
        cache[bug.key] = v
        with live_lock:
            live[id(v)] = v
        while len(cache) > checkout_cache:
            _, evicted = cache.popitem(last=False)
            release(evicted)
        return v

    def one(task) -> dict:
        bug, cfg, run_idx = task
        v = validator_for(bug)
        rec = run_repair(bug, cfg, proto, llm, v, run_idx,
                         patch_log=patch_log, call_log=call_log)
        run_log.write(rec.to_json())
        return {"bug": bug.key, "config": cfg.name, "run": run_idx,
                "plausible": rec.plausible_found, "validated": rec.validated_count}

    if workers <= 1:
        for i, task in enumerate(tasks, 1):
            r = one(task)
            print(f"[{i}/{len(tasks)}] {r['bug']} {r['config']} run{r['run']}: "
                  f"{'plausible' if r['plausible'] else 'no fix'} "
                  f"({r['validated']} validated)")
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(one, t): t for t in tasks}
            for i, fut in enumerate(as_completed(futures), 1):
                bug, cfg, run_idx = futures[fut]
                try:
                    r = fut.result()
                    print(f"[{i}/{len(tasks)}] {r['bug']} {r['config']} run{r['run']}: "
                          f"{'plausible' if r['plausible'] else 'no fix'} "
                          f"({r['validated']} validated)")
                except Exception as e:  # noqa: BLE001 — log and continue the sweep
                    print(f"[{i}/{len(tasks)}] {bug.key} {cfg.name} run{run_idx} FAILED: {e}")

    # Only on normal completion: an interrupted sweep keeps its checkouts so
    # that resume does not re-pay `defects4j checkout` for every live bug.
    with live_lock:
        remaining = list(live.values())
        live.clear()
    if keep_work:
        print(f"kept {len(remaining)} checkout(s) under {P.WORK / 'runs'}")
    else:
        for v in remaining:
            v.cleanup()

    patch_log.close()
    call_log.close()
    run_log.close()
