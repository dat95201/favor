"""Defects4J adapter: metadata (no checkout), working copies, compile and test.
Local execution only — Defects4J 3.0.1 installed by scripts/setup.sh; the
environment (Java 11, PERL5LIB, TZ) comes from favor.paths.tool_env()."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths as P


# ---------------------------------------------------------------- processes

def run(cmd, cwd=None, timeout=None, env=None):
    """Run a command; return (returncode, merged output, wall_s). The command
    gets its own process group and the WHOLE group is killed on timeout: Ant
    spawns test JVMs, and killing only the direct child would leave orphan
    JVMs running (and skew every later timing)."""
    t0 = time.perf_counter()
    try:
        p = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env or P.tool_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except FileNotFoundError as e:
        return 127, str(e), time.perf_counter() - t0
    try:
        out, _ = p.communicate(timeout=timeout)
        code = p.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            p.kill()
        out, _ = p.communicate()
        code = -9
    return code, out or "", time.perf_counter() - t0


def d4j(args, cwd=None, timeout=1800):
    return run([str(P.D4J_BIN)] + list(args), cwd=cwd, timeout=timeout)


_STATUS: dict[str, str] = {}


def status() -> str:
    """'ok' when the Defects4J command line runs here, else why it does not."""
    if "d4j" not in _STATUS:
        if not P.D4J_BIN.exists():
            _STATUS["d4j"] = f"not found at {P.D4J_BIN} (run scripts/setup.sh)"
        elif shutil.which("perl") is None:
            _STATUS["d4j"] = "perl is not installed"
        else:  # `pids` loads the Perl modules every command needs
            code, out, _ = d4j(["pids"], timeout=120)
            last = (out.strip().splitlines() or ["no output"])[-1][:200]
            _STATUS["d4j"] = "ok" if code == 0 else f"does not run ({last})"
    return _STATUS["d4j"]


def available() -> bool:
    return status() == "ok"


# ---------------------------------------------------------------- metadata (no checkout)

def projects() -> list[str]:
    return sorted(
        p.name for p in P.D4J_PROJECTS.iterdir() if (p / "active-bugs.csv").exists()
    )


def active_bugs(pid: str) -> list[int]:
    lines = (P.D4J_PROJECTS / pid / "active-bugs.csv").read_text().splitlines()[1:]
    return [int(l.split(",")[0]) for l in lines if l.strip()]


def _meta_lines(pid, bid, folder, suffix="") -> list[str]:
    p = P.D4J_PROJECTS / pid / folder / f"{bid}{suffix}"
    if not p.exists():
        return []
    return [l.strip() for l in p.read_text(errors="replace").splitlines() if l.strip()]


def modified_classes(pid, bid) -> list[str]:
    return _meta_lines(pid, bid, "modified_classes", ".src")


def patch_file_count(pid, bid) -> int:
    p = P.D4J_PROJECTS / pid / "patches" / f"{bid}.src.patch"
    return p.read_text(errors="replace").count("diff --git") if p.exists() else 0


def trigger_tests_meta(pid, bid) -> list[tuple[str, str]]:
    """[(test 'Class::method', first error line)] from the trigger_tests file."""
    p = P.D4J_PROJECTS / pid / "trigger_tests" / str(bid)
    out: list[list[str]] = []
    cur = None
    for line in p.read_text(errors="replace").splitlines() if p.exists() else []:
        if line.startswith("--- "):
            cur = line[4:].strip()
            out.append([cur, ""])
        elif cur and not out[-1][1] and line.strip():
            out[-1][1] = line.strip()
    return [tuple(x) for x in out]


# ---------------------------------------------------------------- working copies

def checkout(pid: str, bid: int, version: str, workdir: Path) -> bool:
    """version: 'b' (buggy) or 'f' (fixed)."""
    if (workdir / ".defects4j.config").exists():
        return True
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    code, out, _ = d4j(["checkout", "-p", pid, "-v", f"{bid}{version}", "-w", str(workdir)])
    if code != 0:
        print(out[-2000:])
    return code == 0


def export(workdir: Path, prop: str) -> str:
    code, out, _ = d4j(["export", "-p", prop, "-w", str(workdir)])
    lines = [
        l
        for l in out.splitlines()
        if not re.match(r"^(Running|Determine|Check|Init|  - |\s*$)", l) and "..." not in l
    ]
    return "\n".join(lines).strip() if code == 0 else ""


def test_line_source(
    workdir: Path, test_dir: str, test_id: str, location: str | None
) -> str | None:
    """The source line of a failing assertion, from a signature's location
    ("ClassTest.java:42"). ChatRepair's initial prompt AND its dynamic feedback
    include this "relevant test code" next to the test name and the error."""
    if not (location and test_dir):
        return None
    try:
        line_no = int(location.rsplit(":", 1)[1])
        path = Path(workdir) / test_dir / (
            test_id.split("::")[0].replace(".", "/") + ".java"
        )
        lines = path.read_text(errors="replace").splitlines()
        if 0 < line_no <= len(lines):
            return lines[line_no - 1].strip()
    except (OSError, ValueError, IndexError):
        return None
    return None


@dataclass(frozen=True)
class CompileResult:
    ok: bool
    wall_s: float
    error_text: str


def compile_project(workdir: Path, timeout: float = 1800) -> CompileResult:
    code, out, wall = d4j(["compile", "-w", str(workdir)], timeout=timeout)
    if code == 0:
        return CompileResult(ok=True, wall_s=wall, error_text="")
    return CompileResult(ok=False, wall_s=wall, error_text=_compile_error(out))


def _compile_error(out: str) -> str:
    lines = out.splitlines()
    for i, l in enumerate(lines):
        if "error:" in l:
            block = [re.sub(r"^\s*\[javac\]\s?", "", x) for x in lines[i : i + 6]]
            block[0] = re.sub(r"^.*/([^/]+\.java):(\d+):", r"\1:\2:", block[0])
            return "\n".join(x.rstrip() for x in block if x.strip())
    return "\n".join(lines[-6:])


@dataclass(frozen=True)
class TestResult:
    ok: bool                  # ran to completion (regardless of failures)
    timed_out: bool
    n_failing: int
    failing_tests_text: str   # content of the failing_tests file (stack traces)
    wall_s: float


def run_tests(workdir: Path, relevant_only: bool = True, timeout: float = 1800) -> TestResult:
    """`defects4j test [-r]`: compiles tests and runs them; failing tests and
    their stack traces land in <workdir>/failing_tests."""
    failing_file = Path(workdir) / "failing_tests"
    if failing_file.exists():
        failing_file.unlink()
    args = ["test"]
    if relevant_only:
        args.append("-r")
    args += ["-w", str(workdir)]
    code, out, wall = d4j(args, timeout=timeout)
    if code == -9:
        return TestResult(ok=False, timed_out=True, n_failing=-1, failing_tests_text="", wall_s=wall)
    n = -1
    m = re.search(r"Failing tests:\s*(\d+)", out)
    if m:
        n = int(m.group(1))
    text = failing_file.read_text(errors="replace") if failing_file.exists() else ""
    if n < 0:
        # the run broke before the summary line (e.g. test build failure)
        return TestResult(ok=False, timed_out=False, n_failing=-1, failing_tests_text=text, wall_s=wall)
    return TestResult(ok=True, timed_out=False, n_failing=n, failing_tests_text=text, wall_s=wall)


def cleanup(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
