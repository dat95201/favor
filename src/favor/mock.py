"""Offline stand-ins for the validator (and bug records) used by unit tests and
the smoke run. No Defects4J, no network."""

from __future__ import annotations

from .failure_class import (
    PASS,
    FailureSignature,
    class_for_compile_error,
    class_for_failures,
)
from .records import BugRecord, TriggerTest
from .validation import Outcome

BUGGY_METHOD = """public int add(int a, int b) {
    return a - b;
}"""

FIXED_METHOD = """public int add(int a, int b) {
    return a + b;
}"""


def mock_bug(key: str = "Mock-1") -> BugRecord:
    project, _, bid = key.partition("-")
    return BugRecord(
        project=project,
        bug_id=int(bid),
        modified_class="mock.Calc",
        file_rel="src/mock/Calc.java",
        method_name="add",
        method_params="(int a, int b)",
        n_params=2,
        start_byte=0,
        end_byte=len(BUGGY_METHOD),
        start_line=0,
        end_line=2,
        method_src=BUGGY_METHOD,
        fixed_method_src=FIXED_METHOD,
        trigger_tests=(
            TriggerTest(
                test_id="mock.CalcTest::testAdd",
                error_message="junit.framework.AssertionFailedError: expected:<3> but was:<-1>",
                failing_line_src="assertEquals(3, calc.add(1, 2));",
                location="CalcTest.java:12",
            ),
        ),
        fixed_test_time_s=1.0,
        buggy_compile_s=1.0,
    )


class MockValidator:
    """Classifies patches by their text:

    - contains "a + b"            -> plausible
    - contains "COMPILE_ERROR"    -> compile error (one class per marker digit)
    - otherwise                   -> fails the trigger test; the failure class is
      derived from the returned constant, so different wrong constants give
      different L4 classes and the same constant repeats a class.
    """

    def __init__(self):
        self.validated: list[str] = []

    def prepare(self) -> None:
        pass

    def cleanup(self) -> None:
        pass

    def validate(self, method_code: str) -> Outcome:
        self.validated.append(method_code)
        if "a + b" in method_code:
            return Outcome(status="plausible", cls=PASS, compile_s=0.2, test_s=1.0)
        if "COMPILE_ERROR" in method_code:
            return Outcome(
                status="compile_error",
                cls=class_for_compile_error("error: cannot find symbol: variable qux"),
                compile_error="cannot find symbol: variable qux",
                compile_s=0.2,
            )
        # derive a deterministic wrong "actual" from the patch text
        marker = "".join(ch for ch in method_code if ch.isdigit()) or str(len(method_code) % 7)
        sig = FailureSignature(
            test_id="mock.CalcTest::testAdd",
            exception_type="junit.framework.AssertionFailedError",
            message_norm="expected <E> but was <A>",
            expected="3",
            actual=marker[:4],
            location="CalcTest.java:12",
        )
        return Outcome(
            status="fail",
            cls=class_for_failures([sig]),
            signatures=[sig],
            compile_s=0.2,
            test_s=1.0,
        )
