from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Candidate, DownloadResult, MediaInfo


class SourceError(Exception):
    """A source could not search or download; the next source may still work."""


class SourceConfigError(SourceError):
    """The source is misconfigured, for example a missing API key."""


@runtime_checkable
class SubtitleSource(Protocol):
    name: str

    def search(self, media: MediaInfo, language: str) -> list[Candidate]:
        """Return every candidate for a language. Does not spend quota."""

    def download(self, candidate: Candidate) -> DownloadResult:
        """Download one candidate. May spend one quota unit."""
