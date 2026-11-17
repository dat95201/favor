"""LLM access through OpenAI-compatible chat endpoints (Ollama, OpenAI, or any
provider exposing /v1/chat/completions), with a disk cache and a full request
log (Section 5.3: every request, response, token count and latency is logged).

The cache key covers model, messages, temperature, seed and max_tokens, so a
re-run with the same seeds replays identically and costs nothing."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import ModelConfig
from .storage import JsonlWriter


@dataclass(frozen=True)
class ChatResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    cached: bool


class ChatClient:
    """Thin client over the `openai` SDK pointed at any compatible base_url."""

    def __init__(
        self,
        model: ModelConfig,
        cache_dir: str | Path | None = None,
        log_path: str | Path | None = None,
    ):
        self.model = model
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._log = JsonlWriter(log_path) if log_path else None
        self._client = None  # lazy: unit tests never touch the network

    def _api(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=self.model.base_url,
                api_key=self.model.api_key,
                timeout=self.model.request_timeout_s,
                max_retries=0,  # we do our own retries, with logging
            )
        return self._client

    def _cache_key(self, messages, temperature, seed, max_tokens) -> str:
        blob = json.dumps(
            {
                "model": self.model.name,
                "messages": messages,
                "temperature": temperature,
                "seed": seed,
                "max_tokens": max_tokens,
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def chat(
        self,
        messages: list[dict],
        temperature: float,
        seed: int | None,
        max_tokens: int,
        tag: str = "",
    ) -> ChatResult:
        key = self._cache_key(messages, temperature, seed, max_tokens)
        cache_file = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cache_file and cache_file.exists():
            d = json.loads(cache_file.read_text())
            result = ChatResult(**{**d, "cached": True})
            self._log_call(tag, messages, result, key)
            return result

        last_err: Exception | None = None
        for attempt in range(self.model.max_retries):
            try:
                t0 = time.monotonic()
                kwargs = dict(
                    model=self.model.name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **self.model.extra,
                )
                if seed is not None and self.model.supports_seed:
                    kwargs["seed"] = seed
                resp = self._api().chat.completions.create(**kwargs)
                latency = time.monotonic() - t0
                usage = getattr(resp, "usage", None)
                result = ChatResult(
                    text=resp.choices[0].message.content or "",
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    latency_s=latency,
                    cached=False,
                )
                if cache_file:
                    payload = result.__dict__.copy()
                    payload.pop("cached")
                    cache_file.write_text(json.dumps(payload))
                self._log_call(tag, messages, result, key)
                return result
            except Exception as e:  # noqa: BLE001 — retry any transport/API error
                last_err = e
                wait = min(60, 2**attempt)
                time.sleep(wait)
        raise RuntimeError(
            f"LLM call failed after {self.model.max_retries} retries: {last_err}"
        ) from last_err

    def _log_call(self, tag, messages, result: ChatResult, key: str) -> None:
        if not self._log:
            return
        self._log.write(
            {
                "ts": time.time(),
                "tag": tag,
                "model": self.model.name,
                "cache_key": key,
                "cached": result.cached,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "latency_s": round(result.latency_s, 3),
                "messages": messages,
                "response": result.text,
            }
        )


class FakeChatClient:
    """Offline stand-in for tests and the smoke run: `script` maps call index
    to a response string, or is a callable(messages, call_index) -> str."""

    def __init__(self, script):
        self.script = script
        self.calls: list[list[dict]] = []

    def chat(self, messages, temperature, seed, max_tokens, tag="") -> ChatResult:
        idx = len(self.calls)
        self.calls.append(messages)
        if callable(self.script):
            text = self.script(messages, idx)
        else:
            text = self.script[min(idx, len(self.script) - 1)]
        return ChatResult(
            text=text,
            prompt_tokens=sum(len(m["content"].split()) for m in messages),
            completion_tokens=len(text.split()),
            latency_s=0.0,
            cached=False,
        )
