from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .models import Candidate, MediaInfo, RankedCandidate

_RESOLUTION_RE = re.compile(r"\b(2160p|1440p|1080p|720p|480p)\b", re.IGNORECASE)
_SOURCE_RE = re.compile(
    r"\b(blu-?ray|bd-?rip|br-?rip|web-?dl|web-?rip|web|hdtv|dvd-?rip|remux)\b",
    re.IGNORECASE,
)
_CODEC_RE = re.compile(r"\b(x264|x265|h\.?264|h\.?265|hevc|avc)\b", re.IGNORECASE)
_GROUP_RE = re.compile(r"-([A-Za-z0-9]{2,})$")
_CUT_RE = re.compile(
    r"\b(extended|unrated|director'?s cut|theatrical|remastered|imax)\b",
    re.IGNORECASE,
)

SRT_FORMATS = {"srt", "subrip"}


@dataclass(frozen=True)
class RankOptions:
    allow_machine: bool = False
    min_score: int = 50


def _profile(text: str) -> dict[str, set[str]]:
    normalized = text.replace(".", " ").replace("_", " ")
    sources = {
        match.group(1).lower().replace("-", "") for match in _SOURCE_RE.finditer(normalized)
    }
    codecs = {
        match.group(1).lower().replace(".", "") for match in _CODEC_RE.finditer(normalized)
    }
    group_match = _GROUP_RE.search(text.strip())
    return {
        "resolution": {match.lower() for match in _RESOLUTION_RE.findall(normalized)},
        "source": sources,
        "codec": codecs,
        "cut": {match.lower() for match in _CUT_RE.findall(normalized)},
        "group": {group_match.group(1).lower()} if group_match else set(),
    }


def _candidate_text(candidate: Candidate) -> str:
    return f"{candidate.release} {candidate.filename}".strip()


def _release_points(candidate: Candidate, media: MediaInfo) -> tuple[int, list[str]]:
    if not media.release_name:
        return 0, []

    wanted = _profile(media.release_name)
    offered = _profile(_candidate_text(candidate))
    points = 0
    reasons: list[str] = []

    for key, weight, label in (
        ("resolution", 60, "resolution"),
        ("source", 60, "source"),
        ("codec", 40, "codec"),
        ("cut", 40, "cut"),
    ):
        if wanted[key] and offered[key] and wanted[key] & offered[key]:
            points += weight
            reasons.append(f"+{weight} {label} match")

    if wanted["group"] & offered["group"]:
        points += 60
        reasons.append("+60 release group match")

    return min(points, 200), reasons


def rejection_reason(
    candidate: Candidate,
    media: MediaInfo,
    *,
    language: str | None = None,
    options: RankOptions | None = None,
) -> str | None:
    options = options or RankOptions()

    if language is not None and candidate.language.lower() != language.lower():
        return f"wrong language ({candidate.language})"
    if candidate.nb_cd > 1:
        return f"multi-CD subtitle ({candidate.nb_cd} files)"
    if candidate.foreign_parts_only:
        return "foreign parts only"
    if candidate.subtitle_format.lower() not in SRT_FORMATS:
        return f"unsupported format ({candidate.subtitle_format})"
    if (candidate.machine_translated or candidate.ai_translated) and not options.allow_machine:
        return "machine-translated subtitle"

    if media.is_episode:
        if (
            candidate.season is not None
            and media.season is not None
            and candidate.season != media.season
        ):
            return f"wrong season ({candidate.season})"
        if (
            candidate.episode is not None
            and media.episode is not None
            and candidate.episode != media.episode
        ):
            return f"wrong episode ({candidate.episode})"

    return None


def score_candidate(candidate: Candidate, media: MediaInfo) -> tuple[int, tuple[str, ...]]:
    score = 0
    reasons: list[str] = []

    if candidate.moviehash_match:
        score += 1000
        reasons.append("+1000 exact file hash")

    release_points, release_reasons = _release_points(candidate, media)
    score += release_points
    reasons.extend(release_reasons)

    if candidate.from_trusted:
        score += 40
        reasons.append("+40 trusted uploader")

    if candidate.rating > 0:
        points = min(50, round(candidate.rating * 5))
        score += points
        reasons.append(f"+{points} rating {candidate.rating:g}")

    if candidate.download_count > 0:
        points = min(50, round(math.log1p(candidate.download_count) * 5))
        score += points
        reasons.append(f"+{points} downloads {candidate.download_count}")

    if candidate.hearing_impaired:
        score -= 15
        reasons.append("-15 hearing impaired")
    else:
        score += 20
        reasons.append("+20 not hearing impaired")

    if candidate.ai_translated or candidate.machine_translated:
        score -= 30
        reasons.append("-30 machine translated")

    if candidate.fps is not None and media.fps is not None:
        if abs(candidate.fps - media.fps) <= 0.05:
            score += 30
            reasons.append("+30 frame rate match")
        else:
            score -= 50
            reasons.append(
                f"-50 frame rate mismatch ({candidate.fps:g} vs {media.fps:g})"
            )

    score += 10
    reasons.append("+10 SRT format")

    return score, tuple(reasons)


def rank(
    candidates: list[Candidate],
    media: MediaInfo,
    *,
    language: str | None = None,
    options: RankOptions | None = None,
) -> list[RankedCandidate]:
    options = options or RankOptions()
    ranked: list[RankedCandidate] = []

    for candidate in candidates:
        score, reasons = score_candidate(candidate, media)
        rejection = rejection_reason(
            candidate, media, language=language, options=options
        )
        if rejection is None and score < options.min_score:
            rejection = f"score {score} below minimum {options.min_score}"
        ranked.append(
            RankedCandidate(
                candidate=candidate,
                score=score,
                reasons=reasons,
                rejection=rejection,
            )
        )

    ranked.sort(
        key=lambda item: (
            item.accepted,
            item.score,
            item.candidate.moviehash_match,
            item.candidate.download_count,
        ),
        reverse=True,
    )
    return ranked
