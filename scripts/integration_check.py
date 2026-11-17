"""End-to-end integration check on one real Defects4J bug, no LLM API:

1. screen the bug (Table-4 pipeline: single-function, builds, fixed passes,
   flake screen, timing),
2. run the CR loop with a scripted fake model whose third answer is the
   developer's own fixed method -> the real validator must reject two wrong
   patches with proper failure classes and accept the fix as plausible.

Usage: .venv/bin/python scripts/integration_check.py [Project Bug] [--fast]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from favor import paths as P  # noqa: E402
from favor.config import Protocol  # noqa: E402
from favor.configurations import table5  # noqa: E402
from favor.llm import FakeChatClient  # noqa: E402
from favor.loop import run_repair  # noqa: E402
from favor.records import BugRecord  # noqa: E402
from favor.selection import screen_bug  # noqa: E402
from favor.storage import JsonlWriter  # noqa: E402
from favor.validation import Validator  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pid, bid = (args[0], int(args[1])) if len(args) >= 2 else ("Lang", 59)
    fast = "--fast" in sys.argv
    proto = Protocol(budget_b=5, flaky_runs=2 if fast else 5)

    print(f"== screening {pid}-{bid} (flaky_runs={proto.flaky_runs}) ==")
    status = screen_bug(pid, bid, proto, force=False)
    if not status.get("kept"):
        print(f"not kept: [{status.get('step')}] {status.get('reason')}")
        return 1
    bug = BugRecord.from_json(status["record"])
    print(f"kept: {bug.method_name}{bug.method_params} in {bug.file_rel}")
    print(f"trigger: {bug.trigger_tests[0].test_id}")
    print(f"fixed-suite time: {bug.fixed_test_time_s}s")

    wrong1 = bug.method_src  # re-submitting the buggy method: must fail the trigger
    wrong2 = bug.method_src.replace("{", "{ int __favor_unknown = NO_SUCH_SYMBOL;", 1)

    def script(messages, idx):
        code = [wrong1, wrong2, bug.fixed_method_src][min(idx, 2)]
        return f"```java\n{code}\n```"

    print("\n== CR loop with scripted model (wrong, compile-error, developer fix) ==")
    cfg = table5(proto)["CR"]
    v = Validator(bug, proto, P.WORK / "integration" / bug.key)
    v.prepare()
    out = P.RUNS / "integration"
    rec = run_repair(bug, cfg, proto, FakeChatClient(script), v, run_idx=0,
                     patch_log=JsonlWriter(out / "patches.jsonl"),
                     call_log=JsonlWriter(out / "calls.jsonl"))

    print(f"plausible found : {rec.plausible_found}")
    print(f"validated       : {rec.validated_count}")
    print(f"compile+test s  : {rec.compile_s_total + rec.test_s_total:.1f}")
    from favor.storage import read_jsonl

    patches = [p for p in read_jsonl(out / "patches.jsonl") if p["bug"] == bug.key]
    for p in patches[-3:]:
        print(f"  patch {p['patch_index']}: {p['status']}"
              + (f" ({p['compile_error']})" if p.get("compile_error") else "")
              + (f" sig={p['signatures'][0]['test_id']}" if p.get("signatures") else ""))
    ok = (
        rec.plausible_found
        and rec.validated_count == 3
        and patches[0]["status"] == "fail"
        and patches[1]["status"] == "compile_error"
        and patches[2]["status"] == "plausible"
    )
    print("\nINTEGRATION:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
