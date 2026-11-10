from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..srt_io import SrtDocument

MediaKind = Literal["movie", "episode"]


class FetchError(Exception):
    """Fatal error while identifying, finding, or saving subtitles."""


@dataclass(frozen=True)
class MediaInfo:
    """A movie or episode, normalized from any supported input."""

    title: str
    kind: MediaKind = "movie"
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None
    parent_imdb_id: str | None = None
    parent_tmdb_id: int | None = None
    file_path: Path | None = None
    file_hash: str | None = None
    file_size: int | None = None
    release_name: str | None = None
    duration: float | None = None
    fps: float | None = None

    @property
    def is_episode(self) -> bool:
        return self.kind == "episode"

    def label(self) -> str:
        if self.is_episode and self.season is not None and self.episode is not None:
            return f"{self.title} S{self.season:02d}E{self.episode:02d}"
        if self.year is not None:
            return f"{self.title} ({self.year})"
        return self.title


@dataclass(frozen=True)
class Candidate:
    """A subtitle offered by a source."""

    source: str
    language: str
    candidate_id: str
    release: str = ""
    filename: str = ""
    download_count: int = 0
    rating: float = 0.0
    hearing_impaired: bool = False
    machine_translated: bool = False
    ai_translated: bool = False
    from_trusted: bool = False
    subtitle_format: str = "srt"
    fps: float | None = None
    moviehash_match: bool = False
    season: int | None = None
    episode: int | None = None
    nb_cd: int = 1
    foreign_parts_only: bool = False
    url: str | None = None
    extra: dict[str, object] = field(default_factory=dict, compare=False)

    def describe(self) -> str:
        name = self.release or self.filename or self.candidate_id
        return f"{name} [{self.source} {self.candidate_id}]"


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    score: int
    reasons: tuple[str, ...] = ()
    rejection: str | None = None

    @property
    def accepted(self) -> bool:
        return self.rejection is None


@dataclass(frozen=True)
class DownloadResult:
    data: bytes
    filename: str = ""
    subtitle_format: str = "srt"
    remaining: int | None = None
    reset_time_utc: str | None = None
    requests: int | None = None
    from_cache: bool = False


@dataclass
class ValidatedSubtitle:
    document: SrtDocument
    encoding: str
    warnings: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    last_timestamp: float = 0.0
