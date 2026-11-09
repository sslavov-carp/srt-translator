from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .errors import TranslationError
from .gemini import GeminiTranslator
from .models import CueRecord
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
    return input_path.with_name(stem + ".bg.srt")


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
            "Translate an English SRT subtitle file into Bulgarian while "
            "preserving every index and timing line."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", type=Path, help="input English .srt file")
    parser.add_argument("-o", "--output", type=Path, help="output Bulgarian .srt file")
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
        "--no-report",
        action="store_true",
        help="do not write .qc.txt and .qc.json reports",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return exit code 1 when QC warnings are present",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and inspect the file without calling the API or writing output",
    )
    parser.add_argument(
        "--input-encoding",
        help="force an input encoding (for example utf-8-sig or cp1252)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite the output file if it already exists",
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
        raise TranslationError(f"Input file does not exist: {args.input}")
    if not args.input.is_file():
        raise TranslationError(f"Input path is not a file: {args.input}")
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


def _report_paths(output_path: Path) -> tuple[Path, Path]:
    return (
        output_path.with_name(output_path.name + ".qc.txt"),
        output_path.with_name(output_path.name + ".qc.json"),
    )


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
    translated: int,
    cached: int,
    skipped: int,
    dry_run: bool,
) -> tuple[Path, Path]:
    text_path, json_path = _report_paths(output_path)
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


def _run_dry(
    *,
    args: argparse.Namespace,
    document: SrtDocument,
    output_path: Path,
    options: QcOptions,
) -> int:
    records = _source_records(document)
    result = analyze(records, options)
    skipped = sum(1 for cue in document.cues if not visible_text(cue.text).strip())

    print(f"Dry run: {len(document.cues)} cues parsed successfully.")
    print("No API calls made and no subtitle output written.")
    _print_qc_summary(result)

    if not args.no_report:
        text_path, json_path = _write_reports(
            records=records,
            result=result,
            input_path=args.input,
            output_path=output_path,
            translated=0,
            cached=0,
            skipped=skipped,
            dry_run=True,
        )
        print(f"QC report: {text_path}")
        print(f"QC JSON:   {json_path}")

    return 1 if args.strict and result.issue_count else 0


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


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    _load_environment()
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        _validate_args(args)
        output_path = args.output or _default_output_path(args.input)
        if output_path.resolve() == args.input.resolve():
            raise TranslationError("Output path must be different from the input path")
        if (
            not args.dry_run
            and output_path.exists()
            and not args.overwrite
        ):
            raise TranslationError(
                f"Output already exists: {output_path}. Use --overwrite to replace it."
            )

        document = read_srt(args.input, encoding=args.input_encoding)
        glossary = _load_glossary(args.glossary)
        options = QcOptions(
            max_line=args.max_line,
            max_lines=args.max_lines,
            max_cps=args.max_cps,
            check_translation=not args.dry_run,
        )

        if args.dry_run:
            return _run_dry(
                args=args,
                document=document,
                output_path=output_path,
                options=options,
            )

        api_key = _resolve_api_key()
        log = lambda message: print(message, file=sys.stderr, flush=True)

        translator = GeminiTranslator(
            api_key=api_key,
            model=args.model,
            temperature=args.temperature,
            rpm=args.rpm,
            max_retries=args.max_retries,
            log=log,
        )
        pipeline = Pipeline(
            translator,
            model=args.model,
            glossary=glossary,
            cache_path=output_path.with_name(output_path.name + ".cache.json"),
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

        if not args.no_report:
            text_path, json_path = _write_reports(
                records=result.records,
                result=qc_result,
                input_path=args.input,
                output_path=output_path,
                translated=result.translated_cues,
                cached=result.cached_cues,
                skipped=result.skipped_cues,
                dry_run=False,
            )
            print(f"QC report: {text_path}")
            print(f"QC JSON:   {json_path}")

        return 1 if args.strict and qc_result.issue_count else 0

    except (SrtError, TranslationError, TagError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
