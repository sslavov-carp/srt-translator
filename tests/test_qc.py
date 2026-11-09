from __future__ import annotations

from pathlib import Path

from srt_translator.models import CueRecord
from srt_translator.qc import QcOptions, analyze, format_json_report, format_report


def _record(
    label: str,
    source: str,
    output: str,
    *,
    start: float = 1.0,
    end: float = 2.0,
    translated: bool = True,
) -> CueRecord:
    return CueRecord(
        position=int(label),
        label=label,
        timing="00:00:01,000 --> 00:00:02,000",
        source_text=source,
        output_text=output,
        translated=translated,
        start=start,
        end=end,
    )


def test_qc_clean_record_has_no_issues() -> None:
    result = analyze([_record("1", "Hello", "Здравей")], QcOptions())
    assert result.issue_count == 0


def test_qc_flags_length_cps_and_missing_cyrillic() -> None:
    result = analyze(
        [
            _record(
                "2",
                "Hello",
                "This is a very long subtitle line that definitely exceeds the limit.",
                start=1.0,
                end=1.2,
            )
        ],
        QcOptions(max_line=42, max_cps=17),
    )
    kinds = set(result.by_kind)
    assert "line-length" in kinds
    assert "cps" in kinds
    assert "no-cyrillic" in kinds


def test_qc_flags_tag_changes_and_empty_translation() -> None:
    result = analyze(
        [
            _record("3", "<i>Hi</i>", "<b>Здравей</b>"),
            _record("4", "Hello", ""),
        ],
        QcOptions(),
    )
    kinds = set(result.by_kind)
    assert "tags" in kinds
    assert "empty" in kinds


def test_qc_flags_invalid_duration() -> None:
    result = analyze(
        [_record("5", "Hello", "Здравей", start=2.0, end=2.0)],
        QcOptions(),
    )
    assert "duration" in result.by_kind


def test_qc_reports_are_utf8(tmp_path: Path) -> None:
    record = _record("1", "Hello", "Здравей")
    result = analyze([record], QcOptions())
    text = format_report(
        [record],
        result,
        input_path=Path("in.srt"),
        output_path=Path("out.srt"),
        translated=1,
        cached=0,
        skipped=0,
        dry_run=False,
    )
    json_text = format_json_report(
        [record],
        result,
        input_path=Path("in.srt"),
        output_path=Path("out.srt"),
        translated=1,
        cached=0,
        skipped=0,
        dry_run=False,
    )
    assert "Здравей" not in text
    assert "Warnings: 0" in text
    assert '"warnings": 0' in json_text
