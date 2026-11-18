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


def run(config_name: str, proto: Protocol = None, fake_script=script):
    proto = proto or Protocol(budget_b=8, runs_per_bug=1, k=2, m=3)
    cfg = table5(proto)[config_name]
    log = Collector()
    llm = FakeChatClient(fake_script)
    rec = run_repair(
        mock_bug(), cfg, proto, llm, MockValidator(),
        run_idx=0, patch_log=log,
    )
    return rec, log.rows, proto, llm


def test_cr_finds_fix_and_respects_budget():
    rec, patches, proto, _ = run("CR")
    assert rec.plausible_found
    assert rec.stopped_reason == "plausible"
    assert rec.validated_count <= proto.budget_b
    assert rec.validated_to_first_plausible == rec.validated_count


def test_l2_repeats_skip_validation_but_are_logged():
    rec, patches, _, _ = run("CR")
    repeats = [p for p in patches if p["status"] == "repeat"]
    assert repeats, "A2/B2 variants must be filtered as syntactic repeats"
    assert all(not p["validated"] for p in repeats)
    assert all(p["l2_duplicate_of"] is not None for p in repeats)
    # the repeat inherits the L4 class of the patch it duplicates
    for p in repeats:
        orig = next(q for q in patches if q["patch_index"] == p["l2_duplicate_of"])
        assert p["class_hash"] == orig["class_hash"]


def test_run_stops_at_first_plausible():
    rec, patches, _, _ = run("FaVoR")
    assert rec.plausible_found
    after = [p for p in patches if p["patch_index"] > rec.first_plausible_patch_index]
    assert all(not p["validated"] for p in after)


def test_restart_keeps_memory_when_persistent():
    rec, patches, _, _ = run("CR+TopK")
    assert rec.conversations > 1, "scripted run must hit a restart (L=3 attempts)"
    post = [p for p in patches if p["conv_id"] >= 1]
    assert post and any(p["memory_classes_all"] for p in post)
    # context is cleared at restart (first patch of the new conversation)
    first_post = min(post, key=lambda p: p["patch_index"])
    assert first_post["context_patch_indexes"] == []


def test_reset_ablation_clears_memory():
    rec, patches, _, _ = run("CR+TopK-reset")
    if rec.conversations > 1:
        first_post = min(
            (p for p in patches if p["conv_id"] == 1), key=lambda p: p["patch_index"]
        )
        assert first_post["memory_classes_all"] == []


def test_independent_sampling_has_no_context():
    rec, patches, _, _ = run("IS")
    assert all(p["context_patch_indexes"] == [] for p in patches)
    assert all(p["memory_classes_all"] == [] for p in patches)
    assert rec.conversations == 1


def test_vs_parses_probabilities():
    rec, patches, _, _ = run("FaVoR")
    assert any(p.get("probability") is not None for p in patches)


def test_l_counts_attempts_not_calls():
    """With m=3 and L=3, every VS call fills a conversation: the loop must
    restart per call, not after 3 calls (9 attempts)."""
    rec, patches, _, _ = run("FaVoR")
    assert rec.conversations == rec.turns
    rec1, patches1, _, _ = run("CR")
    # m=1: one attempt per call -> restart every 3 calls, as before
    assert rec1.conversations < rec1.turns


def test_same_call_patches_are_in_context():
    """A repeat of a patch generated earlier in the SAME call saw that patch
    in its own output: it is in-context, not lost at a restart (5.7)."""
    rec, patches, _, _ = run("FaVoR")
    same_call_repeats = [
        p for p in patches
        if p["status"] == "repeat"
        and any(
            q["patch_index"] == p["l2_duplicate_of"] and q["turn"] == p["turn"]
            for q in patches
        )
    ]
    for p in same_call_repeats:
        assert p["l2_duplicate_of"] in p["context_patch_indexes"]


def test_memory_block_never_accumulates():
    """The model sees at most one memory block per request (Section 4.4)."""
    _, _, _, llm = run("CR+TopK")
    for call in llm.calls:
        text = "\n".join(m["content"] for m in call)
        assert text.count("## Approaches already tried") <= 1


def test_feedback_named_tests_checked_against_text():
    """ChatRepair's still-fails-the-original-test feedback names no test id,
    so nothing is recorded as named (origin analysis, 5.7)."""
    _, patches, _, _ = run("CR")
    still_failing = [p for p in patches if p["status"] == "fail"]
    assert still_failing
    for p in still_failing:
        for t in p["feedback_named_tests"]:
            assert t in (p["feedback_line"] or "")


def test_instruction_only_arm():
    rec, patches, _, llm = run("CR+Instr")
    assert rec.plausible_found
    joined = "\n".join(m["content"] for call in llm.calls for m in call)
    assert "Do not repeat approaches you have already tried" in joined
    assert "## Approaches already tried" not in joined  # no entries, ever
    assert all(p["memory_classes_all"] == [] for p in patches)


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
    assert rec.stopped_reason == "budget"
    assert rec.validated_count == proto.budget_b
    assert rec.validated_to_first_plausible is None


def test_call_cap_terminates_unparseable_loops():
    """Unparseable output consumes no budget; without the cap the loop would
    never terminate."""
    proto = Protocol(budget_b=4, runs_per_bug=1, max_llm_calls=7)
    cfg = table5(proto)["CR"]
    rec = run_repair(
        mock_bug(), cfg, proto, FakeChatClient(lambda m, i: "I cannot help."),
        MockValidator(), run_idx=0,
    )
    assert rec.stopped_reason == "call_cap"
    assert rec.turns == 7
    assert rec.validated_count == 0


def test_seed_is_stable_and_shared_across_arms():
    rec1, _, _, _ = run("FaVoR")
    rec2, _, _, _ = run("FaVoR")
    assert rec1.seed == rec2.seed
    rec3, _, _, _ = run("CR")
    # common random numbers: the same (bug, run) draws the same seed in every arm
    assert rec3.seed == rec1.seed
