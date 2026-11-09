# English → Bulgarian SRT translator

A Python command-line tool that translates English `.srt` subtitles into
Bulgarian while leaving every cue index and timing line untouched.

It uses the Gemini API with structured JSON output, so the translator only sees
subtitle text and never timestamps.

## What it does

- Parses the SRT file and copies index/timing lines verbatim.
- Sends text in batches of 30–50 cues, with surrounding cues as context.
- Requires exactly one translation per cue, matched by internal IDs.
- Protects formatting tags such as `<i>`, `<b>`, `<font …>`, `{\an8}`, and
  `{\i1}` with placeholders, then validates and restores them.
- Retries malformed responses and splits a batch in half if alignment keeps
  failing.
- Reflows text to at most 2 lines of about 42 characters, without changing
  timings.
- Reports cues that exceed line-length or characters-per-second guidelines.
- Caches successful translations so an interrupted run can resume.
- Writes UTF-8 output, so Cyrillic displays correctly.

## Requirements

- Windows, macOS, or Linux
- Python 3.11 or newer
- A Gemini API key from <https://aistudio.google.com/apikey>

This project was set up with Python 3.13.

## Setup

From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Put your real key in `.env`:

```dotenv
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_RPM=10
```

Do not commit `.env`.

## Usage

```powershell
.\.venv\Scripts\python.exe translate_srt.py input.en.srt
```

By default, `input.en.srt` becomes `input.bg.srt`.

With a glossary and an explicit output path:

```powershell
.\.venv\Scripts\python.exe translate_srt.py input.en.srt `
  -o output.bg.srt `
  --glossary glossary.json
```

Or use the wrapper:

```powershell
.\run.cmd input.en.srt -o input.bg.srt
```

The first time, the script asks for the API key if it is not in `.env`.

### Translating a whole folder

Pass a folder instead of a file to translate every `.srt` in it, one by one:

```powershell
.\run.cmd "D:\Subs"
```

- Files already named `*.bg.srt` are ignored, so finished translations are
  never fed back in.
- Outputs are written next to each source file by default
  (`movie.en.srt` → `movie.bg.srt`).
- Use `--output-dir` to collect all outputs in one folder:

```powershell
.\run.cmd "D:\Subs" --output-dir "D:\Subs\Bulgarian"
```

- Add `--recursive` to include subfolders:

```powershell
.\run.cmd "D:\Subs" --recursive --output-dir "D:\Subs\Bulgarian"
```

- Existing outputs are skipped unless you pass `--overwrite`.
- One broken file does not stop the batch; a summary is printed at the end.

### Dry run

Check parsing, line lengths, and timing problems without calling the API:

```powershell
.\.venv\Scripts\python.exe translate_srt.py input.en.srt --dry-run
```

No subtitle output is written in dry-run mode.

## Common options

| Option | Default | Meaning |
| --- | --- | --- |
| `-o`, `--output` | auto | Output file (single input only) |
| `--output-dir` | none | Folder for translated files (folder mode) |
| `--recursive` | off | Include `.srt` files in subfolders |
| `--model` | `gemini-3.5-flash-lite` | Gemini model ID |
| `--batch-size` | `40` | Cues per API request |
| `--context` | `2` | Previous/next cues supplied as context |
| `--rpm` | `10` | Request pacing; `0` disables it |
| `--max-line` | `42` | Maximum characters per subtitle line |
| `--max-lines` | `2` | Maximum lines per cue |
| `--max-cps` | `17` | Maximum characters per second |
| `--glossary` | none | JSON object of preferred terms |
| `--no-wrap` | off | Keep translator line breaks |
| `--no-cache` | off | Disable resume cache |
| `--report` | off | Write detailed QC reports |
| `--work-dir` | OS cache dir | Where cache and QC reports are stored |
| `--strict` | off | Exit with code 1 if QC warns |
| `--overwrite` | off | Replace existing output files |
| `--verbose` | off | Show cache hits and extra diagnostics |

Run `translate_srt.py --help` for the complete list.

## Glossary

`glossary.json` is a simple JSON object:

```json
{
  "Sarah Connor": "Сара Конър",
  "Terminator": "Терминатор",
  "Skynet": "Скайнет"
}
```

Only glossary entries that occur in the current batch or its context are
included in the prompt, keeping requests small.

## Output files

By default, the only file written next to your subtitles is the translation:

- `movie.bg.srt` — translated subtitles

The resume cache and optional QC reports are stored outside the subtitle
folder, in your per-user cache directory:

- Windows: `%LOCALAPPDATA%\srt-translator\`
- macOS: `~/Library/Caches/srt-translator/`
- Linux: `~/.cache/srt-translator/`

Each input gets a subfolder such as `movie.en-3f9c1a2b/` containing
`cache.json`. Pass `--report` to also write `qc.txt` and `qc.json` there, and
use `--work-dir PATH` to choose a different location. The cache and reports
are never written beside the translated `.srt` unless you point `--work-dir`
at that folder yourself.

Disable the cache with `--no-cache`.

## Quality checks

The QC report flags:

- more than 2 lines in a cue
- lines longer than 42 characters
- more than 17 characters per second
- empty translations
- translations with no Cyrillic characters
- formatting tags that differ from the source
- invalid or zero-length cue durations

These are warnings by default. Use `--strict` to make any warning produce a
non-zero exit code. Detailed reports are written only with `--report`; the
one-line summary is always printed.

Automatic wrapping does not truncate text and never changes timings. If a
translation is too long even for two full lines, it stays two lines and is
listed in the QC report for manual review.

## Free-tier notes

- The default model, `gemini-3.5-flash-lite`, is available on the Gemini free
  tier and is a good fit for translation.
- Free-tier rate limits vary by model and project. The script paces requests
  with `--rpm` and backs off on `429` errors.
- Free-tier content may be used by Google to improve its products. Do not use
  the free tier for confidential or unreleased material.
- Daily quotas reset at midnight Pacific time. Cached work is preserved across
  runs.
- If free-tier quality is not sufficient, try
  `--model gemini-3.8-flash`.

## How alignment is protected

1. The source SRT is parsed into cues without sending timing lines to the model.
2. Each cue gets an internal ID and a tag-protected text string.
3. The model must return JSON with exactly those IDs.
4. Missing, extra, duplicate, empty, or tag-damaged entries are rejected.
5. A failed batch is retried, then split in half until individual cues can be
   translated or the script fails with a clear error.
6. The translated text is written back into the original cue objects, and the
   timing lines are serialized as they were read.

The output file is written atomically only after translation succeeds.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The tests use a fake translator, so they do not call the Gemini API or require
an API key.

## Troubleshooting

**`Gemini API key not found`**  
Create `.env` from `.env.example` and set `GEMINI_API_KEY`.

**`429 RESOURCE_EXHAUSTED`**  
Reduce `--rpm` (for example `--rpm 5`) and rerun. Completed batches are cached.

**`Output already exists`**  
Run with `--overwrite`, or choose a different `-o`/`--output-dir` path. In
folder mode the file is skipped instead, unless `--overwrite` is given.

**`No .srt files found`**  
The folder contains no `.srt` files at the top level. Check the path, or add
`--recursive` to include subfolders.

**`block N has no valid timing line`**  
The input is not a standard SRT file. Open it in Subtitle Edit or a text editor
and check the cue structure.

**Cyrillic looks wrong in another program**  
The script writes UTF-8. Make sure the player or editor is not forcing a legacy
encoding.
