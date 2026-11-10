from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

AUTHENTICATED_LIMIT = 20
ANONYMOUS_LIMIT = 5


def default_limit(authenticated: bool) -> int:
    return AUTHENTICATED_LIMIT if authenticated else ANONYMOUS_LIMIT


def _parse_reset(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00").replace(" UTC", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@dataclass
class QuotaState:
    date: str = ""
    downloads: int = 0
    remaining: int | None = None
    reset_time_utc: str | None = None
    history: list[dict[str, object]] = field(default_factory=list)


class QuotaTracker:
    """Track downloads per day, reconciling with the server's remaining count.

    OpenSubtitles charges a download on POST /download and returns the
    remaining quota there. The local counter protects against wasting a day's
    allowance when the server cannot be asked. The server value wins when it is
    stricter, and can raise the built-in default when the API key has a larger
    allowance than expected.
    """

    def __init__(
        self,
        path: Path,
        *,
        limit: int,
        today: date | None = None,
        allow_raise: bool = True,
    ) -> None:
        self.path = Path(path)
        self.limit = max(1, limit)
        self._allow_raise = allow_raise
        self._today = today or datetime.now(UTC).date()
        self.state = self._load()

    def _load(self) -> QuotaState:
        state = QuotaState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None

        if isinstance(raw, dict):
            downloads = raw.get("downloads")
            remaining = raw.get("remaining")
            history = raw.get("history")
            state = QuotaState(
                date=str(raw.get("date") or ""),
                downloads=downloads if isinstance(downloads, int) else 0,
                remaining=remaining if isinstance(remaining, int) else None,
                reset_time_utc=(
                    raw.get("reset_time_utc")
                    if isinstance(raw.get("reset_time_utc"), str)
                    else None
                ),
                history=history if isinstance(history, list) else [],
            )

        today_text = self._today.isoformat()
        if state.date != today_text:
            state.date = today_text
            state.downloads = 0
            state.remaining = None
            state.reset_time_utc = None
        else:
            reset = _parse_reset(state.reset_time_utc)
            if reset is not None and datetime.now(UTC) >= reset:
                state.downloads = 0
                state.remaining = None
                state.reset_time_utc = None

        if self._allow_raise and state.remaining is not None:
            self.limit = max(self.limit, state.downloads + max(0, state.remaining))

        return state

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self.state)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    @property
    def used(self) -> int:
        return self.state.downloads

    @property
    def server_remaining(self) -> int | None:
        return self.state.remaining

    @property
    def remaining(self) -> int:
        left = max(0, self.limit - self.state.downloads)
        if self.state.remaining is not None:
            left = min(left, max(0, self.state.remaining))
        return left

    def can_download(self) -> bool:
        return self.remaining > 0

    def record(
        self,
        *,
        remaining: int | None = None,
        reset_time_utc: str | None = None,
    ) -> None:
        self.state.downloads += 1
        if remaining is not None:
            self.state.remaining = remaining
            if self._allow_raise:
                self.limit = max(
                    self.limit, self.state.downloads + max(0, remaining)
                )
        if reset_time_utc:
            self.state.reset_time_utc = reset_time_utc
        self._save()

    def describe(self) -> str:
        parts = [f"{self.remaining} download(s) left today (limit {self.limit})"]
        if self.state.remaining is not None:
            parts.append(f"server says {self.state.remaining}")
        if self.state.reset_time_utc:
            parts.append(f"resets {self.state.reset_time_utc}")
        return "; ".join(parts)
