"""Project paths and the environment needed to call Defects4J and Java.
Follows the local (no container) setup of scripts/setup.sh: Defects4J 3.0.1
lives in benchmarks/defects4j and Java 11 is auto-detected."""

from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def load_dotenv() -> None:
    """Read KEY=VALUE lines from .env without overriding the real environment."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv()  # .env must be in effect before the paths below are computed

D4J_HOME = Path(
    os.getenv("D4J_HOME") or os.getenv("DEFECTS4J_HOME") or ROOT / "benchmarks" / "defects4j"
)
D4J_BIN = D4J_HOME / "framework" / "bin" / "defects4j"
D4J_PROJECTS = D4J_HOME / "framework" / "projects"

DATA = ROOT / "data"        # bug selection, splits, per-bug facts
CACHE = ROOT / "cache"      # LLM responses, shared by all configurations
RUNS = ROOT / "runs"        # experiment logs (one directory per experiment)
WORK = ROOT / "work"        # Defects4J checkouts (scratch; rebuilt on demand)
ANALYSIS = ROOT / "analysis"

# Defects4J 3.x is validated on Java 11; a JDK is required.
_JAVA11_CANDIDATES = [
    os.getenv("JAVA_HOME", ""),
    "/opt/homebrew/opt/openjdk@11/libexec/openjdk.jdk/Contents/Home",
    "/usr/local/opt/openjdk@11/libexec/openjdk.jdk/Contents/Home",
    *sorted(glob.glob("/usr/lib/jvm/java-11-openjdk*")),
]


def java_home() -> str:
    for c in _JAVA11_CANDIDATES:
        if c and (Path(c) / "bin" / "java").exists():
            return c
    return ""


def tool_env() -> dict:
    """Environment for Defects4J and Ant."""
    env = dict(os.environ)
    jh = java_home()
    if jh:
        env["JAVA_HOME"] = jh
        env["PATH"] = f"{jh}/bin:{env.get('PATH', '')}"
    env["PATH"] = f"{D4J_BIN.parent}:{env['PATH']}"
    env["TZ"] = "America/Los_Angeles"      # Defects4J runs its tests in this zone
    env.pop("JAVA_TOOL_OPTIONS", None)     # would change every JVM we time
    perl5 = Path.home() / "perl5" / "lib" / "perl5"  # cpanm --local-lib default
    if perl5.exists() and str(perl5) not in env.get("PERL5LIB", ""):
        env["PERL5LIB"] = f"{perl5}:{env['PERL5LIB']}" if env.get("PERL5LIB") else str(perl5)
    return env


def java_bin(name: str = "java") -> str:
    jh = java_home()
    return str(Path(jh) / "bin" / name) if jh else (shutil.which(name) or name)
