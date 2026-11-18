"""Configuration objects: model endpoints, the experiment protocol (Section 5),
and loading from YAML. Every [TBD]/[CHOOSE] of the proposal that needed a value
has a default here and is documented in DESIGN.md; all of them are meant to be
frozen on the development split before any test-split run (Section 5.10)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


@dataclass(frozen=True)
class ModelConfig:
    """One LLM endpoint, reached through an OpenAI-compatible chat API."""

    name: str                                # model id sent to the API
    base_url: str = "http://localhost:11434/v1"
    api_key_env: str = "OLLAMA_API_KEY"      # env var holding the key
    max_output_tokens: int = 4096
    supports_seed: bool = True               # pass `seed` when the API accepts it
    request_timeout_s: float = 600.0
    max_retries: int = 5
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def api_key(self) -> str:
        # The OpenAI client rejects an empty key, so local endpoints that ignore
        # it entirely (Ollama) still get a non-empty placeholder.
        return os.environ.get(self.api_key_env, "") or "unset"

    @property
    def needs_api_key(self) -> bool:
        """False for endpoints served on this machine, which authenticate
        nobody; only then is an unset api_key_env worth reporting."""
        return (urlparse(self.base_url).hostname or "") not in (
            "localhost",
            "127.0.0.1",
            "::1",
        )

    @staticmethod
    def load(path: str | Path) -> "ModelConfig":
        raw = yaml.safe_load(Path(path).read_text())
        return ModelConfig(**raw)


@dataclass(frozen=True)
class LoopConfig:
    """One row of Table 5: how patches are produced for one bug."""

    name: str
    regime: str          # "loop" | "independent"
    memory: str          # "none" | "full"
    selection: str       # "none" | "all" | "random" | "topk"
    persist: bool        # memory kept across conversation restarts
    m: int               # patches requested per LLM call
    vs: str              # "none" | "prob" | "noprob"  (verbalized sampling)
    temperature: float = 1.0
    instr_only: bool = False  # "do not repeat" instruction without any entries

    def __post_init__(self) -> None:
        assert self.regime in ("loop", "independent"), self.regime
        assert self.memory in ("none", "full"), self.memory
        assert self.selection in ("none", "all", "random", "topk"), self.selection
        assert self.vs in ("none", "prob", "noprob"), self.vs
        if self.memory == "none":
            assert self.selection == "none", f"{self.name}: selection without memory"
        if self.vs == "none":
            assert self.m == 1, f"{self.name}: m>1 requires verbalized sampling"


@dataclass(frozen=True)
class Protocol:
    """Experiment protocol (Section 5). Defaults are development-split choices;
    see DESIGN.md for the mapping to the proposal's [TBD]/[CHOOSE] markers."""

    budget_b: int = 20                 # validated patches per run (Section 5.5)
    max_conversation_length: int = 3   # L, ATTEMPTS per conversation (ChatRepair);
                                       # an attempt = a validated patch or an L2 repeat
    max_llm_calls: int = 200           # hard termination cap per run (disclosed):
                                       # repeats/unparseable output consume no budget
    k: int = 3                         # memory entries rendered per turn (4.4)
    m: int = 3                         # patches per call under VS (4.5)
    runs_per_bug: int = 3              # r (5.3)
    temperature: float = 1.0           # all configurations except CR-hot (5.3)
    hot_temperature: float = 1.3       # CR-hot (Table 5)
    diff_max_lines: int = 16           # memory-entry diff truncation (4.3)
    score_weights: tuple[float, float, float] = (1.0, 1.0, 1.0)  # (freq, recency, absent)
    seed: int = 20261117               # global seed; per-run seeds derive from it
    delta: float = 0.07                # non-inferiority margin, repair-rate points (5.8);
                                       # power-driven: at r=3, N~100-300, delta=0.03 has
                                       # ~0.2-0.4 power when arms are identical, ~0.9 at 0.07
    dev_fraction: float = 0.2          # development split share (5.2)
    flaky_runs: int = 5                # repeats in the flakiness screen (5.2)
    slow_suite_limit_s: float = 600.0  # exclude bugs whose relevant tests exceed this (5.2)
    test_timeout_factor: float = 5.0   # per-run test timeout = factor x fixed time (5.5)
    test_timeout_min_s: float = 300.0
    l2_alpha_rename: bool = True       # L2 normalization renames local variables (5.6)
    signature_stack_frames: int = 1    # frames kept in the assertion location (5.6)
    n_bootstrap: int = 10000           # paired bootstrap resamples (5.8)
    max_output_tokens: int = 4096      # generation cap (5.3)

    def run_seed(self, bug_key: str, run_idx: int) -> int:
        """Stable per-(bug, run) seed, shared by every configuration: common
        random numbers across arms, so paired comparisons keep the variance
        reduction the pairing buys (identical prompts in different arms draw
        identical responses)."""
        import hashlib

        h = hashlib.sha256(f"{self.seed}|{bug_key}|{run_idx}".encode()).digest()
        return int.from_bytes(h[:4], "big")


@dataclass(frozen=True)
class ExperimentConfig:
    """A full experiment: protocol + which Table-5 configurations to run."""

    protocol: Protocol
    configurations: tuple[str, ...]

    @staticmethod
    def load(path: str | Path) -> "ExperimentConfig":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        proto_raw = dict(raw.get("protocol", {}))
        if "score_weights" in proto_raw:
            proto_raw["score_weights"] = tuple(proto_raw["score_weights"])
        protocol = Protocol(**proto_raw)
        confs = tuple(raw.get("configurations", []))
        return ExperimentConfig(protocol=protocol, configurations=confs)

    def dump(self) -> dict:
        d = {"protocol": asdict(self.protocol), "configurations": list(self.configurations)}
        d["protocol"]["score_weights"] = list(self.protocol.score_weights)
        return d
