from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .cache import DownloadCache
from .models import (
    Candidate,
    DownloadResult,
    MediaInfo,
    RankedCandidate,
    ValidatedSubtitle,
)
from .quota import QuotaTracker
from .ranking import RankOptions, rank
from .sources.base import SourceError, SubtitleSource
from .validate import ValidationError, validate


@dataclass
class SearchReport:
    language: str
    ranked: list[RankedCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> list[RankedCandidate]:
        return [item for item in self.ranked if item.accepted]

    @property
    def best(self) -> RankedCandidate | None:
        accepted = self.accepted
        return accepted[0] if accepted else None


@dataclass
class FetchOutcome:
    ok: bool
    message: str
    language: str | None = None
    selected: RankedCandidate | None = None
    download: DownloadResult | None = None
    validated: ValidatedSubtitle | None = None
    attempts: list[str] = field(default_factory=list)
    quota_exhausted: bool = False


def collect(
    media: MediaInfo,
    sources: Iterable[SubtitleSource],
    language: str,
    *,
    options: RankOptions | None = None,
    log: Callable[[str], None] | None = None,
) -> SearchReport:
    """Search every source for one language. Does not spend download quota."""
    log = log or (lambda message: None)
    report = SearchReport(language=language)
    candidates: list[Candidate] = []

    for source in sources:
        try:
            found = source.search(media, language)
        except SourceError as exc:
            report.errors.append(f"{source.name}: {exc}")
            log(f"  {source.name}: {exc}")
            continue
        log(f"  {source.name}: {len(found)} candidate(s) for {language}")
        candidates.extend(found)

    report.ranked = rank(candidates, media, language=language, options=options)
    return report


def download_first(
    report: SearchReport,
    *,
    media: MediaInfo,
    sources: Iterable[SubtitleSource],
    cache: DownloadCache,
    quota: QuotaTracker,
    max_attempts: int = 2,
    clean: bool = True,
    strict_duration: bool = False,
    log: Callable[[str], None] | None = None,
) -> FetchOutcome:
    """Download and validate the best candidates until one works."""
    log = log or (lambda message: None)
    by_name = {source.name: source for source in sources}
    attempts: list[str] = []

    accepted = report.accepted
    if not accepted:
        return FetchOutcome(
            ok=False,
            message=f"no usable {report.language} candidates",
            language=report.language,
            attempts=attempts,
        )

    for item in accepted[: max(1, max_attempts)]:
        candidate = item.candidate
        attempts.append(candidate.describe())

        cached = cache.get(candidate.source, candidate.candidate_id)
        if cached is not None:
            log(f"  using cached subtitle {candidate.describe()}")
            try:
                validated = validate(
                    cached,
                    language=report.language,
                    duration=media.duration,
                    clean=clean,
                    strict_duration=strict_duration,
                )
            except ValidationError as exc:
                attempts.append(f"cached copy rejected: {exc}")
                log(f"  cached copy rejected: {exc}")
                continue
            return FetchOutcome(
                ok=True,
                message="used cached download",
                language=report.language,
                selected=item,
                download=DownloadResult(data=cached, from_cache=True),
                validated=validated,
                attempts=attempts,
            )

        if not quota.can_download():
            return FetchOutcome(
                ok=False,
                message=f"daily download limit reached ({quota.describe()})",
                language=report.language,
                selected=item,
                attempts=attempts,
                quota_exhausted=True,
            )

        source = by_name.get(candidate.source)
        if source is None:
            attempts.append(f"source {candidate.source} is not configured")
            continue

        try:
            result = source.download(candidate)
        except SourceError as exc:
            attempts.append(f"download failed: {exc}")
            log(f"  download failed: {exc}")
            continue

        quota.record(remaining=result.remaining, reset_time_utc=result.reset_time_utc)
        cache.put(
            candidate.source,
            candidate.candidate_id,
            result.data,
            filename=result.filename or candidate.filename,
            language=report.language,
            release=candidate.release,
        )

        try:
            validated = validate(
                result.data,
                language=report.language,
                duration=media.duration,
                clean=clean,
                strict_duration=strict_duration,
            )
        except ValidationError as exc:
            attempts.append(f"rejected after download: {exc}")
            log(f"  rejected {candidate.describe()}: {exc}")
            continue

        return FetchOutcome(
            ok=True,
            message="downloaded",
            language=report.language,
            selected=item,
            download=result,
            validated=validated,
            attempts=attempts,
        )

    return FetchOutcome(
        ok=False,
        message="every candidate failed validation",
        language=report.language,
        attempts=attempts,
    )
