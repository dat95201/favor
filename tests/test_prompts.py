"""Prompt provenance: the VS request follows the VS-Standard template of
Zhang et al. (App. G.3), the no-probability variant their Sequence baseline,
and the memory block Maniple's fenced fact-section convention."""

from favor.prompts import feedback_single, instruction_only_block, request_block


def test_vs_standard_elements():
    p = request_block(3, "prob", "add")
    assert "Generate 3 responses" in p
    assert "estimated probability from 0.0 to 1.0" in p
    assert "(relative to the full " in p
    assert "Randomly sample the responses from the full distribution." in p
    assert "Give ONLY the 3 responses" in p
    assert "no explanations or extra text" in p


def test_sequence_baseline_has_no_probability():
    p = request_block(3, "noprob", "add")
    assert "Generate 3 responses" in p
    assert "Return exactly 3 responses" in p
    assert "robability" not in p


def test_ask_carries_no_diversity_nudge_in_any_arm():
    """The ask is turn-invariant everywhere (ChatRepair Fig. 2; VS-Standard has
    no turn-dependent wording). A "previous ones were wrong, give different
    ones" nudge in shared scaffolding would pre-empt the treatment it belongs
    to — in the baseline AND in the VS arms."""
    assert "the correct, complete fixed version" in request_block(1, "none", "add")
    assert "```java" in request_block(1, "none", "add")
    # VS's "each response is a different fixed version" is within-call
    # diversity — the treatment itself, and present on turn 1. What must not
    # appear anywhere is a BACKWARD reference to earlier attempts, which is
    # the memory arms' mechanism leaking into shared scaffolding.
    for m, vs in ((1, "none"), (3, "prob"), (3, "noprob")):
        ask = request_block(m, vs, "add").lower()
        for nudge in ("another", "previous", "earlier", "again",
                      "already", "do not repeat", "alternative"):
            assert nudge not in ask, f"{m}/{vs} refers back to earlier attempts: {nudge}"


def test_dynamic_feedback_carries_relevant_test_code():
    """A *different* failing test gets test name + relevant test code + error
    (Section 3.2); the original failure gets only the short line."""
    new = feedback_single("fail", None, False, "T::t fails, expected <1> but was <2>",
                          "assertEquals(1, f(x));")
    assert "T::t fails" in new and "assertEquals(1, f(x));" in new
    assert "\n" not in new          # one line per patch, linear in m
    same = feedback_single("fail", None, True, "T::t fails", "assertEquals(1, f(x));")
    assert "still not correct" in same and "assertEquals" not in same


def test_instruction_only_block_has_no_entries():
    b = instruction_only_block()
    assert "Do not repeat" in b
    assert "Tried" not in b and "```" not in b
