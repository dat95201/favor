# RUNBOOK — operating the FaVoR study

Every command assumes the repo root and the venv (`bash scripts/setup.sh` once;
`.venv/bin/` prefixes below). `.env` holds `D4J_HOME` (optional), the API keys
and nothing else secret-free; see `.env.example`.

## 0. Health checks (minutes, offline)

```bash
.venv/bin/favor env                          # Java 11, Defects4J, model YAML
.venv/bin/python -m pytest                   # unit tests
.venv/bin/favor smoke                        # Algorithm-1 invariants + analysis, offline
.venv/bin/python scripts/integration_check.py Lang 59 --fast
                                             # one real bug: screen -> validate 3 patches
```

The integration check must end `INTEGRATION: OK` with patch statuses
`fail -> compile_error -> plausible`.

## 1. Bug selection (Table 4) — slow, resumable

```bash
.venv/bin/favor select-bugs                       # all projects, all active bugs
.venv/bin/favor select-bugs --projects Lang,Cli   # a subset first
.venv/bin/favor select-bugs --limit 50            # pilot
```

Each bug costs two checkouts, two builds and `flaky_runs`(=5)+1 relevant-test
runs; expect minutes per bug and hours–days for the full sweep. Verdicts are
cached per bug in `data/selection/status/`; re-running skips everything cached
(`--force` re-screens). Outputs:

- `data/bugs.jsonl` — kept bugs (buggy method, spans, trigger tests, timings)
- `data/selection/table4.json` — counts per screen, in order
- `data/split.json` — dev/test split (stratified by project, seed from experiment.yaml)

`favor split` rewrites the three outputs from the cached statuses without
re-screening (e.g. after changing `dev_fraction`).

## 2. Development split: tune, then freeze

```bash
.venv/bin/favor run --model configs/models/qwen25-coder-7b.yaml --split dev --workers 4
.venv/bin/favor analyze --runs runs/qwen25-coder-7b-dev
```

What gets fixed here (Section 5.10): `k`, `m` (grid: run with `--configs`
over variants, or edit protocol and use separate `--out` dirs), `budget_b`
(smallest B where CR reaches 90% of its dev plausible repairs), score weights
(zero one term at a time), `delta`, and the pilot checks (parsing success rate
in `calls.jsonl`, failure-class assignment, timing completeness).

**Freeze**: commit `configs/experiment.yaml` + `prompts.py`, record the hash,
then do not touch them for the test split. Any later deviation is reported in
the paper's threats section.

## 3. Test split

```bash
# tier 1 (default in experiment.yaml): confirmatory + mechanism factorial, full r
.venv/bin/favor run --model configs/models/qwen25-coder-7b.yaml  --split test --workers 4
.venv/bin/favor run --model configs/models/gpt-luna-6.yaml --split test --workers 4

# tier 2 (if compute allows): uncomment the arms in experiment.yaml, or pass
# them explicitly; same --out merges via resume
.venv/bin/favor run --model configs/models/qwen25-coder-7b.yaml --split test \
    --configs IS+VS,CR+Rand --out runs/qwen25-coder-7b-test --workers 4
```

The arm tiers live as comments in `configs/experiment.yaml` — scoping the
study is editing that one file (tier 1: IS, CR, CR+Instr, CR+TopK,
CR+TopK-reset, CR+VS, FaVoR). For r' < r on the ablations (Section 5.5),
copy it, set `runs_per_bug`, and pass `--experiment` — note r' in the paper.

Timing caveat: wall-clock numbers (compile/test seconds) are interpretable
only at `--workers 1`; parallel Ant builds contend for cores. The primary
endpoint (validated-patch counts) is unaffected by workers.

- Tasks are (bug x configuration x r runs), interleaved in a seeded random
  order so machine drift favors no configuration (5.5).
- Resume is automatic from `runs.jsonl`; `--no-resume` redoes everything
  (the LLM cache still replays identical requests for free).
- `--workers N` runs N threads, each with its own checkout per bug under
  `work/runs/`. Compilation is the bottleneck; 4 workers is a good default on
  an 8-core machine.
- Compute ceiling: |configurations| x r x N x B validations worst case; the
  mean is far lower because runs stop at the first plausible patch.
- If compute binds (5.5): keep r for CR, FaVoR, IS; drop ablations to r' < r —
  decide and note it before any test-split run.

## 4. Correctness labels (Section 5.9)

```bash
.venv/bin/favor label --runs runs/qwen25-coder-7b-test --labeler dat
.venv/bin/favor label --runs runs/qwen25-coder-7b-test --labeler <second-labeler>
```

One item per distinct (bug, L2 class) over all plausible patches, blind to
configuration; the tool shows the diff against the developer fix and records
label + rationale into `data/labels.jsonl`. Cohen's kappa over the two blind
passes is printed. Resolve disagreements by discussion and append one
`{"bug", "l2", "label", "resolved": true}` row per class (third labeler
decides if needed).

## 5. Analysis

```bash
.venv/bin/favor analyze --runs runs/qwen25-coder-7b-test --labels data/labels.jsonl
```

Writes to `analysis/<run>/`:

| file | feeds |
|---|---|
| `summary.csv` | headline per configuration: repair rates, cost, DR_L, wasted validation, tokens/calls/validations per repaired bug (RQ2, RQ4) |
| `rq1_s_curves.csv`, `s_l4_curves.png` | accumulation curves S_L(n) per level (RQ1) |
| `rq1_origin.csv`, `rq1_repeats.csv` | origin of every L4 repeat (RQ1) |
| `rq2_cost.csv` | paired cost-to-first-plausible vs CR with bootstrap CI (confirmatory row: FaVoR) |
| `rq3_noninferiority.csv` | repair-rate non-inferiority vs CR within delta (confirmatory row: FaVoR) |
| `rq4_survival.csv`, `survival.png` | fraction of runs plausible vs budget spent |

Everything re-derives from `runs/<exp>/*.jsonl`; the analysis never mutates
run logs.

## Troubleshooting

- `favor env` says Defects4J "does not run": `cpanm --local-lib=$HOME/perl5
  --installdeps $D4J_HOME` (the harness adds `~/perl5` to `PERL5LIB` itself).
- Java mismatch: Defects4J 3.x needs a JDK 11 (`brew install openjdk@11`);
  detection order is `$JAVA_HOME`, Homebrew paths, `/usr/lib/jvm`.
- A validator raising "method span does not match": the bug record predates a
  Defects4J update — re-screen with `favor select-bugs --force` for that bug.
- Interrupted `favor run`: just re-run the same command; finished runs are
  skipped, cached LLM calls replay.
- Ollama: `ollama pull qwen2.5-coder:7b && ollama serve`; throughput improves with
  `OLLAMA_NUM_PARALLEL=4` when using `--workers 4`.
- `work/` is scratch (one Defects4J checkout per worker and bug); delete it
  freely between sweeps to reclaim disk — checkouts rebuild on demand.
- A run that ends with `stopped_reason: call_cap` hit the LLM-call ceiling
  (`max_llm_calls`) without spending its budget: the model kept producing
  repeats or unparseable output. `call_capped_runs` in summary.csv counts them.
