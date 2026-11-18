# DESIGN — proposal-to-code map

The authoritative text is the proposal (*FaVoR — Abstract & Introduction,
revised framing*, 2026-11-16), committed at `docs/proposal-2026-11-16.pdf`.
This file maps each section onto the code and records every decision the
proposal leaves `[TBD]`/`[CHOOSE]`, with the default this harness implements.
**All of these are development-split choices**: run the dev split, revisit
them, then freeze `configs/experiment.yaml` (commit the hash) before the first
test-split run (Section 5.10).

The 2026-11-18 external review's blockers and their resolutions are in the
"Review fixes" section at the end; the items that require amending the
proposal text (not just the code) are listed under "Proposal amendments
required".

## Section -> module

| Proposal | Code |
|---|---|
| 2.2 validation cost, full/fail-fast protocols | `validation.py`, `d4j.py` (phase timing; see "fail-fast" below) |
| 4.1–4.2 baseline loop (ChatRepair re-implementation) | `loop.py`, `prompts.py` |
| 4.3 failure memory M, entries (c, a, n, t_first, t_last) | `memory.py` |
| 4.4 top-k fact selection, score, rendering format | `memory.py` (`scored`, `select`), `prompts.memory_block` |
| 4.5 verbalized diversification, parsing, within-turn L2 filter | `prompts.request_block`, `parsing.py`, `loop.py` |
| 4.6 feedback (single + multi-patch) | `prompts.py`, `loop.py` |
| 4.7 Algorithm 1 | `loop.run_repair` (line-by-line; IS regime = no conv, no memory) |
| 5.2 benchmark & bug selection (Table 4) | `extraction.py`, `selection.py` |
| 5.3 model & generation settings | `config.ModelConfig`, `llm.py` (full logging, seeds, cache) |
| 5.4 configurations (Table 5) | `configurations.table5` |
| 5.5 budget, validation environment, interleaving | `runner.py`, `validation.py` |
| 5.6 duplication levels L1–L4, failure signatures | `java_ast.py` (L1/L2), `failure_class.py` (L4), `semantics.py` (L3 stub) |
| 5.7 metrics: DR_L, S_L(n), W_L, cost-to-first-plausible, origins | `metrics.py`, `origin.py` |
| 5.8 statistics: paired bootstrap, non-inferiority | `stats.py` |
| 5.9 correctness assessment | `labeling.py` (two blind labelers, kappa, resolution rows) |
| 5.10 hyperparameters & protocol freezing | `configs/experiment.yaml` + this file |

## Resolved [TBD] / [CHOOSE] defaults

