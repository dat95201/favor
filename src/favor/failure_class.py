"""Failure signatures and L4 failure classes (Section 5.6).

A failure signature is (exception type, normalized message, expected value,
actual value, assertion location). The L4 class of a validated patch is its
compilation status plus the set of failing tests with their signatures.
Non-compiling patches form classes by normalized compiler error; timeouts form
one class. A plausible patch has the `pass` class (it never enters memory)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict


@dataclass(frozen=True)
class FailureSignature:
    test_id: str                 # package.Class::method
    exception_type: str
    message_norm: str
    expected: str | None = None
    actual: str | None = None
    location: str | None = None  # "File.java:123", first frame in the test class
    # the asserting source line, resolved from `location` at validation time.
    # ChatRepair's dynamic feedback shows it with the test name and the error;
    # it is NOT part of key(), so it never splits an L4 class. Keep it LAST:
    # memory.result_line_for rebuilds signatures positionally from key().
    failing_line_src: str | None = None

    def key(self) -> tuple:
        return (
            self.test_id,
            self.exception_type,
            self.message_norm,
            self.expected,
            self.actual,
            self.location,
        )

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "FailureSignature":
        return FailureSignature(**d)

    def summary(self) -> str:
        """One line for feedback and memory rendering."""
        loc = f" at {self.location}" if self.location else ""
        if self.expected is not None and self.actual is not None:
            return (
                f"{self.test_id} fails{loc}, "
                f"expected <{self.expected}> but was <{self.actual}>"
            )
        msg = f": {self.message_norm}" if self.message_norm else ""
        return f"{self.test_id} fails{loc} with {self.exception_type}{msg}"


@dataclass(frozen=True)
class FailureClass:
    kind: str  # "compile_error" | "timeout" | "tests" | "pass" | "none"
    detail: tuple = field(default_factory=tuple)

    def key(self) -> tuple:
        return (self.kind, self.detail)

    def to_json(self) -> dict:
        return {"kind": self.kind, "detail": _jsonable(self.detail)}

    def failing_test_ids(self) -> set[str]:
        if self.kind != "tests":
            return set()
        return {sig[0] for sig in self.detail}


def _jsonable(x):
    if isinstance(x, tuple):
        return [_jsonable(i) for i in x]
    return x


PASS = FailureClass(kind="pass")
TIMEOUT = FailureClass(kind="timeout")


def class_hash(cls: "FailureClass") -> str:
    import hashlib
    import json

    blob = json.dumps({"kind": cls.kind, "detail": _jsonable(cls.detail)}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def class_for_compile_error(stderr: str) -> FailureClass:
    return FailureClass(kind="compile_error", detail=(normalize_compiler_error(stderr),))


def class_for_failures(sigs: list[FailureSignature]) -> FailureClass:
    detail = tuple(sorted(s.key() for s in sigs))
    return FailureClass(kind="tests", detail=detail)


# --- normalization -------------------------------------------------------------

_HEX_ID = re.compile(r"@[0-9a-fA-F]{4,}")
_PATH = re.compile(r"(/[\w.\-]+)+")
_WS = re.compile(r"\s+")
_EXPECTED_ACTUAL = [
    re.compile(r"expected:\s*<(?P<e>.*?)>\s*but was:\s*<(?P<a>.*?)>", re.S),
    re.compile(r"expected\s*\[(?P<e>.*?)\]\s*but\s*(?:found|was)\s*\[(?P<a>.*?)\]", re.S),
    re.compile(r"Expected\s*:\s*(?P<e>.*?)\s*Actual\s*:\s*(?P<a>.*?)$", re.S),
]


def normalize_message(msg: str) -> str:
    msg = _HEX_ID.sub("@ID", msg)
    msg = _PATH.sub("<PATH>", msg)
    msg = _WS.sub(" ", msg).strip()
    return msg[:300]


def extract_expected_actual(msg: str) -> tuple[str | None, str | None]:
    """Expected/actual values enter the L4 key, so they get the same scrubbing
    as messages: object ids and absolute paths must not split classes."""
    for pat in _EXPECTED_ACTUAL:
        m = pat.search(msg)
        if m:
            e = _WS.sub(" ", _PATH.sub("<PATH>", _HEX_ID.sub("@ID", m.group("e")))).strip()[:120]
            a = _WS.sub(" ", _PATH.sub("<PATH>", _HEX_ID.sub("@ID", m.group("a")))).strip()[:120]
            return e, a
    return None, None


_JAVAC_ERROR = re.compile(r"error:\s*(?P<msg>.+)")
_SYMBOL_LINE = re.compile(r"symbol:\s*(?P<sym>.+)")


def normalize_compiler_error(stderr: str) -> str:
    """First javac error, with paths and line numbers stripped. 'cannot find
    symbol' keeps the symbol line, which carries the actual information."""
    lines = stderr.splitlines()
    for i, line in enumerate(lines):
        m = _JAVAC_ERROR.search(line)
        if not m:
            continue
        msg = m.group("msg").strip()
        if "cannot find symbol" in msg:
            for extra in lines[i + 1 : i + 4]:
                s = _SYMBOL_LINE.search(extra)
                if s:
                    msg = f"cannot find symbol: {s.group('sym').strip()}"
                    break
        msg = _PATH.sub("<PATH>", msg)
        msg = re.sub(r"\d+", "<N>", msg)
        return _WS.sub(" ", msg).strip()[:300]
    return "compilation failed"


