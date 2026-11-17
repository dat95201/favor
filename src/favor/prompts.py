"""Prompt construction.

The initial prompt and feedback follow the released ChatRepair prompts for
single-function bugs (Xia & Zhang 2024); the memory block follows Section 4.4
verbatim; the Verbalized Sampling instruction adapts Zhang et al.'s VS prompt
to repair (Section 4.5). DESIGN.md flags the fidelity items the proposal marks
[verify] — check these against the released ChatRepair code before freezing."""

from __future__ import annotations

from .memory import MemoryEntry

SYSTEM_PROMPT = (
    "You are an automated program repair tool. You are given a buggy Java "
    "function and information about failing tests, and you return a fixed "
    "version of the function."
)


# --- initial prompt (ChatRepair-style, single-function setting) ----------------

def initial_prompt(
    method_src: str,
    trigger_test_id: str,
    failing_line_src: str | None,
    error_message: str,
) -> str:
    parts = [
        "The following code contains a bug:",
        "```java",
        method_src.rstrip(),
        "```",
        f"The code fails on this test: `{trigger_test_id}`",
    ]
    if failing_line_src:
        parts += ["on this test line:", "```java", failing_line_src.strip(), "```"]
    parts += [
        f"with the following test error:\n{error_message.strip()}",
    ]
    return "\n".join(parts)


def request_block(m: int, vs: str, method_name: str, again: bool = False) -> str:
    """What the model must return this turn. vs: 'none' | 'prob' | 'noprob'.
    `again` marks turns after failed attempts (ChatRepair asks for "another"
    fixed version)."""
    if vs == "none" or m == 1:
        ask = "another" if again else "the correct, complete"
        return (
            f"Please provide {ask} fixed version of the `{method_name}` function. "
            "Return the entire fixed function in a single ```java code block, "
            "with no explanation outside the block."
        )
    lines = []
    if again:
        lines.append("None of the previous fixes is correct.")
    lines += [
        f"Instead of one fix, propose {m} different fixes of the buggy "
        f"`{method_name}` function. Sample the fixes from the full range of "
        "plausible fixes rather than only the most likely one; the fixes must "
        "differ in approach, not only in wording.",
        "Format your answer exactly as follows, for each fix i:",
        "",
        "Fix i:",
        "```java",
        "<the complete fixed function>",
        "```",
    ]
    if vs == "prob":
        lines.insert(-3, "")
        lines += [
            f"Probability: <the probability, between 0 and 1, that you would "
            "produce this fix>",
            "",
            "Give every fix its own code block and its own probability line.",
        ]
    else:
        lines += ["", "Give every fix its own code block."]
    return "\n".join(lines)


# --- memory block (Section 4.4, exact format) ----------------------------------

def memory_block(entries: list[MemoryEntry]) -> str:
    if not entries:
        return ""
    lines = [
        "Approaches already tried for this bug, and how they failed.",
        "Do not repeat them; try a different idea.",
    ]
    for i, e in enumerate(entries, start=1):
        times = "time" if e.n == 1 else "times"
        lines += [
            "",
            f"[{i}] Tried {e.n} {times}. Example:",
            e.representative_diff if e.representative_diff else "(no textual change)",
            f"Result: {e.result_line}",
        ]
    return "\n".join(lines)


# --- feedback (Section 4.6) -----------------------------------------------------

def feedback_single(
    outcome_kind: str,
    compile_error: str | None,
    still_fails_trigger: bool,
    new_failure_summary: str | None,
) -> str:
    """ChatRepair's feedback, unchanged, for a turn that returned one patch:
    still failing the original test only states the bug is not fixed; a
    different failing test is named with line and error (Section 2.3)."""
    if outcome_kind == "compile_error":
        return (
            "The fixed version does not compile. Compilation error: "
            f"{compile_error}."
        )
    if outcome_kind == "timeout":
        return "The fixed version causes the test suite to time out."
    if still_fails_trigger:
        return "The fixed version is still not correct; it fails the original failing test."
    return (
        "The fixed version fails a different test: "
        f"{new_failure_summary}."
    )


def feedback_line_multi(index: int, line: str) -> str:
    return f"[Fix {index}] {line}"


def repeat_line(index: int, earlier_index: int) -> str:
    return (
        f"[Fix {index}] is a syntactic repeat of an earlier attempt "
        f"(fix {earlier_index}); it was not run."
    )


def unparseable_line() -> str:
    return (
        "Your previous reply could not be parsed into a complete Java function. "
        "Return the entire function inside one ```java code block."
    )
