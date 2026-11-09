from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

CACHE_VERSION = 1


def make_cache_key(
    *,
    model: str,
    prompt_version: str,
    glossary: dict[str, str],
    source: str,
    context_before: list[str],
    context_after: list[str],
) -> str:
    payload = {
        "target": "bg",
        "model": model,
        "prompt_version": prompt_version,
        "glossary": glossary,
        "source": source,
        "context_before": context_before,
        "context_after": context_after,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TranslationCache:
    def __init__(
        self,
        path: Path | None,
        *,
        enabled: bool = True,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.enabled = enabled and self.path is not None
        self._log = log or (lambda _message: None)
        self.entries: dict[str, str] = {}
        if self.enabled:
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != CACHE_VERSION:
                self._log(
                    f"Cache version mismatch in {self.path}; starting with an empty cache."
                )
                return
            entries = data.get("entries")
            if isinstance(entries, dict):
                self.entries = {
                    str(key): str(value)
                    for key, value in entries.items()
                    if isinstance(key, str) and isinstance(value, str)
                }
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            self._log(f"Could not read cache {self.path}: {exc}. Starting empty.")
            self.entries = {}

    def get(self, key: str) -> str | None:
        if not self.enabled:
            return None
        return self.entries.get(key)

    def put(self, key: str, value: str) -> None:
        if not self.enabled:
            return
        self.entries[key] = value

    def delete(self, key: str) -> None:
        if self.enabled:
            self.entries.pop(key, None)

    def save(self) -> None:
        if not self.enabled or self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": CACHE_VERSION, "entries": self.entries},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
