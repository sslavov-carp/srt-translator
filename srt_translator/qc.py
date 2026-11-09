from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import CueRecord
from .tags import TAG_RE, visible_text

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class QcOptions:
    max_line: int = 42
    max_lines: int = 2
    max_cps: float = 17.0
    check_translation: bool = True


@dataclass
class QcIssue:
    cue: str
    timing: str
    kind: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QcResult:
    issues: list[QcIssue]
    by_kind: dict[str, int]

    @property
    def issue_count(self) -> int:
        return len(self.issues)


def _line_metrics(record: CueRecord, options: QcOptions) -> tuple[list[int], float, float]:
    lines = [line for line in record.output_text.splitlines() if line.strip()]
    lengths = [len(visible_text(line)) for line in lines]
    char_count = len(visible_text(" ".join(lines)))
    duration = max(0.0, record.end - record.start)
    cps = (char_count / duration) if duration > 0 else 0.0
    return lengths, duration, cps


def analyze(records: list[CueRecord], options: QcOptions) -> QcResult:
    issues: list[QcIssue] = []

    def add(record: CueRecord, kind: str, detail: str) -> None:
        issues.append(
            QcIssue(
                cue=record.label,
                timing=record.timing,
                kind=kind,
                detail=detail,
            )
        )

    for record in records:
        lengths, duration, cps = _line_metrics(record, options)
        source_visible = visible_text(record.source_text).strip()
        output_visible = visible_text(record.output_text).strip()

        if duration <= 0:
            add(record, "duration", "end time is not after start time")

        if options.check_translation and record.translated:
            if not output_visible:
                add(record, "empty", "translation is empty")

            if len(_LATIN_RE.findall(source_visible)) >= 3 and not _CYRILLIC_RE.search(
                output_visible
            ):
                add(record, "no-cyrillic", "translation contains no Cyrillic characters")

            source_tags = TAG_RE.findall(record.source_text)
            output_tags = TAG_RE.findall(record.output_text)
            if source_tags != output_tags:
                add(
                    record,
                    "tags",
                    "formatting tags differ from the source cue",
                )

        if len(lengths) > options.max_lines:
            add(
                record,
                "line-count",
                f"{len(lengths)} lines (max {options.max_lines})",
            )

        for index, length in enumerate(lengths, start=1):
            if length > options.max_line:
                add(
                    record,
                    "line-length",
                    f"line {index} has {length} characters (max {options.max_line})",
                )

        if duration > 0 and cps > options.max_cps:
            add(
                record,
                "cps",
                f"{cps:.1f} characters/second (max {options.max_cps:g})",
            )

    by_kind = dict(Counter(issue.kind for issue in issues))
    return QcResult(issues=issues, by_kind=by_kind)


def format_report(
    records: list[CueRecord],
    result: QcResult,
    *,
    input_path: Path,
    output_path: Path,
    translated: int,
    cached: int,
    skipped: int,
    dry_run: bool,
) -> str:
    lines: list[str] = []
    lines.append("English -> Bulgarian SRT translation QC")
    lines.append("=" * 40)
    lines.append(f"Input:  {input_path}")
    lines.append(f"Output: {output_path}")
    lines.append(f"Cues:   {len(records)}")
    lines.append(f"Translated: {translated} ({cached} from cache)")
    lines.append(f"Skipped (no visible text): {skipped}")
    if dry_run:
        lines.append("Mode:   dry run (source text checked, no translation)")
    lines.append(f"Warnings: {result.issue_count}")
    if result.by_kind:
        for kind in sorted(result.by_kind):
            lines.append(f"  {kind}: {result.by_kind[kind]}")
    lines.append("")

    if result.issues:
        lines.append("Details:")
        for issue in result.issues:
            lines.append(
                f"Cue {issue.cue} [{issue.timing}] {issue.kind}: {issue.detail}"
            )
    else:
        lines.append("No QC warnings.")

    return "\n".join(lines) + "\n"


def format_json_report(
    records: list[CueRecord],
    result: QcResult,
    *,
    input_path: Path,
    output_path: Path,
    translated: int,
    cached: int,
    skipped: int,
    dry_run: bool,
) -> str:
    payload = {
        "input": str(input_path),
        "output": str(output_path),
        "dry_run": dry_run,
        "summary": {
            "cues": len(records),
            "translated": translated,
            "cached": cached,
            "skipped": skipped,
            "warnings": result.issue_count,
            "by_kind": result.by_kind,
        },
        "issues": [issue.to_dict() for issue in result.issues],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
