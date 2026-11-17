"""L3 (approximated semantic) duplication — pluggable T_gen adapter.

L3 groups L4-equivalent patches by their behavior on a set of generated tests
T_gen (Section 5.6, following xTestCluster and AlphaCode). Generating T_gen
(e.g. with EvoSuite on the buggy method's class) and executing every validated
patch against it is an analysis instrument: its test executions do not count
toward the budget.

This adapter is intentionally a stub: the proposal leaves the test generator
[TBD], and no result in RQ1-RQ4 depends on L3 (only the W_L3 oracle-waste
share does, reported when available). To enable L3:

  1. generate a JUnit suite per bug (EvoSuite 1.2.0 works with Defects4J's
     Java 11 toolchain), store it under data/tgen/<bug>/,
  2. implement `run_tgen(bug, method_code) -> frozenset[str]` returning the
     set of failing generated tests plus output hashes,
  3. post-process runs/<exp>/patches.jsonl, adding an `l3` key to every
     validated patch (equal L4 class AND equal T_gen behavior).

metrics.level_sequence already picks up the `l3` field once present."""

from __future__ import annotations

from .records import BugRecord


def run_tgen(bug: BugRecord, method_code: str) -> frozenset[str]:
    raise NotImplementedError(
        "L3 needs a generated test suite; see semantics.py for the recipe."
    )