| Item (proposal) | Default here | Rationale / how to revisit |
|---|---|---|
| Defects4J version (5.2) | **3.0.1** (`scripts/setup.sh`, tag `v3.0.1`) | newest release line |
| Model (5.3) | configurable: qwen2.5-coder:7b (Ollama), gpt-luna-6 (cloud), suggestions in `configs/models/` | per-model YAML |
| Memory attempt representation (4.3 CHOOSE) | **unified diff**, truncated to `diff_max_lines: 16` | no extra LLM call, cannot misdescribe the patch |
| Scoring function (4.4) | z-scored log-frequency + recency + absence, weights (1,1,1), ties by earlier t_first | `score_weights` in experiment.yaml; ablate by zeroing a weight |
| k, m (5.10) | k=3, m=3 (from {1,3,5} × {3,5}) | pick on dev by FaVoR's cost to first plausible |
| Budget B (5.10 CHOOSE) | **B=20** validated patches | re-fit on dev: smallest B where CR reaches 90% of its plausible repairs |
| Margin delta (5.8) | **0.07** (7 repair-rate points) | power-driven judgment call: at r=3, N~100-300, delta=0.03 has ~0.2-0.4 power when the arms are truly identical, ~0.9 at 0.07 (review simulation); alternatives are raising r or a run-level GLMM. Fixed before test-split runs; report as rate and bug count |
| Runs per bug r (5.3) | **3** | compute-bound; confirmatory configs keep r if ablations must drop to r' < r |
| Second phase of ChatRepair (4.2 CHOOSE) | **dropped** — a run stops at its first plausible patch | the endpoint is cost to first plausible |
| Feedback granularity for m>1 (4.6 CHOOSE) | **one line per validated patch**; an L2-filtered patch replays its twin's line | keeps feedback linear in m; see "repeats are invisible to the model" below |
| Few-shot examples (ChatRepair 4.1) | **0-shot** | ChatRepair's headline runs are 1-shot (same-project historical fixes), but its own ablation study (5.3) is 0-shot "since it can best illustrate the effect of individual components" — which is exactly what every FaVoR arm is doing. **Dev-split consequence:** Table 4 puts 0-shot at 21.86 avg. tries vs 9.91 at 1-shot, so B=20 censors far more at 0-shot; re-fit B *and* revisit this row together |
| Failure signature (5.6) | exception type, normalized message, expected/actual when present, assertion location with **1 stack frame** | `signature_stack_frames` |
| L2 normalization (5.6 TBD) | comments/whitespace dropped + **consistent renaming of method-local identifiers** (params, locals, catch/for/lambda/resource names) | `l2_alpha_rename: false` turns renaming off |
| Test timeout (5.5) | max(300 s, 5 × fixed-version relevant-test time) per validation run | `test_timeout_*` |
| Slow-suite screen (5.2 TBD) | relevant tests > **600 s** on the fixed version | `slow_suite_limit_s` |
| Dev split share (5.2 CHOOSE) | **20%**, stratified by project, seed 20261117 | `dev_fraction`, `seed` |
| CR-hot temperature (Table 5 TBD) | **1.3** | `hot_temperature` |
| Max output tokens (5.3) | 4096 | `max_output_tokens` |
| T_gen / L3 (5.6 TBD) | **stub adapter** (`semantics.py`), L3/W_L3 reported only when enabled | EvoSuite recipe documented in the stub |
| Non-inferiority CI (5.8 TBD) | one-sided **95%** paired bootstrap over bugs, 10 000 resamples | `n_bootstrap` |
| Cost comparison (5.7 TBD) | mean validated-patch count to first plausible, censored at B, paired bootstrap CI; survival curve reported alongside | `analyze.py` rq2/rq4 |
| Memorization check (5.2 CHOOSE) | **not run by default**; GitBug-Java sweep possible later — the selection pipeline only needs a Defects4J-style CLI | discuss as a threat regardless |

## Prompt provenance (adjusted 2026-11-18 to the two cited instruments)

