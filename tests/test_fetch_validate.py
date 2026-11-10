from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from srt_translator.fetch.validate import (
    ValidationError,
    clean_document,
    decode_subtitle,
    is_ad_line,
    probe_duration,
    validate,
)
from srt_translator.srt_io import parse_srt


def _srt(*texts: str | tuple[str, ...]) -> str:
    blocks = []
    for index, text in enumerate(texts, start=1):
        body = "\n".join(text) if isinstance(text, tuple) else text
        start = index * 2
        blocks.append(
            f"{index}\n00:00:{start:02d},000 --> 00:00:{start + 1:02d},000\n{body}"
        )
    return "\n\n".join(blocks) + "\n"


SAMPLE = _srt(("Здравей", "Hello"))


def test_decodes_legacy_bulgarian_cp1251() -> None:
    text, encoding = decode_subtitle(SAMPLE.encode("cp1251"), language="bg")

    assert encoding == "cp1251"
    assert "Здравей" in text


def test_decodes_utf8_with_bom() -> None:
    data = b"\xef\xbb\xbf" + SAMPLE.encode("utf-8")

    text, encoding = decode_subtitle(data)

    assert encoding == "utf-8-sig"
    assert text.lstrip("\ufeff").startswith("1")


def test_rejects_empty_download() -> None:
    with pytest.raises(ValidationError):
        decode_subtitle(b"")


def test_validate_returns_document_and_strips_ads() -> None:
    raw = _srt(
        ("Subtitles by SomeSite", "First line"),
        ("Second line",),
        ("Middle line",),
        ("Last line", "https://example.com"),
    )

    result = validate(raw.encode("utf-8"), language="bg")

    assert result.document.cues
    assert "Subtitles by SomeSite" in result.removed_lines
    assert "https://example.com" in result.removed_lines
    assert "First line" in result.document.cues[0].text


def test_ads_in_the_middle_are_kept() -> None:
    raw = _srt(
        ("One",),
        ("Two",),
        ("Subtitles by SomeSite",),
        ("Three",),
        ("Four",),
    )

    result = validate(raw.encode("utf-8"), language="bg")

    assert any("Subtitles by SomeSite" in cue.text for cue in result.document.cues)


def test_ad_only_file_is_rejected() -> None:
    document = parse_srt(_srt(("Subtitles by SomeSite",)))

    with pytest.raises(ValidationError):
        clean_document(document)


@pytest.mark.parametrize(
    "line",
    [
        "Subtitles by OpenSubtitles",
        "Synced by someone",
        "www.example.com",
        "Превод: Иван",
        "OpenSubtitles",
    ],
)
def test_ad_line_detection(line: str) -> None:
    assert is_ad_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "Hello there",
        "I synced the files yesterday",
        "По света и у нас",
    ],
)
def test_normal_lines_are_not_ads(line: str) -> None:
    assert not is_ad_line(line)


def test_duration_mismatch_is_a_warning() -> None:
    result = validate(SAMPLE.encode("utf-8"), duration=3600.0)

    assert result.warnings
    assert "different cut" in result.warnings[0]


def test_strict_duration_raises() -> None:
    with pytest.raises(ValidationError):
        validate(SAMPLE.encode("utf-8"), duration=3600.0, strict_duration=True)


def test_close_duration_is_accepted() -> None:
    result = validate(SAMPLE.encode("utf-8"), duration=4.0)

    assert not result.warnings


def test_invalid_srt_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate(b"this is not a subtitle file")


def test_probe_duration_returns_none_without_ffprobe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(subprocess, "run", explode)

    assert probe_duration(tmp_path / "video.mkv") is None


def test_probe_duration_parses_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Result:
        returncode = 0
        stdout = "1234.5\n"

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())

    assert probe_duration(tmp_path / "video.mkv") == pytest.approx(1234.5)
