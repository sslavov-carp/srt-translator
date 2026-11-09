from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .errors import TranslationError
from .gemini import GeminiTranslator
from .models import CueRecord
from .paths import cache_root
from .paths import work_dir_for as _aux_work_dir
from .pipeline import Pipeline, PipelineResult
from .qc import (
    QcOptions,
    QcResult,
    analyze,
    format_json_report,
    format_report,
    write_text,
)
from .srt_io import SrtDocument, SrtError, read_srt, write_srt
from .tags import TagError, visible_text

DEFAULT_MODEL = "gemini-3.5-flash-lite"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET_SUFFIX = ".bg.srt"


def _load_environment() -> None:
    # Project .env first, then a .env in the current directory. Existing
    # environment variables always win.
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv()


def _default_output_path(input_path: Path) -> Path:
    stem = input_path.stem
    lowered = stem.lower()
    for suffix in (".en-us", ".en-gb", ".english", ".eng", ".en"):
        if lowered.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return input_path.with_name(stem + TARGET_SUFFIX)


def _load_glossary(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise TranslationError(f"Could not read glossary {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TranslationError(f"Glossary {path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise TranslationError(
            f"Glossary {path} must be a JSON object mapping English terms to Bulgarian."
        )

    glossary: dict[str, str] = {}
    for source, target in data.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise TranslationError(
                f"Glossary {path} contains a non-string key or value."
            )
        if source.strip() and target.strip():
            glossary[source] = target
    return glossary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="translate_srt.py",
        description=(
            "Translate English SRT subtitles into Bulgarian while preserving "
            "every index and timing line. Accepts a single .srt file or a "
            "folder of .srt files."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "input",
        type=Path,
        help="input English .srt file or a folder containing .srt files",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output Bulgarian .srt file (single input file only)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="write translated .srt files into this folder",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="also translate .srt files in subfolders",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
        help="Gemini model id",
    )
    parser.add_argument("--glossary", type=Path, help="optional glossary JSON file")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=40,
        help="cues per API request (suggested: 30-50)",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=2,
        help="surrounding cues supplied as context",
    )
    parser.add_argument(
        "--rpm",
        type=float,
        default=float(os.environ.get("GEMINI_RPM", "10")),
        help="maximum API requests per minute; <= 0 disables pacing",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="retries for rate limits, server errors, and network failures",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="model temperature",
    )
    parser.add_argument(
        "--max-line",
        type=int,
        default=42,
        help="maximum characters per subtitle line for QC/wrapping",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=2,
        help="maximum subtitle lines per cue for QC/wrapping",
    )
    parser.add_argument(
        "--max-cps",
        type=float,
        default=17.0,
        help="maximum characters per second before a QC warning",
    )
    parser.add_argument(
        "--no-wrap",
        action="store_true",
        help="keep translator line breaks instead of reflowing text",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="disable the per-cue resume cache",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="write detailed qc.txt/qc.json reports to the work directory",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="directory for cache and QC reports (default: OS cache directory)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return exit code 1 when QC warnings are present",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and inspect files without calling the API or writing output",
    )
    parser.add_argument(
        "--input-encoding",
        help="force an input encoding (for example utf-8-sig or cp1252)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite output files that already exist",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show cache hits and extra progress information",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if not args.input.exists():
        raise TranslationError(f"Input path does not exist: {args.input}")
    if not args.input.is_file() and not args.input.is_dir():
        raise TranslationError(f"Input path is not a file or folder: {args.input}")
    if args.output is not None and args.output_dir is not None:
        raise TranslationError("Use either --output or --output-dir, not both")
    if args.input.is_dir() and args.output is not None:
        raise TranslationError(
            "--output only works with a single input file; use --output-dir for folders"
        )
    if args.batch_size < 1:
        raise TranslationError("--batch-size must be at least 1")
    if args.context < 0:
        raise TranslationError("--context cannot be negative")
    if args.max_line < 1:
        raise TranslationError("--max-line must be at least 1")
    if args.max_lines < 1:
        raise TranslationError("--max-lines must be at least 1")
    if args.max_cps <= 0:
        raise TranslationError("--max-cps must be greater than 0")


def _discover_srt_files(
    input_path: Path,
    *,
    recursive: bool,
    output_dir: Path | None,
) -> list[Path]:
    if input_path.is_file():
        return [input_path]

    excluded_root = output_dir.resolve() if output_dir is not None else None
    candidates = input_path.rglob("*") if recursive else input_path.glob("*")

    files: list[Path] = []
    for path in candidates:
        if not path.is_file() or path.suffix.lower() != ".srt":
            continue
        if path.name.lower().endswith(TARGET_SUFFIX):
            continue
        if excluded_root is not None:
            try:
                if path.resolve().is_relative_to(excluded_root):
                    continue
            except (OSError, ValueError):
                pass
        files.append(path)

    return sorted(files, key=lambda path: str(path).casefold())


def _output_path_for(input_path: Path, args: argparse.Namespace) -> Path:
    if args.output is not None:
        return args.output
    default = _default_output_path(input_path)
    if args.output_dir is not None:
        return args.output_dir / default.name
    return default


def _resolve_work_dir(input_path: Path, args: argparse.Namespace) -> Path:
    root = args.work_dir if args.work_dir is not None else cache_root()
    return _aux_work_dir(input_path, root)


def _source_records(document: SrtDocument) -> list[CueRecord]:
    records: list[CueRecord] = []
    for position, cue in enumerate(document.cues):
        start, end = cue.bounds()
        records.append(
            CueRecord(
                position=position,
                label=cue.label,
                timing=cue.timing_line,
                source_text=cue.text,
                output_text=cue.text,
                translated=False,
                start=start,
                end=end,
            )
        )
    return records


def _print_qc_summary(result: QcResult) -> None:
    if result.issue_count == 0:
        print("QC: no warnings.")
        return
    details = ", ".join(
        f"{kind}={count}" for kind, count in sorted(result.by_kind.items())
    )
    print(f"QC: {result.issue_count} warning(s) ({details}).")


def _write_reports(
    *,
    records: list[CueRecord],
    result: QcResult,
    input_path: Path,
    output_path: Path,
    work_dir: Path,
    translated: int,
    cached: int,
    skipped: int,
    dry_run: bool,
) -> tuple[Path, Path]:
    text_path = work_dir / "qc.txt"
    json_path = work_dir / "qc.json"
    text = format_report(
        records,
        result,
        input_path=input_path,
        output_path=output_path,
        translated=translated,
        cached=cached,
        skipped=skipped,
        dry_run=dry_run,
    )
    json_text = format_json_report(
        records,
        result,
        input_path=input_path,
        output_path=output_path,
        translated=translated,
        cached=cached,
        skipped=skipped,
        dry_run=dry_run,
    )
    write_text(text_path, text)
    write_text(json_path, json_text)
    return text_path, json_path


def _print_report_paths(text_path: Path, json_path: Path) -> None:
    print(f"QC report: {text_path}")
    print(f"QC JSON:   {json_path}")


def _resolve_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if api_key:
        return api_key

    if sys.stdin.isatty():
        api_key = getpass.getpass("Gemini API key: ").strip()
        if api_key:
            return api_key

    raise TranslationError(
        "Gemini API key not found. Set GEMINI_API_KEY in .env or the environment. "
        "Create a key at https://aistudio.google.com/apikey"
    )


def _print_translation_summary(result: PipelineResult, output_path: Path) -> None:
    from_api = max(0, result.translated_cues - result.cached_cues)
    print(
        f"Translated {result.translated_cues} cues "
        f"({from_api} from API, {result.cached_cues} from cache); "
        f"{result.skipped_cues} skipped."
    )
    print(
        f"API calls: {result.api_calls}; batch splits: {result.splits}."
    )
    print(f"Output: {output_path}")


def _configure_output() -> None:
    """Make Unicode progress and error messages safe on legacy consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


@dataclass
class _FileOutcome:
    status: str  # "translated", "checked", "skipped", or "failed"
    qc_warnings: int = 0


def _process_dry_run(
    *,
    args: argparse.Namespace,
    document: SrtDocument,
    input_path: Path,
    output_path: Path,
    work_dir: Path,
    options: QcOptions,
) -> _FileOutcome:
    records = _source_records(document)
    result = analyze(records, options)
    skipped = sum(1 for cue in document.cues if not visible_text(cue.text).strip())

    print(f"Dry run: {input_path.name}: {len(document.cues)} cues parsed successfully.")
    _print_qc_summary(result)

    if args.report:
        text_path, json_path = _write_reports(
            records=records,
            result=result,
            input_path=input_path,
            output_path=output_path,
            work_dir=work_dir,
            translated=0,
            cached=0,
            skipped=skipped,
            dry_run=True,
        )
        _print_report_paths(text_path, json_path)

    return _FileOutcome("checked", result.issue_count)


def _process_file(
    *,
    args: argparse.Namespace,
    input_path: Path,
    output_path: Path,
    glossary: dict[str, str],
    options: QcOptions,
    translator: GeminiTranslator | None,
) -> _FileOutcome:
    try:
        document = read_srt(input_path, encoding=args.input_encoding)
        work_dir = _resolve_work_dir(input_path, args)

        if args.dry_run:
            return _process_dry_run(
                args=args,
                document=document,
                input_path=input_path,
                output_path=output_path,
                work_dir=work_dir,
                options=options,
            )

        if translator is None:
            raise TranslationError("internal error: translator was not initialized")

        def log(message: str) -> None:
            print(f"[{input_path.name}] {message}", file=sys.stderr, flush=True)

        cache_path = work_dir / "cache.json"
        pipeline = Pipeline(
            translator,
            model=args.model,
            glossary=glossary,
            cache_path=cache_path,
            use_cache=not args.no_cache,
            batch_size=args.batch_size,
            context=args.context,
            retry_delay=0.8,
            max_line=args.max_line,
            max_lines=args.max_lines,
            no_wrap=args.no_wrap,
            verbose=args.verbose,
            log=log,
        )
        result = pipeline.run(document)

        write_srt(document, output_path, encoding="utf-8")
        qc_result = analyze(result.records, options)

        _print_translation_summary(result, output_path)
        _print_qc_summary(qc_result)
        if args.verbose and not args.no_cache:
            print(f"Cache: {cache_path}", file=sys.stderr)

        if args.report:
            text_path, json_path = _write_reports(
                records=result.records,
                result=qc_result,
                input_path=input_path,
                output_path=output_path,
                work_dir=work_dir,
                translated=result.translated_cues,
                cached=result.cached_cues,
                skipped=result.skipped_cues,
                dry_run=False,
            )
            _print_report_paths(text_path, json_path)

        return _FileOutcome("translated", qc_result.issue_count)

    except (SrtError, TranslationError, TagError, OSError, ValueError) as exc:
        print(f"Error: {input_path}: {exc}", file=sys.stderr)
        return _FileOutcome("failed")


def _print_batch_summary(outcomes: list[_FileOutcome]) -> None:
    counts = {
        status: sum(1 for outcome in outcomes if outcome.status == status)
        for status in ("translated", "checked", "skipped", "failed")
    }
    parts = [
        f"{counts[status]} {status}"
        for status in ("translated", "checked", "skipped", "failed")
        if counts[status]
    ]
    print("Done: " + ", ".join(parts) + "." if parts else "Done: nothing to do.")


def _exit_code(outcomes: list[_FileOutcome], args: argparse.Namespace) -> int:
    if any(outcome.status == "failed" for outcome in outcomes):
        return 1
    if args.strict and any(outcome.qc_warnings for outcome in outcomes):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    _load_environment()
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        _validate_args(args)
        inputs = _discover_srt_files(
            args.input,
            recursive=args.recursive,
            output_dir=args.output_dir,
        )
        if not inputs:
            raise TranslationError(f"No .srt files found in {args.input}")

        glossary = _load_glossary(args.glossary)
        options = QcOptions(
            max_line=args.max_line,
            max_lines=args.max_lines,
            max_cps=args.max_cps,
            check_translation=not args.dry_run,
        )
        pairs = [(path, _output_path_for(path, args)) for path in inputs]

        translator: GeminiTranslator | None = None
        needs_translation = not args.dry_run and any(
            args.overwrite or not output_path.exists()
            for _input_path, output_path in pairs
        )
        if needs_translation:
            translator = GeminiTranslator(
                api_key=_resolve_api_key(),
                model=args.model,
                temperature=args.temperature,
                rpm=args.rpm,
                max_retries=args.max_retries,
                log=lambda message: print(message, file=sys.stderr, flush=True),
            )

        outcomes: list[_FileOutcome] = []
        total = len(pairs)
        for index, (input_path, output_path) in enumerate(pairs, start=1):
            prefix = f"[{index}/{total}] " if total > 1 else ""

            try:
                same_file = input_path.resolve() == output_path.resolve()
            except OSError:
                same_file = False
            if same_file:
                print(
                    f"{prefix}Error: output path must be different from input: "
                    f"{input_path}",
                    file=sys.stderr,
                )
                outcomes.append(_FileOutcome("failed"))
                continue

            if not args.dry_run and output_path.exists() and not args.overwrite:
                print(
                    f"{prefix}Skipping {input_path.name}: {output_path} already "
                    "exists (use --overwrite)."
                )
                outcomes.append(_FileOutcome("skipped"))
                continue

            if total > 1:
                print(f"{prefix}{input_path.name} -> {output_path.name}")

            outcomes.append(
                _process_file(
                    args=args,
                    input_path=input_path,
                    output_path=output_path,
                    glossary=glossary,
                    options=options,
                    translator=translator,
                )
            )

        if total > 1:
            _print_batch_summary(outcomes)
        return _exit_code(outcomes, args)

    except (SrtError, TranslationError, TagError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
