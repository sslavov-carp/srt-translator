from __future__ import annotations

from pathlib import Path

import pytest

from srt_translator.srt_io import SrtError, parse_srt, read_srt, write_srt

FIXTURE = Path(__file__).parent / "fixtures" / "sample.en.srt"


def test_parse_and_render_round_trip() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    document = parse_srt(text)

    assert len(document.cues) == 5
    assert document.cues[0].label == "1"
    assert document.cues[0].timing_line == "00:00:01,000 --> 00:00:03,000"
    assert document.cues[0].text == "Hello, world!"
    assert document.cues[2].text == "{\\an8}Top line\nSecond line."
    assert document.cues[3].text == ""
    assert document.render() == text


def test_crlf_is_preserved() -> None:
    text = (
        "1\r\n"
        "00:00:01,000 --> 00:00:02,000\r\n"
        "Hello\r\n"
        "\r\n"
        "2\r\n"
        "00:00:03,000 --> 00:00:04,000\r\n"
        "World\r\n"
    )
    document = parse_srt(text)
    assert document.newline == "\r\n"
    assert document.render() == text


def test_missing_final_newline_is_preserved() -> None:
    text = "1\n00:00:01,000 --> 00:00:02,000\nHello"
    document = parse_srt(text)
    assert document.final_newline is False
    assert document.render() == text


def test_read_bom_and_write_utf8(tmp_path: Path) -> None:
    path = tmp_path / "bom.srt"
    path.write_bytes(
        b"\xef\xbb\xbf"
        b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"
    )
    document = read_srt(path)
    assert document.cues[0].text == "Hello"

    output = tmp_path / "out.srt"
    document.cues[0].text = "Здравей"
    write_srt(document, output)
    data = output.read_bytes()
    assert data.startswith(b"1\n")
    assert "Здравей".encode() in data


def test_malformed_timing_raises() -> None:
    with pytest.raises(SrtError):
        parse_srt("1\nnot a timestamp\nHello\n")


def test_bounds_and_duration() -> None:
    document = parse_srt("1\n01:02:03,500 --> 01:02:05,000\nHello\n")
    start, end = document.cues[0].bounds()
    assert start == pytest.approx(3723.5)
    assert end == pytest.approx(3725.0)
    assert document.cues[0].duration() == pytest.approx(1.5)
