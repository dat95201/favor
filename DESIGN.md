# DESIGN — proposal-to-code map

The authoritative text is the proposal (*FaVoR — Abstract & Introduction,
revised framing*, 2026-11-16). This file maps each section onto the code and
records every decision the proposal leaves `[TBD]`/`[CHOOSE]`, with the default
this harness implements. **All of these are development-split choices**: run
the dev split, revisit them, then freeze `configs/experiment.yaml` (commit the
hash) before the first test-split run (Section 5.10).

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
| Margin delta (5.8) | **0.03** (3 repair-rate points) | judgment call, fixed before test-split runs; report as rate and bug count |
| Runs per bug r (5.3) | **3** | compute-bound; confirmatory configs keep r if ablations must drop to r' < r |
| Second phase of ChatRepair (4.2 CHOOSE) | **dropped** — a run stops at its first plausible patch | the endpoint is cost to first plausible |
| Feedback granularity for m>1 (4.6 CHOOSE) | **one line per validated patch** (L2-filtered ones listed as repeats) | keeps feedback linear in m |
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

## Faithfulness notes (the proposal's [verify] items)

- The initial prompt and the single-patch feedback follow the released
  ChatRepair prompts in shape (buggy function, failing test, failing line,
  error; "still not correct" vs. naming a *different* failing test). The
  proposal requires a token-for-token check against the released ChatRepair
  code before freezing — do that against `prompts.py` and record deviations in
  Table 2.
- Conversation length L=3 counts *attempts per conversation* (assistant
  turns), after which the loop restarts from the initial prompt. Restarts
  clear the conversation, never the memory (except `CR+TopK-reset`).
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
  is named in that feedback (checked against the actual text).

## Departures from the proposal's environment section

- **No containers.** Validation runs locally through the Defects4J CLI (the
  user's call); one checkout per (worker, bug), original file restored after
  every candidate. Same-machine interleaving of configurations (seeded
  shuffle) still holds (5.5).
- Per-test timing (full vs fail-fast protocols) is approximated as described
  above.
