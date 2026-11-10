from __future__ import annotations

import codecs
import re
import subprocess
import sys
from pathlib import Path

from ..srt_io import SrtDocument, SrtError, parse_srt
from .models import FetchError, ValidatedSubtitle


class ValidationError(FetchError):
    """A downloaded subtitle is unusable; the next candidate may work."""


_AD_PATTERNS = (
    re.compile(
        r"^\s*(sub(title)?s?|captions?)\s+"
        r"(by|from|made|created|synced|edited|corrected|ripped|downloaded)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(synced?|sync(ed)?(\s+and\s+\w+)?|ripped|transcri(bed|pt)|"
        r"encoded|corrected|edited)\s+by\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*(copyright|©|\(c\))\b", re.IGNORECASE),
    re.compile(r"(?:https?://?|www\.)\S+", re.IGNORECASE),
    re.compile(
        r"^\s*(opensubtitles|addic7ed|subscene|podnapisi|yify|subdl|"
        r"subs?\.?(sab|sunacs|yavka))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(превод|субтитри|корекция|редакция|синхронизация)\b",
        re.IGNORECASE,
    ),
    # Translator credits such as "®Translator's Heaven" or "Translated by X".
    re.compile(r"^\s*[®©™]?\s*translator'?s?\b", re.IGNORECASE),
    re.compile(r"\btranslated\s+by\b", re.IGNORECASE),
    re.compile(r"^\s*[®©™]\s*\S", re.IGNORECASE),
    # Letter-spaced group tags such as "Г Е Н Е З И С".
    re.compile(r"^\s*(?:[A-ZА-Я] ){2,}[A-ZА-Я]\s*$"),
)

# A short, single-token name with a digit, such as "dimi123". These often sit
# next to a translator credit and are otherwise hard to tell apart from
# dialogue.
_CREDIT_HANDLE_RE = re.compile(r"^(?=.*\d)[A-Za-z0-9_.-]{3,24}$")

_DURATION_TOLERANCE_SECONDS = 60.0
_DURATION_TOLERANCE_RATIO = 0.02


def _cyrillic_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    cyrillic = sum(1 for char in letters if "\u0400" <= char <= "\u04ff")
    return cyrillic / len(letters)


def _decode_utf16(data: bytes) -> str | None:
    if b"\x00" not in data[:2048]:
        return None
    for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if text.count("\x00") == 0 and ":" in text:
            return text
    return None


def decode_subtitle(data: bytes, language: str | None = None) -> tuple[str, str]:
    """Decode subtitle bytes to text. Returns the text and the encoding used."""
    if not data or not data.strip():
        raise ValidationError("the downloaded file is empty")

    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16"), "utf-16"

    utf16 = _decode_utf16(data)
    if utf16 is not None:
        return utf16, "utf-16"

    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    # Legacy Bulgarian subtitles are usually Windows-1251. Decoding as
    # Windows-1252 always succeeds and would silently produce mojibake, so the
    # Cyrillic ratio decides.
    if language == "bg":
        try:
            text = data.decode("cp1251")
        except UnicodeDecodeError:
            text = None
        if text is not None and _cyrillic_ratio(text) > 0.2:
            return text, "cp1251"

    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
    except Exception:  # noqa: BLE001 - charset-normalizer can raise unexpected errors
        best = None

    if best is not None:
        text = str(best)
        if text:
            if language == "bg" and _cyrillic_ratio(text) < 0.2:
                try:
                    cyrillic = data.decode("cp1251")
                except UnicodeDecodeError:
                    cyrillic = None
                if cyrillic is not None and _cyrillic_ratio(cyrillic) > 0.2:
                    return cyrillic, "cp1251"
            return text, best.encoding or "unknown"

    for encoding in ("cp1251", "cp1252", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue

    raise ValidationError("could not decode the subtitle text")


def is_ad_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return any(pattern.search(stripped) for pattern in _AD_PATTERNS)


def clean_document(document: SrtDocument) -> list[str]:
    """Remove ad/watermark lines near the start and end. Returns removed lines."""
    total = len(document.cues)
    edge = {index for index in range(total) if index < 3 or index >= total - 3}
    removed: list[str] = []
    ad_indexes: set[int] = set()

    for index in edge:
        cue = document.cues[index]
        lines: list[str] = []
        had_ad = False
        for line in cue.text_lines:
            if is_ad_line(line):
                removed.append(line.strip())
                had_ad = True
                continue
            lines.append(line)

        if had_ad:
            # The credit and the uploader handle often share one cue.
            filtered: list[str] = []
            for line in lines:
                token = line.strip()
                if token and _CREDIT_HANDLE_RE.fullmatch(token):
                    removed.append(token)
                else:
                    filtered.append(line)
            lines = filtered
            ad_indexes.add(index)

        cue.text_lines = lines

    # Translator handles often follow or precede the credit line itself.
    for index in sorted(edge - ad_indexes):
        cue = document.cues[index]
        if not (ad_indexes & {index - 1, index + 1}):
            continue
        tokens = [line.strip() for line in cue.text_lines if line.strip()]
        if tokens and all(_CREDIT_HANDLE_RE.fullmatch(token) for token in tokens):
            removed.extend(tokens)
            cue.text_lines = []

    kept = [
        cue for cue in document.cues if any(line.strip() for line in cue.text_lines)
    ]

    if not kept:
        raise ValidationError("every cue looked like an advertisement")

    for position, cue in enumerate(kept, start=1):
        cue.prefix_lines = [str(position)]
        cue.block_number = position

    document.cues = kept
    return removed


def probe_duration(path: Path) -> float | None:
    """Read the video duration with ffprobe, or return None when unavailable."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    extra: dict[str, object] = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            **extra,  # type: ignore[arg-type]
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None
    try:
        duration = float(result.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None
    return duration if duration > 0 else None


def validate(
    data: bytes,
    *,
    language: str | None = None,
    duration: float | None = None,
    clean: bool = True,
    strict_duration: bool = False,
) -> ValidatedSubtitle:
    text, encoding = decode_subtitle(data, language=language)

    try:
        document = parse_srt(text.lstrip("\ufeff"), source="<download>")
    except SrtError as exc:
        raise ValidationError(f"not a valid SRT file: {exc}") from exc

    if not document.cues:
        raise ValidationError("the subtitle file contains no cues")

    warnings: list[str] = []
    removed = clean_document(document) if clean else []

    last_timestamp = 0.0
    for cue in document.cues:
        try:
            _start, end = cue.bounds()
        except SrtError as exc:
            raise ValidationError(str(exc)) from exc
        last_timestamp = max(last_timestamp, end)

    if last_timestamp <= 0:
        raise ValidationError("the subtitle has no usable timestamps")

    if duration is not None and duration > 0:
        tolerance = max(
            _DURATION_TOLERANCE_SECONDS, duration * _DURATION_TOLERANCE_RATIO
        )
        difference = abs(duration - last_timestamp)
        if difference > tolerance:
            message = (
                f"subtitle ends at {last_timestamp:.0f}s but the video is "
                f"{duration:.0f}s long ({difference:.0f}s off); it may be for "
                "a different cut"
            )
            if strict_duration:
                raise ValidationError(message)
            warnings.append(message)

    return ValidatedSubtitle(
        document=document,
        encoding=encoding,
        warnings=warnings,
        removed_lines=removed,
        last_timestamp=last_timestamp,
    )
