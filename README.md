# Subtitle translator and fetcher

Two command-line tools for Bulgarian subtitles:

- **`translate_srt.py`** translates English `.srt` subtitles into Bulgarian
  while leaving every cue index and timing line untouched.
- **`fetch_subs.py`** finds and downloads subtitles for a movie or episode,
  preferring Bulgarian and falling back to English.

The translator uses the Gemini API with structured JSON output, so the model
only sees subtitle text and never timestamps.

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
- A Gemini API key from <https://aistudio.google.com/apikey> (translating)
- An OpenSubtitles API key (finding subtitles)
- Optional: a TMDB API key for more accurate title matching

`ffprobe` (from FFmpeg) is optional. When it is on `PATH`, fetched subtitles are
checked against the video duration.

This project was set up with Python 3.13.

## Setup

From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Put your keys in `.env`:

```dotenv
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_RPM=10
OPENSUBTITLES_API_KEY=your-opensubtitles-api-key
```

The OpenSubtitles key is only needed by `fetch_subs.py`. See
[Finding subtitles](#finding-subtitles) for the optional account and TMDB keys.

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

## Finding subtitles

`fetch_subs.py` identifies a movie or episode, searches OpenSubtitles, and
downloads the best Bulgarian subtitle. If nothing usable is found, it tries
English. It does not translate yet: run `translate_srt.py` on the English
fallback if you need Bulgarian.

```powershell
.\fetch.cmd "D:\Movies\Inception.2010.1080p.BluRay.x264-GRP.mkv"
.\fetch.cmd "Breaking Bad S02E05"
.\fetch.cmd "Inception 2010"
.\fetch.cmd tt1375666
.\fetch.cmd tmdb:27205
```

The input can be:

- a video file: `guessit` parses the filename, and the video's OpenSubtitles
  hash and duration are used when available
- free text such as `Inception 2010` or `Show Name S02E05`
- an IMDb id such as `tt1375666`
- a TMDB id such as `tmdb:27205`

### OpenSubtitles setup

Create an API key at
<https://www.opensubtitles.com/en/users/settings/api> and add it to `.env`:

```dotenv
OPENSUBTITLES_API_KEY=your-api-key
```

The API key is required for every request. To raise the daily download limit
from 5 (anonymous) to 20, also add your OpenSubtitles account:

```dotenv
OPENSUBTITLES_USERNAME=your-username
OPENSUBTITLES_PASSWORD=your-password
```

Optional defaults:

```dotenv
SUBS_PREFERRED_LANG=bg
SUBS_FALLBACK_LANG=en
SUBS_MAX_DOWNLOADS=20
```

TMDB is optional. Add `TMDB_API_KEY` to resolve titles to IMDb/TMDB ids and
improve search accuracy; without it the script searches by title text and
filename heuristics.

### What it does

1. Normalizes the input into one movie or episode.
2. Searches every configured source for Bulgarian, then for the fallback
   language. Searching does not spend download quota.
3. Ranks every candidate, rejecting wrong seasons or episodes, multi-CD sets,
   non-SRT formats, foreign-parts-only uploads, and machine translations
   (unless `--allow-machine` is given).
4. Downloads only the best candidate, validates it after download, and tries
   the next candidate if it is unusable.
5. Saves the subtitle next to the video, or in the current directory for
   title/ID input.

Ranking favors an exact file-hash match, then release tags (resolution,
source, codec, cut, release group), then trusted uploaders, rating, download
count, and frame rate. Season and episode mismatches are rejected outright.
Non-hearing-impaired subtitles score higher.

### Validation

Every download is decoded (UTF-8, UTF-16, or legacy Windows-1251 for
Bulgarian), parsed as SRT, and checked for cues and timestamps. Ad and
watermark lines near the start and end are removed; pass `--no-clean` to keep
them. When `ffprobe` is available, the subtitle length is compared with the
video and a mismatch is reported; `--strict-duration` turns that into a
rejection. Without `ffprobe` the check is skipped.

### Fetch output files

- `Movie.Name.2010.bg.srt` — preferred language
- `Movie.Name.2010.en.srt` — fallback language
- `Show.Name.S02E05.bg.srt` — episodes

Existing files are skipped unless you pass `--overwrite`.

### Cache, quota, and log

Fetched files, the quota counter (`quota.json`), and `fetch_log.json` are
stored in the per-user cache directory, never next to the media:

- Windows: `%LOCALAPPDATA%\srt-translator\fetch\`
- macOS: `~/Library/Caches/srt-translator/fetch/`
- Linux: `~/.cache/srt-translator/fetch/`

Downloads are cached, so retrying the same run does not spend quota twice.
OpenSubtitles charges a download on `POST /download`; the local counter tracks
the daily limit and reconciles with the server's remaining count. If the API
key has a larger allowance than the built-in default, the server value raises
the limit. An explicit `--max-downloads` or `SUBS_MAX_DOWNLOADS` stays a hard
cap. Use `--work-dir PATH` to move the cache, quota file, and log, and
`--no-cache` to disable reuse.

### Fetch options

| Option | Default | Meaning |
| --- | --- | --- |
| `--lang` | `bg` | Preferred subtitle language |
| `--fallback-lang` | `en` | Language tried when the preferred one fails |
| `--imdb` | auto | Explicit IMDb id (also `tt…` as input) |
| `--tmdb` | auto | Explicit TMDB id (also `tmdb:…` as input) |
| `--season`, `--episode` | auto | Episode overrides; use together |
| `--year` | auto | Release year override |
| `--dry-run` | off | Search and rank without downloading |
| `--overwrite` | off | Replace an existing subtitle file |
| `--min-score` | `50` | Reject candidates scoring below this |
| `--max-attempts` | `2` | Try this many candidates if validation fails |
| `--max-downloads` | `20` or `5` | Daily download limit |
| `--allow-machine` | off | Allow machine/AI-translated subtitles |
| `--no-hash` | off | Skip the OpenSubtitles moviehash |
| `--no-clean` | off | Keep ad and watermark lines |
| `--strict-duration` | off | Reject duration mismatches |
| `--no-cache` | off | Disable the download cache |
| `--sources` | `opensubtitles` | Comma-separated source adapters |
| `--work-dir` | OS cache dir | Where cache, quota, and log are stored |
| `--json` | off | Print a machine-readable result |
| `--verbose` | off | Show score details and cache paths |

Run `fetch_subs.py --help` for the complete list.

### Limitations

- Only OpenSubtitles is supported for now. Podnapisi, SubDL, and Bulgarian
  community sites are planned as additional adapters.
- It does not translate. The English fallback is left in place for
  `translate_srt.py`.
- Without an OpenSubtitles account the limit is 5 downloads per day.
- Batch and folder mode are not implemented yet.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The tests use a fake translator and fake or mocked HTTP sources, so they do
not call the Gemini, TMDB, or OpenSubtitles APIs and do not need real keys.

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

**`OPENSUBTITLES_API_KEY is not set`**  
Create an API key at <https://www.opensubtitles.com/en/users/settings/api> and
add it to `.env`. The key is required even for anonymous searches.

**`OpenSubtitles login failed, continuing with anonymous limits`**  
The username or password is wrong, or the login was rate-limited. Fetching
still works, but the daily limit drops to 5. Check the credentials in `.env`.

**`daily download limit reached`**  
Wait for the reset shown in the message, sign in with an OpenSubtitles account
for 20 downloads per day, or raise `--max-downloads` if your account allows
more. Use `--dry-run` to inspect candidates without spending quota.

**`no usable bg candidates` / `no usable subtitles`**  
Everything was filtered out. Run with `--dry-run --verbose` to see rejection
reasons. Common causes are a wrong season or episode (use `--season` and
`--episode`), only hearing-impaired or machine-translated subtitles (try
`--allow-machine`), or a title TMDB matched incorrectly (pass `--imdb` or
`--tmdb` explicitly). The fallback language is tried automatically.

**`ffprobe not available; skipping the duration check`**  
Optional. Install FFmpeg and add `ffprobe` to `PATH` to enable duration
validation.
