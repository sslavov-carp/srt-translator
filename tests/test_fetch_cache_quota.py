from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from srt_translator.fetch.cache import DownloadCache
from srt_translator.fetch.quota import QuotaTracker, default_limit


def test_cache_round_trip(tmp_path: Path) -> None:
    cache = DownloadCache(tmp_path / "downloads")

    assert cache.get("opensubtitles", "42") is None
    path = cache.put("opensubtitles", "42", b"subtitle", filename="movie.srt")

    assert path.read_bytes() == b"subtitle"
    assert cache.get("opensubtitles", "42") == b"subtitle"
    assert cache.info("opensubtitles", "42")["filename"] == "movie.srt"


def test_cache_separates_sources_and_ids(tmp_path: Path) -> None:
    cache = DownloadCache(tmp_path / "downloads")
    cache.put("a", "1", b"one")
    cache.put("b", "1", b"two")

    assert cache.get("a", "1") == b"one"
    assert cache.get("b", "1") == b"two"


def test_disabled_cache_never_stores(tmp_path: Path) -> None:
    cache = DownloadCache(tmp_path / "downloads", enabled=False)
    cache.put("a", "1", b"one")

    assert cache.get("a", "1") is None
    assert not cache.path_for("a", "1").exists()


def test_cache_clear_removes_files(tmp_path: Path) -> None:
    cache = DownloadCache(tmp_path / "downloads")
    cache.put("a", "1", b"one")

    assert cache.clear() >= 2
    assert not (tmp_path / "downloads").exists()


def test_default_limits() -> None:
    assert default_limit(authenticated=True) == 20
    assert default_limit(authenticated=False) == 5


def test_quota_starts_empty(tmp_path: Path) -> None:
    quota = QuotaTracker(tmp_path / "quota.json", limit=20)

    assert quota.used == 0
    assert quota.remaining == 20
    assert quota.can_download()


def test_quota_counts_downloads(tmp_path: Path) -> None:
    quota = QuotaTracker(tmp_path / "quota.json", limit=2)
    quota.record(remaining=1)

    assert quota.used == 1
    assert quota.remaining == 1

    quota.record(remaining=0)

    assert not quota.can_download()


def test_quota_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "quota.json"
    QuotaTracker(path, limit=20).record()
    quota = QuotaTracker(path, limit=20)

    assert quota.used == 1
    assert quota.remaining == 19


def test_quota_resets_on_a_new_day(tmp_path: Path) -> None:
    path = tmp_path / "quota.json"
    yesterday = date.today() - timedelta(days=1)
    path.write_text(
        json.dumps({"date": yesterday.isoformat(), "downloads": 5}),
        encoding="utf-8",
    )

    quota = QuotaTracker(path, limit=20)

    assert quota.used == 0
    assert quota.remaining == 20


def test_server_remaining_is_a_stricter_cap(tmp_path: Path) -> None:
    quota = QuotaTracker(tmp_path / "quota.json", limit=20)
    quota.record(remaining=3)

    assert quota.used == 1
    assert quota.remaining == 3


def test_passed_reset_time_clears_server_remaining(tmp_path: Path) -> None:
    path = tmp_path / "quota.json"
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    path.write_text(
        json.dumps(
            {
                "date": datetime.now(UTC).date().isoformat(),
                "downloads": 4,
                "remaining": 0,
                "reset_time_utc": past,
            }
        ),
        encoding="utf-8",
    )

    quota = QuotaTracker(path, limit=20)

    assert quota.server_remaining is None
    assert quota.used == 0
    assert quota.remaining == 20


def test_corrupt_quota_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "quota.json"
    path.write_text("{not json", encoding="utf-8")

    quota = QuotaTracker(path, limit=5)

    assert quota.remaining == 5


def test_server_allowance_can_raise_the_default_limit(tmp_path: Path) -> None:
    path = tmp_path / "quota.json"
    quota = QuotaTracker(path, limit=5)
    quota.record(remaining=99)

    assert quota.limit == 100
    assert quota.remaining == 99

    reloaded = QuotaTracker(path, limit=5)

    assert reloaded.limit == 100
    assert reloaded.remaining == 99


def test_explicit_limit_is_not_raised_by_the_server(tmp_path: Path) -> None:
    quota = QuotaTracker(tmp_path / "quota.json", limit=5, allow_raise=False)
    quota.record(remaining=99)

    assert quota.limit == 5
    assert quota.remaining == 4
