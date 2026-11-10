from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from srt_translator.fetch import cli
from srt_translator.fetch.identify import TmdbClient
from srt_translator.fetch.models import Candidate, DownloadResult, MediaInfo
from srt_translator.fetch.quota import QuotaTracker

SUBTITLE = (
    "1\n00:00:01,000 --> 00:00:03,000\nЗдравей\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nСвят\n\n"
)


class FakeSource:
    name = "fake"

    def __init__(self, candidates: dict[str, list[Candidate]] | None = None) -> None:
        self.candidates = candidates or {}
        self.searches: list[str] = []
        self.downloads: list[str] = []

    def search(self, media: MediaInfo, language: str) -> list[Candidate]:
        self.searches.append(language)
        return list(self.candidates.get(language, []))

    def download(self, candidate: Candidate) -> DownloadResult:
        self.downloads.append(candidate.candidate_id)
        return DownloadResult(
            data=SUBTITLE.encode("utf-8"),
            filename="Movie.bg.srt",
            remaining=19,
        )


def _candidate(candidate_id: str = "1", **overrides) -> Candidate:
    values = {
        "source": "fake",
        "language": "bg",
        "candidate_id": candidate_id,
        "release": "Inception.2010.1080p.BluRay.x264-GRP",
        "download_count": 1000,
        "rating": 8.0,
        "subtitle_format": "srt",
    }
    values.update(overrides)
    return Candidate(**values)


@pytest.fixture(autouse=True)
def _no_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "probe_duration", lambda path: None)


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "Inception.2010.1080p.BluRay.x264-GRP.mkv"
    path.write_bytes(b"fake video")
    return path


def _run(video: Path, source: FakeSource, work: Path, *extra: str) -> int:
    return cli.main(
        [str(video), "--work-dir", str(work), "--no-hash", *extra],
        sources=[source],
        tmdb=TmdbClient(None),
    )


def test_downloads_preferred_language(
    video: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = FakeSource({"bg": [_candidate()]})
    work = tmp_path / "work"

    code = _run(video, source, work)

    captured = capsys.readouterr()
    assert code == 0
    assert source.searches == ["bg"]
    assert source.downloads == ["1"]
    assert (tmp_path / "Inception.2010.bg.srt").exists()
    assert "Saved:" in captured.out

    log = json.loads((work / "fetch_log.json").read_text(encoding="utf-8"))
    assert log[-1]["outcome"] == "found"
    assert log[-1]["language"] == "bg"


def test_falls_back_to_english(video: Path, tmp_path: Path) -> None:
    source = FakeSource({"en": [_candidate(language="en")]})

    code = _run(video, source, tmp_path / "work")

    assert code == 0
    assert source.searches == ["bg", "en"]
    assert (tmp_path / "Inception.2010.en.srt").exists()


def test_not_found_exits_one(video: Path, tmp_path: Path) -> None:
    source = FakeSource()

    code = _run(video, source, tmp_path / "work")

    assert code == 1
    assert source.searches == ["bg", "en"]


def test_dry_run_does_not_download(
    video: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = FakeSource({"bg": [_candidate()]})

    code = _run(video, source, tmp_path / "work", "--dry-run")

    captured = capsys.readouterr()
    assert code == 0
    assert source.downloads == []
    assert not (tmp_path / "Inception.2010.bg.srt").exists()
    assert "Would download:" in captured.out
    assert "quota:" in captured.out


def test_existing_output_is_skipped(video: Path, tmp_path: Path) -> None:
    existing = tmp_path / "Inception.2010.bg.srt"
    existing.write_text("keep me", encoding="utf-8")
    source = FakeSource({"bg": [_candidate()]})

    code = _run(video, source, tmp_path / "work")

    assert code == 0
    assert source.searches == []
    assert existing.read_text(encoding="utf-8") == "keep me"


def test_overwrite_replaces_existing(video: Path, tmp_path: Path) -> None:
    existing = tmp_path / "Inception.2010.bg.srt"
    existing.write_text("keep me", encoding="utf-8")
    source = FakeSource({"bg": [_candidate()]})

    code = _run(video, source, tmp_path / "work", "--overwrite")

    assert code == 0
    assert existing.read_text(encoding="utf-8").startswith("1\n")


def test_quota_limit_stops_before_download(
    video: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "quota.json").write_text(
        json.dumps(
            {
                "date": datetime.now(UTC).date().isoformat(),
                "downloads": 20,
            }
        ),
        encoding="utf-8",
    )
    source = FakeSource({"bg": [_candidate()]})

    code = _run(video, source, work)

    captured = capsys.readouterr()
    assert code == 1
    assert source.downloads == []
    assert "daily download limit reached" in captured.err


def test_second_run_reuses_cached_download(video: Path, tmp_path: Path) -> None:
    work = tmp_path / "work"
    source = FakeSource({"bg": [_candidate()]})

    assert _run(video, source, work) == 0
    assert source.downloads == ["1"]

    (tmp_path / "Inception.2010.bg.srt").unlink()
    assert _run(video, source, work) == 0

    assert source.downloads == ["1"]
    assert (tmp_path / "Inception.2010.bg.srt").exists()
    quota = QuotaTracker(work / "quota.json", limit=20)
    assert quota.used == 1


def test_json_output(video: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = FakeSource({"bg": [_candidate()]})

    code = _run(video, source, tmp_path / "work", "--json")

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["outcome"]["status"] == "found"
    assert payload["media"]["title"] == "Inception"
    assert payload["outcome"]["output"].endswith("Inception.2010.bg.srt")
    assert payload["quota"]["used"] == 1


def test_best_ranked_candidate_is_downloaded(video: Path, tmp_path: Path) -> None:
    source = FakeSource(
        {
            "bg": [
                _candidate("good", hearing_impaired=False, download_count=10),
                _candidate("better", hearing_impaired=False, download_count=99999),
            ]
        }
    )

    assert _run(video, source, tmp_path / "work") == 0
    assert source.downloads == ["better"]


def test_season_and_episode_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    video = tmp_path / "show.mkv"
    video.write_bytes(b"fake video")
    source = FakeSource({"bg": []})

    code = cli.main(
        [
            str(video),
            "--season",
            "2",
            "--episode",
            "5",
            "--work-dir",
            str(tmp_path / "work"),
            "--no-hash",
            "--dry-run",
        ],
        sources=[source],
        tmdb=TmdbClient(None),
    )

    assert code == 1
    assert "show S02E05" in capsys.readouterr().out
