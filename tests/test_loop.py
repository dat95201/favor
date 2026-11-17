"""Algorithm-1 invariants, driven by the scripted fake model and mock
validator (the same machinery as `favor smoke`)."""

from favor.config import Protocol
from favor.configurations import table5
from favor.llm import FakeChatClient
from favor.loop import run_repair
from favor.mock import MockValidator, mock_bug
from favor.smoke import POOL, script


class Collector:
    def __init__(self):
        self.rows = []

    def write(self, r):
        self.rows.append(r)


def run(config_name: str, proto: Protocol = None):
    proto = proto or Protocol(budget_b=8, runs_per_bug=1, k=2, m=3)
    cfg = table5(proto)[config_name]
    log = Collector()
    rec = run_repair(
        mock_bug(), cfg, proto, FakeChatClient(script), MockValidator(),
        run_idx=0, patch_log=log,
    )
    return rec, log.rows, proto


def test_cr_finds_fix_and_respects_budget():
    rec, patches, proto = run("CR")
    assert rec.plausible_found
    assert rec.validated_count <= proto.budget_b
    assert rec.validated_to_first_plausible == rec.validated_count


def test_l2_repeats_skip_validation_but_are_logged():
    rec, patches, _ = run("CR")
    repeats = [p for p in patches if p["status"] == "repeat"]
    assert repeats, "A2/B2 variants must be filtered as syntactic repeats"
    assert all(not p["validated"] for p in repeats)
    assert all(p["l2_duplicate_of"] is not None for p in repeats)
    # the repeat inherits the L4 class of the patch it duplicates
    for p in repeats:
        orig = next(q for q in patches if q["patch_index"] == p["l2_duplicate_of"])
        assert p["class_hash"] == orig["class_hash"]


def test_run_stops_at_first_plausible():
    rec, patches, _ = run("FaVoR")
    assert rec.plausible_found
    after = [p for p in patches if p["patch_index"] > rec.first_plausible_patch_index]
    assert all(not p["validated"] for p in after)


def test_restart_keeps_memory_when_persistent():
    rec, patches, _ = run("CR+TopK")
    assert rec.conversations > 1, "scripted run must hit a restart (L=3)"
    post = [p for p in patches if p["conv_id"] >= 1]
    assert post and any(p["memory_classes_all"] for p in post)
    # context is cleared at restart
    first_post = min(post, key=lambda p: p["patch_index"])
    assert first_post["context_patch_indexes"] == []


def test_reset_ablation_clears_memory():
    rec, patches, _ = run("CR+TopK-reset")
    if rec.conversations > 1:
        first_post = min(
            (p for p in patches if p["conv_id"] == 1), key=lambda p: p["patch_index"]
        )
        assert first_post["memory_classes_all"] == []


def test_independent_sampling_has_no_context():
    rec, patches, _ = run("IS")
    assert all(p["context_patch_indexes"] == [] for p in patches)
    assert all(p["memory_classes_all"] == [] for p in patches)
    assert rec.conversations == 1


def test_vs_parses_probabilities():
    rec, patches, _ = run("FaVoR")
    assert any(p.get("probability") is not None for p in patches)


def test_budget_censoring_without_fix():
    # a script that never produces the correct patch: run must stop at B
    pool = [p for p in POOL if "a + b" not in p]

    def no_fix_script(messages, idx):
        code = pool[idx % len(pool)]
        return f"```java\n{code}\n```"

    proto = Protocol(budget_b=4, runs_per_bug=1, k=2, m=3)
    cfg = table5(proto)["CR"]
    rec = run_repair(
        mock_bug(), cfg, proto, FakeChatClient(no_fix_script), MockValidator(),
        run_idx=0,
    )
    assert not rec.plausible_found
    assert rec.validated_count == proto.budget_b
    assert rec.validated_to_first_plausible is None


def test_seed_is_stable():
    rec1, _, _ = run("FaVoR")
    rec2, _, _ = run("FaVoR")
    assert rec1.seed == rec2.seed
