"""JSONL storage for run artifacts."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterator


class JsonlWriter:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf8")
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            self._fh.write(line)
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_jsonl(path: str | Path) -> Iterator[dict]:
    p = Path(path)
    if not p.exists():
        return
    with open(p, encoding="utf8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_json(path: str | Path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def read_json(path: str | Path):
    return json.loads(Path(path).read_text())
