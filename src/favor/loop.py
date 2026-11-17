"""Algorithm 1 — the FaVoR loop on one bug, covering every Table-5 row.

The baseline CR is the special case with k=0 and m=1. Independent sampling
drops the conversation and the memory: every call uses the initial prompt
alone. A syntactic (L2) repeat skips validation but counts toward its class in
memory; a plausible patch ends the run; a run also stops once B patches are
validated. A restart clears the conversation, not the memory (unless the
configuration does not persist it)."""

from __future__ import annotations

import random
import time

from .config import LoopConfig, Protocol
from .failure_class import FailureClass, class_hash
from .java_ast import l1_key, l2_key
from .memory import FailureMemory
from .parsing import parse_response
from .records import BugRecord, CallRecord, PatchRecord, RunRecord
from . import prompts


def _join(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p)


def run_repair(
    bug: BugRecord,
    cfg: LoopConfig,
    proto: Protocol,
    llm,
    validator,
    run_idx: int,
    patch_log=None,
    call_log=None,
) -> RunRecord:
    seed = proto.run_seed(bug.key, cfg.name, run_idx)
    rng = random.Random(seed)
    t_start = time.monotonic()

    memory = FailureMemory(proto.diff_max_lines, proto.score_weights)
    seen_l2: dict[str, int] = {}              # L2 key -> first patch_index
    class_by_patch: dict[int, FailureClass] = {}
    patch_records: list[PatchRecord] = []

    used = 0
    patch_index = 0
    turn = 0
    conv_id = 0
    conv: list[dict] = []                     # messages after the system prompt
    conv_patch_indexes: list[int] = []        # patches generated in current conversation
    pending_feedback: str | None = None
    first_plausible: int | None = None
    plausible_code: str | None = None

    trig = bug.trigger_tests[0]
    init = prompts.initial_prompt(
        bug.method_src, trig.test_id, trig.failing_line_src, trig.error_message
    )
    trigger_ids = {t.test_id for t in bug.trigger_tests}

    totals = {"prompt_tokens": 0, "completion_tokens": 0, "latency": 0.0,
              "compile": 0.0, "test": 0.0, "calls": 0}

    def log_patch(rec: PatchRecord) -> None:
        patch_records.append(rec)
        if patch_log is not None:
            patch_log.write(rec.to_json())

    while used < proto.budget_b and first_plausible is None:
        turn += 1

        # --- select memory entries anew at every turn (Section 4.4) ---
        selected_entries = []
        mem_all: list[str] = []
        mem_selected: list[str] = []
        if cfg.memory == "full":
            conv_classes = {
                class_by_patch[i].key()
                for i in conv_patch_indexes
                if i in class_by_patch and class_by_patch[i] is not None
            }
            selected_entries = memory.select(cfg.selection, proto.k, turn, conv_classes, rng)
            mem_all = [class_hash(e.cls) for e in memory.entries.values()]
            mem_selected = [class_hash(e.cls) for e in selected_entries]
        mem_block = prompts.memory_block(selected_entries)

        # --- build the message list ---
        system = {"role": "system", "content": prompts.SYSTEM_PROMPT}
        if cfg.regime == "independent":
            user = _join(init, prompts.request_block(cfg.m, cfg.vs, bug.method_name))
            messages = [system, {"role": "user", "content": user}]
        else:
            if pending_feedback is None:  # first turn of a conversation
                user = _join(init, mem_block, prompts.request_block(cfg.m, cfg.vs, bug.method_name))
            else:
                user = _join(
                    mem_block,
                    pending_feedback,
                    prompts.request_block(cfg.m, cfg.vs, bug.method_name, again=True),
                )
            conv.append({"role": "user", "content": user})
            messages = [system] + conv

        res = llm.chat(
            messages,
            temperature=cfg.temperature,
            seed=seed + turn,
            max_tokens=proto.max_output_tokens,
            tag=f"{bug.key}|{cfg.name}|run{run_idx}|turn{turn}",
        )
        totals["prompt_tokens"] += res.prompt_tokens
        totals["completion_tokens"] += res.completion_tokens
        totals["latency"] += res.latency_s
        totals["calls"] += 1

        parsed = parse_response(res.text, cfg.m)
        if call_log is not None:
            call_log.write(
                CallRecord(
                    bug=bug.key, config=cfg.name, run_idx=run_idx, turn=turn,
                    conv_id=conv_id, prompt_tokens=res.prompt_tokens,
                    completion_tokens=res.completion_tokens, latency_s=res.latency_s,
                    cached=res.cached, n_parsed=len(parsed),
                    n_parse_ok=sum(1 for p in parsed if p.parse_ok),
                ).to_json()
            )

        # --- validate in model order (Algorithm 1, lines 9-15) ---
        feedback_lines: list[str] = []
        turn_patch_indexes: list[int] = []
        any_parse_fail = False
        for j, pp in enumerate(parsed):
            base = dict(
                bug=bug.key, config=cfg.name, run_idx=run_idx, patch_index=patch_index,
                turn=turn, conv_id=conv_id, within_call_index=j, parse_ok=pp.parse_ok,
                code=pp.code, probability=pp.probability,
                context_patch_indexes=list(conv_patch_indexes),
                memory_classes_all=mem_all, memory_classes_selected=mem_selected,
            )
            if used >= proto.budget_b:
                # budget ran out mid-turn: logged, neither validated nor reported (4.6)
                log_patch(PatchRecord(**base, l1=None, l2=None, l2_duplicate_of=None,
                                      validated=False, status="budget_exhausted",
                                      l4=None, class_hash=None,
                                      budget_used_after=used))
                patch_index += 1
                continue
            if not pp.parse_ok:
                any_parse_fail = True
                log_patch(PatchRecord(**base, l1=None, l2=None, l2_duplicate_of=None,
                                      validated=False, status="unparseable",
                                      l4=None, class_hash=None,
                                      budget_used_after=used))
                patch_index += 1
                continue

            k1 = l1_key(pp.code)
            k2 = l2_key(pp.code, alpha_rename=proto.l2_alpha_rename)
            if k2 in seen_l2:
                earlier = seen_l2[k2]
                ecls = class_by_patch.get(earlier)
                if ecls is not None:
                    class_by_patch[patch_index] = ecls  # inherits L3/L4 class (5.7)
                    if cfg.memory == "full":
                        memory.inherit(ecls, turn)
                line = prompts.repeat_line(j + 1, earlier)
                feedback_lines.append(line)
                log_patch(PatchRecord(**base, l1=k1, l2=k2, l2_duplicate_of=earlier,
                                      validated=False, status="repeat",
                                      l4=ecls.to_json() if ecls else None,
                                      class_hash=class_hash(ecls) if ecls else None,
                                      feedback_line=line,
                                      budget_used_after=used))
                turn_patch_indexes.append(patch_index)
                patch_index += 1
                continue

            seen_l2[k2] = patch_index
            out = validator.validate(pp.code)
            used += 1
            totals["compile"] += out.compile_s
            totals["test"] += out.test_s
            class_by_patch[patch_index] = out.cls

            if out.status == "plausible":
                first_plausible = patch_index
                plausible_code = pp.code
                log_patch(PatchRecord(**base, l1=k1, l2=k2, l2_duplicate_of=None,
                                      validated=True, status="plausible",
                                      l4=out.cls.to_json(), class_hash=class_hash(out.cls),
                                      compile_s=out.compile_s, test_s=out.test_s,
                                      budget_used_after=used))
                patch_index += 1
                break

            if cfg.memory == "full":
                memory.observe(out.cls, bug.method_src, pp.code, turn)

            still_trigger = out.still_fails_trigger(trigger_ids)
            summary = out.signatures[0].summary() if out.signatures else None
            single = prompts.feedback_single(
                out.status, out.compile_error, still_trigger, summary
            )
            line = single if cfg.m == 1 else prompts.feedback_line_multi(j + 1, single)
            named = sorted(
                {s.test_id for s in out.signatures if s.test_id in line}
                | ({t for t in trigger_ids} if still_trigger else set())
            )
            feedback_lines.append(line)
            log_patch(PatchRecord(**base, l1=k1, l2=k2, l2_duplicate_of=None,
                                  validated=True, status=out.status,
                                  l4=out.cls.to_json(), class_hash=class_hash(out.cls),
                                  signatures=[s.to_json() for s in out.signatures],
                                  compile_error=out.compile_error,
                                  compile_s=out.compile_s, test_s=out.test_s,
                                  feedback_line=line, feedback_named_tests=named,
                                  budget_used_after=used))
            turn_patch_indexes.append(patch_index)
            patch_index += 1

        if first_plausible is not None:
            break

        # --- append (P, F) to the conversation; restart when it is full ---
        if cfg.regime == "loop":
            conv.append({"role": "assistant", "content": res.text})
            conv_patch_indexes.extend(turn_patch_indexes)
            if feedback_lines:
                pending_feedback = "\n".join(feedback_lines)
            elif any_parse_fail:
                pending_feedback = prompts.unparseable_line()
            else:
                pending_feedback = "The budget for this turn was exhausted."
            assistant_turns = sum(1 for msg in conv if msg["role"] == "assistant")
            if assistant_turns >= proto.max_conversation_length:
                conv = []                       # restart from I; M persists (4.3)
                conv_patch_indexes = []
                pending_feedback = None
                conv_id += 1
                if not cfg.persist:
                    memory.clear()              # CR+TopK-reset ablation

    return RunRecord(
        bug=bug.key,
        config=cfg.name,
        run_idx=run_idx,
        seed=seed,
        plausible_found=first_plausible is not None,
        first_plausible_patch_index=first_plausible,
        plausible_code=plausible_code,
        validated_count=used,
        validated_to_first_plausible=used if first_plausible is not None else None,
        generated_count=patch_index,
        llm_calls=totals["calls"],
        prompt_tokens=totals["prompt_tokens"],
        completion_tokens=totals["completion_tokens"],
        gen_latency_s=round(totals["latency"], 3),
        compile_s_total=round(totals["compile"], 3),
        test_s_total=round(totals["test"], 3),
        turns=turn,
        conversations=conv_id + 1,
        wall_s=round(time.monotonic() - t_start, 3),
    )
