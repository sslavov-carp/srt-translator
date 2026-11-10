from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

MAX_ENTRIES = 1000


def read_entries(path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return [item for item in payload["entries"] if isinstance(item, dict)]
    return []


def append_entry(path: Path, entry: dict[str, object]) -> dict[str, object]:
    path = Path(path)
    record = {"timestamp": datetime.now(UTC).isoformat(), **entry}
    entries = read_entries(path)
    entries.append(record)
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return record
