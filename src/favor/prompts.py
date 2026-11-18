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


def request_block(m: int, vs: str, method_name: str) -> str:
    """What the model must return this turn. vs: 'none' | 'prob' | 'noprob'.

    The ask is turn-invariant in EVERY arm. ChatRepair re-asks for "the
    correct" fix after every feedback (Fig. 2), and VS-Standard has no
    turn-dependent wording either; a "previous ones were wrong, give different
    ones" nudge in a re-ask is a diversity mechanism, and a diversity mechanism
    must live in the arm that is supposed to be testing it (the memory block,
    or the VS template itself) — never in the scaffolding both arms share.
    vs='prob' follows the VS-Standard template of Zhang et al. (App. G.3):
    "Generate k responses ...", a per-response probability described as "the
    estimated probability from 0.0 to 1.0 of this response given the input
    prompt (relative to the full distribution)", the sampling line "Randomly
    sample the responses from the full distribution.", and the closing "Give
    ONLY ... no explanations or extra text." The JSON transport of the paper
    is replaced by fenced ```java blocks because multi-line code inside JSON
    strings breaks small models' escaping (documented in DESIGN.md; the
    paper's F.4 shows VS is robust to the elicitation format).
    vs='noprob' is the paper's Sequence baseline: the same ask without
    probabilities."""
    if vs == "none" or m == 1:
        return (
            f"Please provide the correct, complete fixed version of the "
            f"`{method_name}` function. "
            "Return the entire fixed function in a single ```java code block, "
            "with no explanation outside the block."
        )
    lines = [
        f"Generate {m} responses to the input prompt. Each response is a "
        f"different fixed version of the buggy `{method_name}` function, as one "
        "complete function.",
        "",
        "Return the responses in the following format, one block per response:",
        "",
        "Fix <i>:",
        "```java",
        "<the complete fixed function>",
        "```",
    ]
    if vs == "prob":
        lines += [
            "Probability: <the estimated probability from 0.0 to 1.0 of this "
            "response given the input prompt (relative to the full "
            "distribution)>",
            "",
            "Randomly sample the responses from the full distribution.",
            f"Give ONLY the {m} responses in this format, with no explanations "
            "or extra text.",
        ]
    else:  # Sequence baseline (Zhang et al., Table 1)
        lines += [
            "",
            f"Return exactly {m} responses. Give ONLY the responses in this "
            "format, with no explanations or extra text.",
        ]
    return "\n".join(lines)


# --- memory block (Section 4.4 content, rendered as a Maniple-style fact -------
# section: a markdown heading with fenced code, following Parasaram et al.'s
# prompt convention in which every fact is a `##` section and code-like
# content is fenced; Fig. 1 of that paper) --------------------------------------

def memory_block(entries: list[MemoryEntry]) -> str:
    if not entries:
        return ""
    lines = [
        "## Approaches already tried for this bug, and how they failed",
        "Do not repeat them; try a different idea.",
    ]
    for i, e in enumerate(entries, start=1):
        times = "time" if e.n == 1 else "times"
        lines += [
            "",
            f"[{i}] Tried {e.n} {times}. Example:",
            "```diff",
            e.representative_diff if e.representative_diff else "(no textual change)",
            "```",
            f"Result: {e.result_line}",
        ]
    return "\n".join(lines)


def instruction_only_block() -> str:
    """The CR+Instr control arm: the memory block's instruction without any
    entries, separating the effect of the instruction from its content."""
    return (
        "Do not repeat approaches you have already tried for this bug; "
        "try a different idea."
    )


# --- feedback (Section 4.6) -----------------------------------------------------

def feedback_single(
    outcome_kind: str,
    compile_error: str | None,
    still_fails_trigger: bool,
    new_failure_summary: str | None,
    new_failure_line_src: str | None = None,
) -> str:
    """ChatRepair's dynamic feedback, unchanged, for a turn that returned one
    patch: still failing the original test only states the bug is not fixed; a
    *different* failing test gets the full three-part description — test name,
    relevant test code, error message (Section 3.2). One line, so the m>1
    feedback stays linear in m."""
    if outcome_kind == "compile_error":
        return (
            "The fixed version does not compile. Compilation error: "
            f"{compile_error}."
        )
    if outcome_kind == "timeout":
        return "The fixed version causes the test suite to time out."
    if still_fails_trigger:
        return "The fixed version is still not correct; it fails the original failing test."
    line = f" on this test line: `{new_failure_line_src}`" if new_failure_line_src else ""
    return f"The fixed version fails a different test: {new_failure_summary}{line}."


def feedback_line_multi(index: int, line: str) -> str:
    return f"[Fix {index}] {line}"


def unparseable_line() -> str:
    return (
        "Your previous reply could not be parsed into a complete Java function. "
        "Return the entire function inside one ```java code block."
    )
