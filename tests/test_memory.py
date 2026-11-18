import random

from favor.failure_class import FailureClass, FailureSignature, class_for_failures
from favor.memory import FailureMemory
from favor.prompts import memory_block

BUGGY = "public int f() {\n    return 1;\n}"


def cls(tag: str) -> FailureClass:
    sig = FailureSignature(
        test_id=f"T::{tag}", exception_type="AssertionFailedError",
        message_norm="expected <E> but was <A>", expected="1", actual=tag,
        location="T.java:1",
    )
    return class_for_failures([sig])


def patch(tag: str) -> str:
    return f"public int f() {{\n    return {hash(tag) % 97};\n}}"


def test_observe_and_inherit_count():
    mem = FailureMemory()
    mem.observe(cls("a"), BUGGY, patch("a"), turn=1)
    mem.observe(cls("a"), BUGGY, patch("a2"), turn=2)
    mem.inherit(cls("a"), turn=3)
    e = list(mem.entries.values())[0]
    assert e.n == 3
    assert e.t_first == 1 and e.t_last == 3


def test_plausible_never_enters():
    from favor.failure_class import PASS

    mem = FailureMemory()
    mem.observe(PASS, BUGGY, patch("x"), turn=1)
    assert len(mem) == 0


def test_topk_prefers_frequent_recent_absent():
    mem = FailureMemory()
    # class A: frequent and recent; class B: old singleton; class C: in-context singleton
    for t in (1, 5, 9):
        mem.observe(cls("A"), BUGGY, patch(f"A{t}"), turn=t)
    mem.observe(cls("B"), BUGGY, patch("B"), turn=2)
    mem.observe(cls("C"), BUGGY, patch("C"), turn=8)
    in_conv = {cls("C").key()}
    top = mem.select("topk", k=2, turn=10, conversation_classes=in_conv)
    picked = [e.cls for e in top]
    assert cls("A") in picked          # frequent + recent + absent
    assert cls("C") not in picked[:1]  # present in conversation: absent term is low


def test_all_returns_everything_ordered():
    mem = FailureMemory()
    for tag, t in (("A", 1), ("B", 2), ("C", 3)):
        mem.observe(cls(tag), BUGGY, patch(tag), turn=t)
    out = mem.select("all", k=1, turn=4, conversation_classes=set())
    assert len(out) == 3


def test_random_k_is_seeded():
    mem = FailureMemory()
    for tag, t in (("A", 1), ("B", 2), ("C", 3), ("D", 4)):
        mem.observe(cls(tag), BUGGY, patch(tag), turn=t)
    r1 = mem.select("random", 2, 5, set(), rng=random.Random(7))
    r2 = mem.select("random", 2, 5, set(), rng=random.Random(7))
    assert [e.cls for e in r1] == [e.cls for e in r2]


def test_small_memory_shows_all():
    mem = FailureMemory()
    mem.observe(cls("A"), BUGGY, patch("A"), turn=1)
    assert len(mem.select("topk", k=3, turn=2, conversation_classes=set())) == 1


def test_render_format():
    mem = FailureMemory()
    for t in (1, 2, 3, 4):
        mem.observe(cls("A"), BUGGY, patch(f"A{t}"), turn=t)
    block = memory_block(mem.select("topk", 1, 5, set()))
    # Maniple-style fact section: markdown heading + fenced diff
    assert block.startswith("## Approaches already tried for this bug")
    assert "Do not repeat them; try a different idea." in block
    assert "[1] Tried 4 times. Example:" in block
    assert "```diff" in block
    assert "Result: T::A fails at T.java:1, expected <1> but was <A>" in block


def test_clear_for_reset_ablation():
    mem = FailureMemory()
    mem.observe(cls("A"), BUGGY, patch("A"), turn=1)
    mem.clear()
    assert len(mem) == 0
