"""Experiment orchestration: (bug x configuration x run) tasks, interleaved in
a seeded random order so machine drift favors no configuration (Section 5.5),
with resume from runs.jsonl and optional thread workers (each worker keeps its
own Defects4J checkout per bug)."""

from __future__ import annotations

import random
import threading
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


def run_experiment(
    exp: ExperimentConfig,
    model_cfg: ModelConfig,
    bugs: list[BugRecord],
    out_dir: str | Path,
    workers: int = 1,
    resume: bool = True,
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

    def validator_for(bug: BugRecord) -> Validator:
        # one checkout per (worker, bug), reused across that bug's runs
        wid = getattr(local, "wid", None)
        if wid is None:
            local.wid = wid = threading.get_ident() % 10000
        v = Validator(bug, proto, P.WORK / "runs" / f"{bug.key}-w{wid}")
        v.prepare()
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

    patch_log.close()
    call_log.close()
    run_log.close()
