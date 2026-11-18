"""The working-copy invariant that checkout reuse depends on.

The runner now hands the same prepared Validator to several tasks instead of
re-running prepare() each time (which re-ran `defects4j export` for a property
of the bug that cannot change). That is only sound if validate() leaves the
working copy byte-identical to the checkout, on every exit path — including
compile errors, timeouts and exceptions out of d4j.
"""

from __future__ import annotations

import pytest

from favor import d4j, validation
from favor.config import ExperimentConfig
from favor.mock import BUGGY_METHOD, mock_bug
from favor.validation import Validator


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    """A Validator over a real file on disk, with Defects4J stubbed out."""
    bug = mock_bug("Mock-1")
    src = tmp_path / bug.file_rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(BUGGY_METHOD.encode())

    monkeypatch.setattr(d4j, "checkout", lambda *a, **kw: True)
    monkeypatch.setattr(d4j, "export", lambda *a, **kw: "src/test")
    monkeypatch.setattr(validation.d4j, "test_line_source", lambda *a, **kw: None)

    proto = ExperimentConfig.load("configs/experiment.yaml").protocol
    v = Validator(bug, proto, tmp_path)
    v.prepare()
    return v, src


def _compile(ok, err=""):
    return d4j.CompileResult(ok=ok, wall_s=0.1, error_text=err)


def _tests(**kw):
    base = dict(ok=True, timed_out=False, n_failing=0, failing_tests_text="", wall_s=0.1)
    return d4j.TestResult(**{**base, **kw})


@pytest.mark.parametrize(
    "name, comp, tr",
    [
        ("plausible", _compile(True), _tests()),
        ("compile_error", _compile(False, "error: cannot find symbol"), None),
        ("timeout", _compile(True), _tests(timed_out=True)),
        ("test_build_broke", _compile(True), _tests(ok=False)),
        (
            "fail",
            _compile(True),
            _tests(n_failing=1, failing_tests_text=(
                "--- mock.CalcTest::testAdd\n"
                "junit.framework.AssertionFailedError: expected:<3> but was:<2>\n"
                "\tat mock.CalcTest.testAdd(CalcTest.java:12)\n"
            )),
        ),
    ],
)
def test_working_copy_is_restored_on_every_outcome(prepared, monkeypatch, name, comp, tr):
    v, src = prepared
    monkeypatch.setattr(validation.d4j, "compile_project", lambda *a, **kw: comp)
    monkeypatch.setattr(validation.d4j, "run_tests", lambda *a, **kw: tr)

    out = v.validate("public int add(int a, int b) {\n    return a * b;\n}")

    assert out.status is not None
    assert src.read_bytes() == BUGGY_METHOD.encode(), (
        f"{name}: working copy left patched; a reused checkout would start dirty"
    )


def test_working_copy_is_restored_when_d4j_raises(prepared, monkeypatch):
    v, src = prepared

    def boom(*a, **kw):
        raise RuntimeError("defects4j crashed")

    monkeypatch.setattr(validation.d4j, "compile_project", boom)

    with pytest.raises(RuntimeError):
        v.validate("public int add(int a, int b) { return 0; }")
    assert src.read_bytes() == BUGGY_METHOD.encode(), (
        "an exception left the working copy patched"
    )


def test_repeated_validates_stay_independent(prepared, monkeypatch):
    """Patch N must be spliced into the original, never onto patch N-1."""
    v, src = prepared
    monkeypatch.setattr(validation.d4j, "compile_project", lambda *a, **kw: _compile(True))
    monkeypatch.setattr(validation.d4j, "run_tests", lambda *a, **kw: _tests())

    # compile_project runs while the candidate is spliced in: capture there.
    seen: list[str] = []
    monkeypatch.setattr(
        validation.d4j, "compile_project",
        lambda *a, **kw: (seen.append(src.read_text()), _compile(True))[1],
    )

    bodies = ("a * b", "a / b", "a + b")
    for body in bodies:
        v.validate(f"public int add(int a, int b) {{\n    return {body};\n}}")

    assert len(seen) == len(bodies)
    for text, body in zip(seen, bodies):
        assert text.count("return") == 1, f"patches stacked: {text!r}"
        assert body in text
