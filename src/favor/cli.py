"""The favor command line.

  favor env                              check Java, Defects4J, model config
  favor select-bugs [...]                Table-4 screening (slow, resumable)
  favor split                            rewrite bugs.jsonl/table4/split from statuses
  favor run --model M [--split S] [...]  run configurations on bugs
  favor analyze --runs DIR [...]         RQ1-RQ4 tables and figures
  favor label --runs DIR --labeler NAME  correctness labeling (Section 5.9)
  favor smoke                            offline end-to-end check (no API, no D4J)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import paths as P


def _load_env() -> None:
    P.load_dotenv()


# ------------------------------------------------------------------ commands

def cmd_env(args) -> int:
    from . import d4j
    from .config import ModelConfig

    print(f"root            : {P.ROOT}")
    jh = P.java_home()
    print(f"java 11         : {jh or 'NOT FOUND (brew install openjdk@11)'}")
    print(f"defects4j       : {P.D4J_HOME}")
    print(f"defects4j state : {d4j.status()}")
    if args.model:
        mc = ModelConfig.load(args.model)
        import os

        key = os.environ.get(mc.api_key_env, "")
        print(f"model           : {mc.name} @ {mc.base_url}")
        if mc.needs_api_key:
            status = "set" if key else "MISSING"
        else:
            status = "set" if key else "not required (local endpoint)"
        print(f"api key ({mc.api_key_env}): {status}")
    return 0


def cmd_select_bugs(args) -> int:
    from .config import ExperimentConfig
    from .selection import run_selection

    exp = ExperimentConfig.load(args.experiment)
    run_selection(
        exp.protocol,
        projects=args.projects.split(",") if args.projects else None,
        limit=args.limit,
        force=args.force,
    )
    return 0


def cmd_split(args) -> int:
    from .config import ExperimentConfig
    from .selection import write_outputs

    exp = ExperimentConfig.load(args.experiment)
    write_outputs(exp.protocol, force_split=args.force)
    return 0


def cmd_run(args) -> int:
    from .config import ExperimentConfig, ModelConfig
    from .runner import run_experiment
    from .selection import load_bugs

    exp = ExperimentConfig.load(args.experiment)
    if args.configs:
        exp = type(exp)(protocol=exp.protocol, configurations=tuple(args.configs.split(",")))
    model = ModelConfig.load(args.model)
    bugs = load_bugs(
        split=args.split, keys=args.bugs.split(",") if args.bugs else None
    )
    if not bugs:
        print("no bugs selected; run `favor select-bugs` first", file=sys.stderr)
        return 1
    out = args.out or (P.RUNS / f"{Path(args.model).stem}-{args.split or 'custom'}")
    from .runner import CHECKOUT_CACHE

    run_experiment(
        exp, model, bugs, out,
        workers=args.workers,
        resume=not args.no_resume,
        keep_work=args.keep_work,
        checkout_cache=(
            CHECKOUT_CACHE if args.checkout_cache is None else args.checkout_cache
        ),
    )
    return 0


def cmd_analyze(args) -> int:
    from .analyze import analyze

    out = args.out or (P.ANALYSIS / Path(args.runs).name)
    analyze(args.runs, out, labels_path=args.labels)
    return 0


def cmd_label(args) -> int:
    from .labeling import label_cli

    return label_cli(args.runs, args.labeler, args.labels)


def cmd_smoke(args) -> int:
    from .smoke import run_smoke

    return run_smoke(out_dir=args.out)


def main(argv: list[str] | None = None) -> int:
    _load_env()
    ap = argparse.ArgumentParser(prog="favor", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    default_exp = str(P.ROOT / "configs" / "experiment.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("env", help="check the environment")
    p.add_argument("--model", help="model YAML to check")
    p.set_defaults(fn=cmd_env)

    p = sub.add_parser("select-bugs", help="Table-4 bug screening (resumable)")
    p.add_argument("--experiment", default=default_exp)
    p.add_argument("--projects", help="comma-separated Defects4J project ids")
    p.add_argument("--limit", type=int, help="screen at most N bugs (pilot)")
    p.add_argument("--force", action="store_true", help="re-screen cached verdicts")
    p.set_defaults(fn=cmd_select_bugs)

    p = sub.add_parser("split", help="rewrite bugs.jsonl, table4.json, split.json")
    p.add_argument("--experiment", default=default_exp)
    p.add_argument("--force", action="store_true",
                   help="redraw split.json even if it exists (invalidates any frozen split)")
    p.set_defaults(fn=cmd_split)

    p = sub.add_parser("run", help="run configurations on bugs")
    p.add_argument("--experiment", default=default_exp)
    p.add_argument("--model", required=True, help="configs/models/*.yaml")
    p.add_argument("--split", choices=["dev", "test"], help="use data/split.json")
    p.add_argument("--bugs", help="comma-separated bug keys (e.g. Lang-59,Cli-12)")
    p.add_argument("--configs", help="comma-separated Table-5 names (default: experiment.yaml)")
    p.add_argument("--out", help="run directory (default: runs/<model>-<split>)")
    p.add_argument("--workers", type=int, default=1,
                   help="parallel tasks; wall-clock timings are only "
                        "interpretable at 1 (DESIGN.md)")
    p.add_argument("--no-resume", action="store_true")
    p.add_argument("--keep-work", action="store_true",
                   help="do not delete Defects4J checkouts when the sweep ends")
    p.add_argument("--checkout-cache", type=int, default=None,
                   help="checkouts kept per worker before the oldest is evicted "
                        "(default: runner.CHECKOUT_CACHE)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("analyze", help="RQ1-RQ4 tables and figures")
    p.add_argument("--runs", required=True)
    p.add_argument("--labels", help="data/labels.jsonl (correctness labels)")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("label", help="correctness labeling of plausible patches")
    p.add_argument("--runs", required=True)
    p.add_argument("--labeler", required=True)
    p.add_argument("--labels", default=str(P.DATA / "labels.jsonl"))
    p.set_defaults(fn=cmd_label)

    p = sub.add_parser("smoke", help="offline end-to-end check (no API, no Defects4J)")
    p.add_argument("--out", default=str(P.RUNS / "smoke"))
    p.set_defaults(fn=cmd_smoke)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
