from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .. import __version__
from ..paths import fetch_dir
from ..srt_io import SrtError, write_srt
from .cache import DownloadCache
from .flow import FetchOutcome, SearchReport, collect, download_first
from .hashing import movie_hash
from .identify import IdentifyError, TmdbClient, identify_input
from .journal import append_entry
from .models import FetchError, MediaInfo
from .naming import output_path
from .quota import QuotaTracker, default_limit
from .ranking import RankedCandidate, RankOptions
from .sources.base import SourceConfigError, SourceError, SubtitleSource
from .sources.opensubtitles import OpenSubtitlesSource
from .validate import probe_duration

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SOURCES = "opensubtitles"
_LANGUAGE_ALIASES = {
    "bg": "bg",
    "bul": "bg",
    "bgr": "bg",
    "bulgarian": "bg",
    "en": "en",
    "eng": "en",
    "english": "en",
    "en-us": "en",
    "en-gb": "en",
}


def _load_environment() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv()


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _stderr_log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _normalize_language(value: str) -> str:
    lowered = value.strip().lower()
    normalized = _LANGUAGE_ALIASES.get(lowered, lowered)
    if not normalized.isalpha() or len(normalized) != 2:
        raise FetchError(f"Unsupported language {value!r}; use a two-letter code such as bg or en")
    return normalized


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch_subs.py",
        description=(
            "Find and download subtitles for a movie or episode. Searches "
            "Bulgarian first, then the fallback language, and saves the best "
            "match next to the video or in the current directory."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "input",
        help=(
            "video file, title text such as \"Breaking Bad S02E05\", an IMDb "
            "id (tt1375666), or a TMDB id (tmdb:27205)"
        ),
    )
    parser.add_argument(
        "--lang",
        default=os.environ.get("SUBS_PREFERRED_LANG", "bg"),
        help="preferred subtitle language",
    )
    parser.add_argument(
        "--fallback-lang",
        default=os.environ.get("SUBS_FALLBACK_LANG", "en"),
        help="language to try when the preferred one is unavailable",
    )
    parser.add_argument("--imdb", dest="imdb_id", help="explicit IMDb id (tt...)")
    parser.add_argument("--tmdb", dest="tmdb_id", type=int, help="explicit TMDB id")
    parser.add_argument("--season", type=int, help="season number override")
    parser.add_argument("--episode", type=int, help="episode number override")
    parser.add_argument("--year", type=int, help="release year override")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be downloaded without spending quota",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing subtitle file",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=50,
        help="reject candidates scoring below this",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=2,
        help="try this many candidates when validation fails",
    )
    parser.add_argument(
        "--max-downloads",
        type=int,
        help="daily download limit (default: 20 signed in, 5 anonymous)",
    )
    parser.add_argument(
        "--allow-machine",
        action="store_true",
        help="allow machine-translated or AI-translated subtitles",
    )
    parser.add_argument(
        "--no-hash",
        action="store_true",
        help="do not compute the OpenSubtitles moviehash",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="keep advertisement and watermark lines",
    )
    parser.add_argument(
        "--strict-duration",
        action="store_true",
        help="reject subtitles whose length does not match the video",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="do not reuse or store downloaded subtitles",
    )
    parser.add_argument(
        "--sources",
        default=DEFAULT_SOURCES,
        help="comma-separated source adapters",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="directory for the download cache, quota file, and log",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a machine-readable result instead of text",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show score details and cache information",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if not args.input.strip():
        raise FetchError("No input given")
    if (args.season is None) != (args.episode is None):
        raise FetchError("--season and --episode must be used together")
    if args.min_score < 0:
        raise FetchError("--min-score cannot be negative")
    if args.max_attempts < 1:
        raise FetchError("--max-attempts must be at least 1")
    if args.max_downloads is not None and args.max_downloads < 1:
        raise FetchError("--max-downloads must be at least 1")


class _Output:
    def __init__(self, as_json: bool) -> None:
        self.as_json = as_json

    def say(self, message: str = "") -> None:
        if not self.as_json:
            print(message, flush=True)


@dataclass
class ItemOutcome:
    status: str  # found, skipped, dry-run, not-found, error
    message: str = ""
    output: Path | None = None
    language: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def _build_sources(args: argparse.Namespace) -> list[SubtitleSource]:
    names = [name.strip().lower() for name in args.sources.split(",") if name.strip()]
    if not names:
        raise FetchError("--sources must name at least one source")
    if names != [DEFAULT_SOURCES]:
        unknown = [name for name in names if name != DEFAULT_SOURCES]
        if unknown:
            raise FetchError(
                f"Unknown source(s): {', '.join(unknown)}. "
                f"Available: {DEFAULT_SOURCES}"
            )

    api_key = os.environ.get("OPENSUBTITLES_API_KEY")
    if not api_key:
        raise SourceConfigError(
            "OPENSUBTITLES_API_KEY is not set. Create an API key at "
            "https://www.opensubtitles.com/en/users/settings/api and add it "
            "to .env."
        )

    return [
        OpenSubtitlesSource(
            api_key,
            username=os.environ.get("OPENSUBTITLES_USERNAME"),
            password=os.environ.get("OPENSUBTITLES_PASSWORD"),
            user_agent=f"srt-translator {__version__}",
            log=_stderr_log,
        )
    ]


def _build_tmdb() -> TmdbClient:
    return TmdbClient(os.environ.get("TMDB_API_KEY"), log=_stderr_log)


def _login_sources(
    sources: list[SubtitleSource], log: Callable[[str], None]
) -> bool:
    authenticated = False
    for source in sources:
        login = getattr(source, "login", None)
        if callable(login):
            try:
                authenticated = bool(login()) or authenticated
            except (FetchError, SourceError) as exc:
                log(f"{source.name}: login failed: {exc}")
    return authenticated


def _resolve_media(
    args: argparse.Namespace, tmdb: TmdbClient | None, log: Callable[[str], None]
) -> MediaInfo:
    try:
        media = identify_input(
            args.input,
            season=args.season,
            episode=args.episode,
            year=args.year,
            imdb_id=args.imdb_id,
            tmdb_id=args.tmdb_id,
        )
    except (IdentifyError, OSError) as exc:
        raise FetchError(str(exc)) from exc

    if tmdb is not None:
        try:
            media, warning = tmdb.resolve(media)
        except FetchError as exc:
            warning = str(exc)
        if warning:
            log(f"TMDB: {warning}")

    if media.file_path is not None:
        if not args.no_hash:
            try:
                media_hash, size = movie_hash(media.file_path)
                media = _replace_media(media, file_hash=media_hash, file_size=size)
                log(f"moviehash: {media_hash}")
            except FetchError as exc:
                log(f"moviehash skipped: {exc}")

        duration = probe_duration(media.file_path)
        if duration is not None:
            media = _replace_media(media, duration=duration)
            log(f"video duration: {duration:.0f}s")
        else:
            log("ffprobe not available; skipping the duration check")

    return media


def _replace_media(media: MediaInfo, **changes: Any) -> MediaInfo:
    return replace(media, **changes)


def _print_media(out: _Output, media: MediaInfo) -> None:
    out.say(f"Media:    {media.label()}")
    ids = []
    if media.imdb_id:
        ids.append(f"IMDb {media.imdb_id}")
    if media.tmdb_id:
        ids.append(f"TMDB {media.tmdb_id}")
    if media.parent_imdb_id:
        ids.append(f"show IMDb {media.parent_imdb_id}")
    if media.parent_tmdb_id:
        ids.append(f"show TMDB {media.parent_tmdb_id}")
    if media.file_hash:
        ids.append(f"hash {media.file_hash}")
    if ids:
        out.say(f"Ids:      {', '.join(ids)}")


def _print_report(out: _Output, report: SearchReport, *, verbose: bool) -> None:
    accepted = report.accepted
    total = len(report.ranked)
    out.say(
        f"  {report.language}: {len(accepted)} usable candidate(s) "
        f"of {total}"
    )
    for index, item in enumerate(report.ranked[:5], start=1):
        marker = " " if item.accepted else "!"
        candidate = item.candidate
        name = candidate.release or candidate.filename or candidate.candidate_id
        out.say(f"   {marker} {index}. {item.score:>5} pts  {name}")
        if item.rejection:
            out.say(f"        rejected: {item.rejection}")
        elif verbose and item.reasons:
            out.say(f"        {', '.join(item.reasons)}")


def _print_dry_run(
    out: _Output,
    report: SearchReport,
    *,
    quota: QuotaTracker,
) -> None:
    best = report.best
    if best is None:
        return
    out.say(f"Would download: {best.candidate.describe()}")
    out.say(f"  score: {best.score}")
    if best.reasons:
        out.say(f"  reasons: {', '.join(best.reasons)}")
    out.say(f"  quota: {quota.describe()}")


def _media_payload(media: MediaInfo) -> dict[str, Any]:
    payload = asdict(media)
    if media.file_path is not None:
        payload["file_path"] = str(media.file_path)
    return payload


def _candidate_payload(item: RankedCandidate) -> dict[str, Any]:
    payload = asdict(item.candidate)
    payload.pop("extra", None)
    payload["score"] = item.score
    payload["reasons"] = list(item.reasons)
    payload["rejection"] = item.rejection
    return payload


def _record(
    log_path: Path,
    media: MediaInfo,
    *,
    language: str | None,
    selected: RankedCandidate | None,
    outcome: str,
    message: str = "",
    output: Path | None = None,
    quota: QuotaTracker | None = None,
) -> None:
    candidate = selected.candidate if selected is not None else None
    append_entry(
        log_path,
        {
            "outcome": outcome,
            "message": message,
            "title": media.title,
            "kind": media.kind,
            "year": media.year,
            "season": media.season,
            "episode": media.episode,
            "imdb_id": media.imdb_id,
            "tmdb_id": media.tmdb_id,
            "parent_imdb_id": media.parent_imdb_id,
            "parent_tmdb_id": media.parent_tmdb_id,
            "file_hash": media.file_hash,
            "language": language,
            "source": candidate.source if candidate else None,
            "candidate_id": candidate.candidate_id if candidate else None,
            "release": candidate.release if candidate else None,
            "score": selected.score if selected else None,
            "hash_match": candidate.moviehash_match if candidate else None,
            "output": str(output) if output is not None else None,
            "machine_translated": False,
            "quota_remaining": quota.remaining if quota is not None else None,
        },
    )


def _fetch_item(
    *,
    args: argparse.Namespace,
    out: _Output,
    media: MediaInfo,
    sources: list[SubtitleSource],
    cache: DownloadCache,
    quota: QuotaTracker,
    log_path: Path,
    options: RankOptions,
    languages: list[str],
) -> ItemOutcome:
    searches: list[dict[str, Any]] = []
    last_report: SearchReport | None = None

    def log(message: str) -> None:
        _stderr_log(message)

    for language in languages:
        target = output_path(media, language)
        if target.exists() and not args.overwrite:
            out.say(f"Already present: {target}")
            _record(
                log_path,
                media,
                language=language,
                selected=None,
                outcome="skipped",
                message="output already exists",
                output=target,
                quota=quota,
            )
            return ItemOutcome(
                status="skipped",
                message=f"{target} already exists (use --overwrite)",
                output=target,
                language=language,
                payload={"searches": searches},
            )

        log(f"Searching {language}...")
        report = collect(media, sources, language, options=options, log=log)
        last_report = report
        searches.append(
            {
                "language": language,
                "candidates": [_candidate_payload(item) for item in report.ranked],
                "errors": report.errors,
            }
        )
        out.say(f"Search:   {language}")
        _print_report(out, report, verbose=args.verbose)

        if args.dry_run:
            if report.best is not None:
                _print_dry_run(out, report, quota=quota)
                _record(
                    log_path,
                    media,
                    language=language,
                    selected=report.best,
                    outcome="dry-run",
                    message="dry run; nothing downloaded",
                    quota=quota,
                )
                return ItemOutcome(
                    status="dry-run",
                    message=f"would download {report.best.candidate.describe()}",
                    language=language,
                    payload={"searches": searches},
                )
            continue

        if report.best is None:
            continue

        log(f"Downloading {report.best.candidate.describe()}")
        outcome: FetchOutcome = download_first(
            report,
            media=media,
            sources=sources,
            cache=cache,
            quota=quota,
            max_attempts=args.max_attempts,
            clean=not args.no_clean,
            strict_duration=args.strict_duration,
            log=log,
        )
        if outcome.quota_exhausted:
            out.say(f"Stopped: {outcome.message}")
            _record(
                log_path,
                media,
                language=language,
                selected=outcome.selected,
                outcome="quota-exhausted",
                message=outcome.message,
                quota=quota,
            )
            return ItemOutcome(
                status="error",
                message=outcome.message,
                language=language,
                payload={"searches": searches},
            )

        if not outcome.ok or outcome.validated is None or outcome.selected is None:
            out.say(f"  {outcome.message}")
            for attempt in outcome.attempts:
                log(f"  attempt: {attempt}")
            continue

        try:
            write_srt(outcome.validated.document, target)
        except (SrtError, OSError) as exc:
            raise FetchError(f"Could not write {target}: {exc}") from exc

        for warning in outcome.validated.warnings:
            out.say(f"Warning: {warning}")
            log(f"Warning: {warning}")

        out.say(f"Saved:    {target}")
        out.say(f"Quota:    {quota.describe()}")
        if args.verbose:
            log(f"cached at {cache.path_for(outcome.selected.candidate.source, outcome.selected.candidate.candidate_id)}")

        _record(
            log_path,
            media,
            language=language,
            selected=outcome.selected,
            outcome="found",
            message=outcome.message,
            output=target,
            quota=quota,
        )
        return ItemOutcome(
            status="found",
            message=outcome.message,
            output=target,
            language=language,
            payload={"searches": searches},
        )

    if last_report is not None:
        _record(
            log_path,
            media,
            language=last_report.language,
            selected=None,
            outcome="not-found",
            message="no usable subtitles",
            quota=quota,
        )
    out.say("Not found: no usable subtitles in the requested languages.")
    return ItemOutcome(
        status="not-found",
        message="no usable subtitles",
        payload={"searches": searches},
    )


def main(
    argv: list[str] | None = None,
    *,
    sources: list[SubtitleSource] | None = None,
    tmdb: TmdbClient | None = None,
) -> int:
    _configure_output()
    _load_environment()
    parser = _build_parser()
    args = parser.parse_args(argv)

    out = _Output(args.json)
    owned_sources: list[SubtitleSource] = []
    owned_tmdb: TmdbClient | None = None
    payload: dict[str, Any] = {}

    try:
        _validate_args(args)
        preferred = _normalize_language(args.lang)
        fallback = _normalize_language(args.fallback_lang)

        languages = [preferred]
        if fallback != preferred:
            languages.append(fallback)

        if tmdb is None:
            tmdb = owned_tmdb = _build_tmdb()
        tmdb_client = tmdb if getattr(tmdb, "enabled", True) else None
        if tmdb_client is None:
            _stderr_log("TMDB_API_KEY is not set; searching by title only")

        media = _resolve_media(args, tmdb_client, _stderr_log)
        _print_media(out, media)

        if sources is None:
            sources = owned_sources = _build_sources(args)
        authenticated = _login_sources(sources, _stderr_log)

        root = args.work_dir if args.work_dir is not None else fetch_dir()
        cache = DownloadCache(root / "downloads", enabled=not args.no_cache)
        quota_path = root / "quota.json"
        log_path = root / "fetch_log.json"

        limit = args.max_downloads
        explicit_limit = limit is not None
        if limit is None:
            env_limit = os.environ.get("SUBS_MAX_DOWNLOADS", "").strip()
            if env_limit.isdigit() and int(env_limit) > 0:
                limit = int(env_limit)
                explicit_limit = True
            else:
                limit = default_limit(authenticated)
        quota = QuotaTracker(
            quota_path, limit=limit, allow_raise=not explicit_limit
        )

        if not quota.can_download() and not args.dry_run:
            raise FetchError(
                f"daily download limit reached ({quota.describe()}); "
                "try again after the reset, raise --max-downloads, or use --dry-run"
            )

        options = RankOptions(
            allow_machine=args.allow_machine,
            min_score=args.min_score,
        )

        result = _fetch_item(
            args=args,
            out=out,
            media=media,
            sources=sources,
            cache=cache,
            quota=quota,
            log_path=log_path,
            options=options,
            languages=languages,
        )

        payload = {
            "version": __version__,
            "input": args.input,
            "media": _media_payload(media),
            "languages": languages,
            "quota": {
                "limit": quota.limit,
                "used": quota.used,
                "remaining": quota.remaining,
                "reset_time_utc": quota.state.reset_time_utc,
            },
            "outcome": {
                "status": result.status,
                "message": result.message,
                "language": result.language,
                "output": str(result.output) if result.output else None,
            },
            **result.payload,
        }

        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        return 0 if result.status in {"found", "skipped", "dry-run"} else 1

    except (FetchError, SourceConfigError, SrtError, OSError, ValueError) as exc:
        _stderr_log(f"Error: {exc}")
        if args.json:
            print(
                json.dumps(
                    {"version": __version__, "error": str(exc)}, ensure_ascii=False
                )
            )
        return 1
    except KeyboardInterrupt:
        _stderr_log("Interrupted.")
        return 130
    finally:
        for source in owned_sources:
            close = getattr(source, "close", None)
            if callable(close):
                close()
        if owned_tmdb is not None:
            owned_tmdb.close()
