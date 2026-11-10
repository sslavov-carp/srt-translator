from __future__ import annotations

import re
from pathlib import Path

from .models import MediaInfo

_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def sanitize_title(title: str) -> str:
    cleaned = re.sub(r"[^\w\-. ]+", "", title, flags=re.UNICODE)
    cleaned = cleaned.replace(" ", ".")
    cleaned = re.sub(r"\.{2,}", ".", cleaned).strip(".-_")
    if not cleaned:
        return "subtitles"
    if cleaned.casefold() in _WINDOWS_RESERVED:
        return cleaned + "_"
    return cleaned


def media_stem(media: MediaInfo) -> str:
    title = sanitize_title(media.title)
    if media.is_episode and media.season is not None and media.episode is not None:
        return f"{title}.S{media.season:02d}E{media.episode:02d}"
    if media.year is not None:
        return f"{title}.{media.year}"
    return title


def suffix_for(language: str, *, machine: bool = False) -> str:
    language = language.lower()
    if machine:
        return f".{language}.machine.srt"
    return f".{language}.srt"


def output_path(
    media: MediaInfo,
    language: str,
    *,
    directory: Path | None = None,
    machine: bool = False,
) -> Path:
    if directory is None:
        if media.file_path is not None:
            directory = media.file_path.parent
        else:
            directory = Path.cwd()
    filename = media_stem(media) + suffix_for(language, machine=machine)
    return Path(directory) / filename
