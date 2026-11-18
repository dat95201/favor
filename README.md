# FaVoR — failure-aware prompting and verbalized diversification for conversational program repair

Experiment harness for the FaVoR study: a ChatRepair-style repair loop whose
costly repeats (patches that differ in text but fail the same way) are
measured, traced to their origin, and attacked with two mechanisms —

- **Failure-aware prompting** (Sections 4.3–4.4): a memory of earlier attempts
  and their failure classes, kept across conversation restarts, with the top-k
  most informative entries rendered into every prompt;
- **Verbalized diversification** (Section 4.5): each turn asks for m distinct
  patches with their verbalized probabilities (Verbalized Sampling).

The study design is in the proposal (`FaVoR — Abstract & Introduction`); this
repository implements all of it: the four nested duplication levels (L1
textual, L2 syntactic, L3 semantic*, L4 test-equivalent), the L2 pre-validation
filter every configuration shares, the twelve Table-5 configurations, the
origin analysis of L4 repeats, cost-to-first-plausible as the primary
efficiency endpoint, and repair-rate non-inferiority as the guardrail.

*L3 needs a generated test suite and ships as a documented adapter stub
(`src/favor/semantics.py`); no RQ depends on it.

## Setup (local, no containers)

Needs macOS or Linux with `git`, `svn`, `perl`, `cpanm`, Python 3.10+ and a
**Java 11 JDK** (Defects4J 3 requires Java 11).

```bash
bash scripts/setup.sh        # Python venv + Defects4J 3.0.1 into benchmarks/
cp .env.example .env         # then put the API key(s) in .env
.venv/bin/favor env          # checks Java, Defects4J, model config
.venv/bin/favor smoke        # offline end-to-end check: no API, no Defects4J
```

## Models

Any OpenAI-compatible endpoint works; a model is one YAML in `configs/models/`:

| config | model | where |
|---|---|---|
| `qwen25-coder-7b.yaml` | qwen2.5-coder:7b | local, Ollama |
| `gpt-luna-6.yaml` | gpt-luna-6 | cloud, OpenAI-compatible provider |

Every request, response, token count and latency is logged (`llm.jsonl`), and
responses are cached under `cache/` keyed by (model, messages, temperature,
seed, max_tokens): a re-run with the same seeds replays for free.

## The study, stage by stage

```bash
# 1. bug selection (Table 4) — slow, resumable; writes data/bugs.jsonl,
#    data/selection/table4.json, data/split.json (stratified dev/test)
.venv/bin/favor select-bugs

# 2. development split: pick k, m, B, weights, delta; freeze configs/experiment.yaml
.venv/bin/favor run --model configs/models/qwen25-coder-7b.yaml --split dev --workers 4
.venv/bin/favor analyze --runs runs/qwen25-coder-7b-dev

# 3. test split (after freezing the protocol)
.venv/bin/favor run --model configs/models/qwen25-coder-7b.yaml --split test --workers 4

# 4. correctness labels (two labelers, then resolution; Section 5.9)
.venv/bin/favor label --runs runs/qwen25-coder-7b-test --labeler dat

# 5. analysis: RQ1-RQ4 tables and figures
.venv/bin/favor analyze --runs runs/qwen25-coder-7b-test --labels data/labels.jsonl
```

`favor run` interleaves configurations on the same bug in a seeded random
order (Section 5.5), resumes from `runs.jsonl`, and accepts `--configs
CR,FaVoR` and `--bugs Lang-59,Cli-12` for partial runs.

A real-infrastructure check on one bug, no API:

```bash
.venv/bin/python scripts/integration_check.py Lang 59 --fast
```

## What lands where

| path | content |
|---|---|
| `data/bugs.jsonl` | selected single-function bugs with the buggy method, trigger tests, timings |
| `data/selection/table4.json` | how many bugs each screen removed |
| `data/split.json` | dev/test split (stratified by project, fixed seed) |
| `runs/<exp>/runs.jsonl` | one record per (bug, configuration, run) |
| `runs/<exp>/patches.jsonl` | every generated patch: L1/L2 keys, L4 class, signatures, timing, origin context |
| `runs/<exp>/calls.jsonl`, `llm.jsonl` | every LLM call with tokens and latency |
| `analysis/<exp>/` | `summary.csv`, `rq1_*`, `rq2_cost.csv`, `rq3_noninferiority.csv`, `rq4_survival.csv`, figures |

## Documentation

- [DESIGN.md](DESIGN.md) — proposal-to-code map and every resolved [TBD]/[CHOOSE]
- [RUNBOOK.md](RUNBOOK.md) — operational detail per stage, compute estimates, troubleshooting

## Tests

```bash
.venv/bin/python -m pytest          # unit tests, offline
.venv/bin/favor smoke               # loop invariants + analysis, offline
```
