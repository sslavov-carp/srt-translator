from __future__ import annotations

import codecs
import os
import re
from dataclasses import dataclass
from pathlib import Path

_TIMING_LINE_RE = re.compile(
    r"^\s*\d{1,3}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*"
    r"\d{1,3}:\d{2}:\d{2}[,.]\d{1,3}(?:\s+.*)?$"
)
_CLOCK_RE = re.compile(r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})")


class SrtError(Exception):
    """Raised when an SRT file cannot be parsed or written safely."""


def _decode_bytes(data: bytes, encoding: str | None) -> tuple[str, str]:
    if encoding:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            raise SrtError(
                f"Could not decode the input as {encoding}. "
                "Try --input-encoding with the correct encoding."
            ) from exc

    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16"), "utf-16"

    # A UTF-16 file without a BOM is uncommon, but decoding it as cp1252 would
    # produce a wall of NUL characters and an unusable subtitle file.
    if b"\x00" in data[:1024]:
        raise SrtError(
            "The input appears to contain NUL bytes, so it is probably UTF-16. "
            "Try --input-encoding utf-16."
        )

    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        # Most legacy English subtitle files are Windows-1252. This fallback is
        # only used when UTF-8 decoding fails and no NUL bytes were detected.
        try:
            return data.decode("cp1252"), "cp1252"
        except UnicodeDecodeError as exc:
            raise SrtError(
                "Could not decode the input. Try --input-encoding with the "
                "correct encoding."
            ) from exc


def _detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\n" in text:
        return "\n"
    if "\r" in text:
        return "\r"
    return "\n"


def _parse_clock(value: str) -> float:
    match = re.fullmatch(r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})", value.strip())
    if not match:
        raise SrtError(f"Invalid timestamp: {value!r}")
    hours, minutes, seconds, fraction = match.groups()
    milliseconds = int(fraction.ljust(3, "0")[:3])
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + milliseconds / 1000.0
    )


@dataclass
class Cue:
    prefix_lines: list[str]
    timing_line: str
    text_lines: list[str]
    block_number: int

    @property
    def text(self) -> str:
        return "\n".join(self.text_lines)

    @text.setter
    def text(self, value: str) -> None:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        self.text_lines = value.split("\n") if value else []

    @property
    def label(self) -> str:
        for line in reversed(self.prefix_lines):
            stripped = line.strip()
            if stripped:
                return stripped
        return str(self.block_number)

    def bounds(self) -> tuple[float, float]:
        matches = _CLOCK_RE.findall(self.timing_line)
        if len(matches) < 2:
            raise SrtError(f"Cue {self.label}: could not read start/end timestamps")
        start = _parse_clock(":".join(matches[0][:3]) + "." + matches[0][3])
        end = _parse_clock(":".join(matches[1][:3]) + "." + matches[1][3])
        return start, end

    def duration(self) -> float:
        start, end = self.bounds()
        return max(0.0, end - start)


@dataclass
class SrtDocument:
    cues: list[Cue]
    newline: str = "\n"
    final_newline: bool = True
    encoding: str = "utf-8"

    def render(self) -> str:
        blocks: list[str] = []
        for cue in self.cues:
            lines = list(cue.prefix_lines)
            lines.append(cue.timing_line)
            lines.extend(cue.text_lines)
            blocks.append(self.newline.join(lines))

        rendered = (self.newline * 2).join(blocks)
        if rendered and self.final_newline:
            rendered += self.newline
        return rendered


def parse_srt(text: str, source: str = "<string>") -> SrtDocument:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    newline = _detect_newline(text)
    final_newline = text.endswith(("\n", "\r"))

    lines = normalized.split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    if not blocks:
        raise SrtError(f"{source}: no subtitle blocks found")

    cues: list[Cue] = []
    for block_number, block in enumerate(blocks, start=1):
        timing_index = next(
            (index for index, line in enumerate(block) if _TIMING_LINE_RE.match(line)),
            None,
        )
        if timing_index is None:
            preview = " / ".join(block[:2])
            raise SrtError(
                f"{source}: block {block_number} has no valid timing line"
                + (f" ({preview!r})" if preview else "")
            )

        cue = Cue(
            prefix_lines=block[:timing_index],
            timing_line=block[timing_index],
            text_lines=block[timing_index + 1 :],
            block_number=block_number,
        )
        # Validate the clock now, so malformed timing lines fail before any API
        # calls are made.
        cue.bounds()
        cues.append(cue)

    return SrtDocument(
        cues=cues,
        newline=newline,
        final_newline=final_newline,
        encoding="utf-8",
    )


def read_srt(path: Path, encoding: str | None = None) -> SrtDocument:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SrtError(f"Could not read {path}: {exc}") from exc

    text, detected_encoding = _decode_bytes(data, encoding)
    text = text.lstrip("\ufeff")
    document = parse_srt(text, source=str(path))
    document.encoding = detected_encoding
    return document


def write_srt(document: SrtDocument, path: Path, encoding: str = "utf-8") -> None:
    """Write atomically as UTF-8. Timing and index lines are copied verbatim."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = document.render().encode(encoding)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
