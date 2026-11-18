"""Prompt provenance: the VS request follows the VS-Standard template of
Zhang et al. (App. G.3), the no-probability variant their Sequence baseline,
and the memory block Maniple's fenced fact-section convention."""

from favor.prompts import instruction_only_block, request_block


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


def test_single_patch_stays_chatrepair_style():
    first = request_block(1, "none", "add")
    again = request_block(1, "none", "add", again=True)
    assert "the correct, complete fixed version" in first
    assert "another fixed version" in again
    assert "```java" in first


def test_instruction_only_block_has_no_entries():
    b = instruction_only_block()
    assert "Do not repeat" in b
    assert "Tried" not in b and "```" not in b