# --- parsing Defects4J `failing_tests` ----------------------------------------

_TEST_HEADER = re.compile(r"^---\s+(?P<id>\S+)")
_FIRST_LINE = re.compile(r"^(?P<exc>[\w.$]+(?:Exception|Error|Failure|Throwable)?)(?::\s*(?P<msg>.*))?$")
_FRAME = re.compile(r"^\s+at\s+(?P<cls>[\w.$]+)\.(?P<meth>[\w$<>]+)\((?P<file>[^:)]+):?(?P<line>\d+)?\)")


def parse_failing_tests(
    text: str, stack_frames: int = 1
) -> list[FailureSignature]:
    """Parse the `failing_tests` file a Defects4J test run writes. Each block:

        --- package.Class::method
        junit.framework.AssertionFailedError: expected:<3> but was:<2>
            at junit.framework.Assert.fail(Assert.java:47)
            at package.ClassTest.testFoo(ClassTest.java:42)
    """
    sigs: list[FailureSignature] = []
    blocks: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in text.splitlines():
        h = _TEST_HEADER.match(line)
        if h:
            if current:
                blocks.append(current)
            current = (h.group("id"), [])
        elif current is not None:
            current[1].append(line)
    if current:
        blocks.append(current)

    for test_id, body in blocks:
        exc, raw_msg = "UnknownError", ""
        for line in body:
            line = line.strip()
            if not line or line.startswith("at "):
                continue
            if ":" in line:
                head, _, tail = line.partition(":")
                exc, raw_msg = head.strip(), tail.strip()
            else:
                exc, raw_msg = line, ""
            break
        # multi-line assertion messages: keep appending until the first frame
        extra: list[str] = []
        seen_first = False
        for line in body:
            if _FRAME.match(line):
                break
            s = line.strip()
            if not s:
                continue
            if not seen_first:
                seen_first = True
                continue
            extra.append(s)
        if extra:
            raw_msg = (raw_msg + " " + " ".join(extra)).strip()

        test_class = test_id.split("::")[0].split(".")[-1]
        location = None
        frames_kept = 0
        for line in body:
            fm = _FRAME.match(line)
            if not fm:
                continue
            if fm.group("file").startswith(test_class) and fm.group("line"):
                location = f"{fm.group('file')}:{fm.group('line')}"
                frames_kept += 1
                if frames_kept >= stack_frames:
                    break

        expected, actual = extract_expected_actual(raw_msg)
        msg_norm = normalize_message(raw_msg)
        if expected is not None:
            # keep the signature stable when only formatting around the values moved
            msg_norm = normalize_message(
                re.sub(r"expected.*", "expected <E> but was <A>", raw_msg, flags=re.S)
            )
        sigs.append(
            FailureSignature(
                test_id=test_id,
                exception_type=exc,
                message_norm=msg_norm,
                expected=expected,
                actual=actual,
                location=location,
            )
        )
    return sigs