- **Initial prompt I and single-patch feedback**: ChatRepair-shaped (the
  proposal's fidelity requirement, Table 2); the token-for-token check against
  the released ChatRepair code is still owed before freezing.
- **VS request** (`prompts.request_block`, m>1 with probabilities): the
  **VS-Standard** template of Zhang et al., App. G.3 — "Generate {m}
  responses ...", per-response "estimated probability from 0.0 to 1.0 ...
  (relative to the full distribution)", the sampling line "Randomly sample the
  responses from the full distribution.", and "Give ONLY ... no explanations
  or extra text." One documented transport change: the paper returns JSON;
  multi-line Java inside JSON strings breaks small models' escaping, so fixes
  travel in fenced ```java blocks with a `Probability:` line (the paper's F.4
  shows VS is robust to the elicitation format).
- **CR+VS-noProb** instantiates the paper's **Sequence** baseline (same ask,
  no probabilities), which is exactly the RQ4 contrast the arm exists for.
- **Memory block** (`prompts.memory_block`): content exactly as Section 4.4,
  rendered as a **Maniple-style fact section** (Parasaram et al., Fig. 1):
  a `##` markdown heading and the representative diff in a fenced ```diff
  block. Facts are sections with fenced code in that instrument.

## Faithfulness notes (the proposal's [verify] items)

- The initial prompt and the single-patch feedback follow the released
  ChatRepair prompts in shape (buggy function, failing test, failing line,
  error; "still not correct" vs. naming a *different* failing test). The
  proposal requires a token-for-token check against the released ChatRepair
  code before freezing — do that against `prompts.py` and record deviations in
  Table 2.
- **Nothing in the baseline may pre-empt the treatment.** FaVoR's mechanism is
  "tell the model what it already tried"; a CR arm that leaks the same signal
  makes H1 untestable in the conservative direction. Three places were audited
  (2026-11-18), all in `loop.py`/`prompts.py`:
  - *Repeats are invisible to the model.* An L2 repeat skips validation (a cost
    decision), but its feedback is the **twin's line, replayed verbatim** —
    what ChatRepair, which does not deduplicate, would have produced by
    re-running it. Announcing the repeat would have given every arm, the
    baseline included, the anti-duplication signal CR+Instr exists to isolate.
  - *The ask is turn-invariant in every arm.* ChatRepair asks for "the
    correct" fix after every feedback (Fig. 2) and VS-Standard has no
    turn-dependent wording, so `request_block` lost its `again` parameter.
    This cut a nudge on both sides: "please provide **another** fixed version"
    in the m=1 arms, and "**None of the previous fixes is correct.**" in the
    VS arms. Within-call diversity ("each response is a different fixed
    version") stays — that IS the VS treatment, and it is present on turn 1.
  - *The "do not repeat" instruction* appears only in `memory_block` and
    `instruction_only_block` — i.e. only in the arms that are supposed to have
    it.
- **Blast radius of the 2026-11-18 prompt audit** (measured, not argued:
  replay every arm against the scripted model and diff the sent messages and
  the records — `FakeChatClient.calls` makes this a few lines):
  - **IS and IS+VS are byte-identical.** The independent regime never builds
    feedback, so the sampling reference is untouched.
  - **All seven m=1 conversational arms shifted identically** (the same two
    substitutions in CR, CR+Instr, CR-hot, CR+Mem, CR+Rand, CR+TopK,
    CR+TopK-reset), so the mechanism contrasts keep their structure — and
    CR vs CR+Instr becomes a real contrast for the first time.
  - **At the frozen k/m/L the VS arms saw no prompt change at all.** With m=3
    and L=3 attempts, a VS conversation holds exactly one call, so `again`
    never fired and in-conversation feedback never reached them. Two
    consequences: (a) the removed VS nudge was dead code *in this
    configuration* and only matters for sensitivity runs with L > m, and (b)
    **the audit moved CR but not FaVoR**, i.e. it moved the confirmatory
    contrast. Direction is not one-sided: CR lost an unearned duplication
    hint (widens the gap) and gained ChatRepair's relevant-test-code feedback
    (narrows it). Disclose both in the write-up; this landed before any
    test-split run, which is why the freeze gate exists.
  - **No outcome field moved in any arm** — `plausible_found`,
    first-plausible index, validated/generated counts, llm_calls, turns,
    conversations, stopped_reason and seed are identical; only prompt token
    counts (text length) differ. L4 classes and `class_hash` are identical
    everywhere, confirming `failing_line_src` stays out of `key()`.
- Dynamic feedback about a *different* failing test carries all three pieces
  ChatRepair gives (Section 3.2): test name, **relevant test code**, error
  message. The asserting line is resolved from the signature's location at
  validation time (`d4j.test_line_source`, shared with extraction) and rides on
  `FailureSignature.failing_line_src` — deliberately **outside** `key()`, so it
  never splits an L4 class. Without it the feedback is Table 5's
  `TestName+ErrMsg` row (59 fixes), not the `Dynamic` default (64).
- ChatRepair's **second phase** (alternative variations of a plausible patch)
  is dropped because a run stops at its first plausible patch, which is the
  endpoint. Consequence for the write-up: the CR arm reproduces ChatRepair's
  *patch-generation* loop, not its reported repair counts — do not present CR's
  numbers as a replication of Table 1.
- Conversation length L=3 counts **attempts** per conversation — validated
  patches plus L2 repeats — not LLM calls. With m patches per call, a call
  contributes up to m attempts, so every configuration restarts after the same
  number of attempts; counting calls would let the VS arms restart 1/m as
  often and confound H1. Consequence at m=3, L=3: a VS conversation holds one
  call, so in-conversation feedback only exists for m=1 arms; cross-restart
  information flows through the memory alone. Restarts clear the conversation,
  never the memory (except `CR+TopK-reset`).
- The memory block is rendered ONLY into the latest user message; earlier user
  messages are stored without theirs. The model always sees one current
  snapshot of M (at most k entries) — never an accumulation of stale
  snapshots, which would reproduce the too-many-facts failure top-k exists to
  avoid. (The stored conversation therefore differs from the sent one in
  exactly this: past memory blocks are dropped.)
- A patch generated earlier in the same call counts as in-context for origin
  analysis: the model saw it in its own output. Its feedback was not yet
  shown, so a same-call repeat is at most "in context, unreported".
- "Fail-fast" cost (2.2) is *derived* from recorded timing. The local
  validator records phase-level compile and test wall time; per-test times are
  not emitted by `defects4j test`. The derived fail-fast estimate therefore
  needs the optional per-test timing pass (run each relevant test class once on
  the fixed version and reuse the table) — not implemented yet; W_L and the
  primary endpoint (validated-patch count) do not depend on it.
- Unparseable model output: logged, has no class, never enters memory, does
  not consume budget (4.3). The next feedback asks for one ```java block —
  an implementation decision the proposal does not specify.
- For origin analysis, "reported" for compile-error/timeout classes means the
  earlier patch's feedback line was present in context (the line itself
  describes the class); for test classes it means a failing test of the class
  is **named in the actual feedback text** (`feedback_named_tests` is computed
  by substring check against the line). ChatRepair's "still not correct"
  names no test, so repeats after it count as unreported — the conservative
  reading for H1.

## Departures from the proposal's environment section

- **No containers.** Validation runs locally through the Defects4J CLI (the
  user's call); one checkout per (worker, bug), original file restored after
  every candidate; commands run in their own process group and the whole
  group is killed on timeout, so no orphan test JVM outlives its run.
  Residual threat vs the container design: build artifacts persist within a
  (worker, bug) checkout across patches of the same sweep. Report in Threats.
- Per-test timing (full vs fail-fast protocols) is approximated as described
  above; until the optional per-class timing pass exists, the paper should
  claim the **full protocol only**.
- Wall-clock timing is only interpretable at `--workers 1` (parallel builds
  contend for cores); the validated-patch-count endpoint is unaffected.
- Infrastructure failures inside validation (broken checkout, d4j crash) are
  not yet distinguished from patch-caused compile errors (no `harness_error`
  status); known limitation, watch the compile-error classes on the dev split.

## Review fixes (2026-11-18)

Resolutions of the external review, all verified by tests:

| # | Finding | Resolution |
|---|---|---|
| A1 | L counted in LLM calls: VS arms restarted 1/m as often | L now counts attempts (`loop.py`, `attempts_in_conv`); smoke asserts conversations == turns for m=3 |
| A2 | Stale memory blocks accumulated across turns | one current block, injected only into the latest user message; test + smoke assert ≤ 1 block per request |
| A3 | DR at run-end n; mean S-curve carried short runs | `duplicate_rate_at` at fixed n over runs reaching n (`rq1_dr.csv`); `mean_s_curve` averages only live runs; summary DR columns removed |
| A4 | `named` unioned trigger tests; same-call repeats read as lost-at-restart | named = text-checked only; same-call earlier patches are in `context_patch_indexes` |
| A5 | Resume re-appended patch records, deflating DR | last record per (run, patch_index) wins in `runs_from_patches`; runs deduped the same way in analyze |
| — | L2 alpha-rename rewrote member names (`obj.get()` ≡ `obj.put()`) | member names (invocation name, field access, method refs) never renamed; tests pin both directions |
| — | Raw expected/actual leaked JVM ids/paths into L4 | scrubbed with the message normalizers |
| — | No termination guarantee (repeats/unparseable cost nothing) | `max_llm_calls` cap (200), `stopped_reason` recorded, `call_capped_runs` in summary |
| B1 | δ=0.03 underpowered at r=3 | δ=0.07 (see defaults table) |
| B5 | Memorization check open | proxy in summary: share of plausible patches whose L2 equals the developer fix's (`plausible_matches_dev_fix`) |
| C1 | No instruction-only control | new arm `CR+Instr` (tier 1): the "do not repeat" instruction with no entries |
| C2 | Origins lack an opportunity denominator | `rq1_opportunity.csv`: repeat hits on visible vs hidden classes, normalized by availability |
| C3 | absent(e) z-scores to 0 when all entries tie | kept as specified (4.4): when every entry is absent the term has nothing to discriminate and freq/recency break ties; a constant term would change no ranking |
| D | Factorial not analyzed; decisions on every arm; run-level survival; cost averaged over runs | `rq4_factorial.csv` (memory x VS main effects + interaction, paired bootstrap); non-inferior/superior emitted only for FaVoR vs CR; survival and costs aggregate per bug first |
| D | No common random numbers | run seed = f(global seed, bug, run) — shared across arms |
| D | labels keyed by l2 only; alpha mismatch; split redrawn; Table-4 order; orphan JVMs | labels keyed (bug, l2); alpha read from the run's protocol; split preserved unless `favor split --force`; Table-4 order matches `analyze_bug`; process-group kill |
| E | Top-k cutoff regression discontinuity | not implemented; the per-turn `memory_classes_all` / `memory_classes_selected` logs contain everything needed — planned exploratory analysis; check |M| > k frequency on the dev split first |

Declined/deferred: B2 (L3/T_gen stays a stub — amend the text, below), B3
(fail-fast timing pass not built — claim full protocol only), GLMM alternative
for 5.8 (bootstrap kept; revisit if δ=0.07 is contested).

## Proposal amendments required (text, not code)

1. §4.7 Algorithm 1 line 17 and Table 2: define L in **attempts**; the Table-2
   row "Maximum conversation length 3 / 3 / As in [5]" must note the
   attempt-based definition (and its consequence for m>1).
2. §4.4: state that the selected entries replace the previous turn's block
   (one snapshot per prompt), and add the final rendering (fenced diff) to the
   appendix.
3. §4.5 / Appendix: fix the VS variant name (**VS-Standard**) and include the
   exact adapted prompt with the JSON→fenced-block transport note.
4. §5.5: replace "fresh container per patch" with the local protocol +
   process-group isolation; move state-leakage to Threats.
5. §5.6/5.7: either implement T_gen or demote L3/W_L3 to "reported when
   available" and drop T_gen from §5.9's labeling evidence.
6. §2.2/5.5: claim the full validation protocol only (fail-fast derivation
   needs the per-class timing pass).
7. §5.8: δ=0.07 with the power rationale; state r=3 and the planned
   run-level sensitivity analysis.
8. §5.7: add the same-call in-context rule and the text-checked definition of
   "reported"; add the opportunity-normalized origin shares as a secondary
   analysis.
9. Table 5: add CR+Instr (instruction-only control).
10. §5.5: disclose the max_llm_calls termination cap.
11. §4.2/Table 2: state that the CR arm is 0-shot (as in ChatRepair's own
    ablation), that L2 repeats are fed back with their twin's line, and that
    the second phase is dropped — so CR is ChatRepair's loop, not a
    replication of its Table 1.
