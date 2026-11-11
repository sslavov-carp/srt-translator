# srt-translator — Simple Manual

A practical guide to the two tools in this project:

- **`fetch_subs.py`** finds and downloads subtitles (Bulgarian first, English
  as fallback).
- **`translate_srt.py`** translates an English `.srt` file into Bulgarian.

The examples use Windows PowerShell. macOS and Linux equivalents are shown
where they differ. For every flag and implementation detail, see
[README.md](README.md).

---

## 1. Install and configure

### Requirements

- Python 3.11 or newer
- A Gemini API key for translation: <https://aistudio.google.com/apikey>
- An OpenSubtitles API key for fetching:
  <https://www.opensubtitles.com/en/users/settings/api>
- Optional: TMDB API key for better title matching
- Optional: `ffprobe` (FFmpeg) on `PATH` for duration checks

### Install

Open PowerShell in the project folder and run:

```powershell
cd "C:\Users\Stanislav Slavov\Documents\opencode_projs\srt-translator"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

On macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env
```

### Add your keys

Open `.env` in a text editor and fill in the values:

```dotenv
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_RPM=10

OPENSUBTITLES_API_KEY=your-opensubtitles-api-key
```

`.env` is gitignored. Never commit it or paste its contents into a ticket.

### Optional settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `OPENSUBTITLES_USERNAME` / `OPENSUBTITLES_PASSWORD` | empty | Sign in to OpenSubtitles for a higher daily download limit (20 instead of 5) |
| `TMDB_API_KEY` | empty | Resolve titles to IMDb/TMDB IDs for more accurate searches |
| `SUBS_PREFERRED_LANG` | `bg` | First language to look for |
| `SUBS_FALLBACK_LANG` | `en` | Language tried if the preferred one fails |
| `SUBS_MAX_DOWNLOADS` | `20` | Hard cap on downloads per day |
| `GEMINI_RPM` | `10` | Requests per minute; `0` disables pacing |

### Verify the setup

```powershell
.\fetch.cmd --version
.\run.cmd --version
```

Both commands print the version. The scripts read `.env` from the project
folder, so you can run them from any directory.

> `.fetch.cmd` and `.\run.cmd` are Windows wrappers around the `.venv` Python.
> On macOS/Linux, call `python fetch_subs.py …` and
> `python translate_srt.py …` instead.

---

## 2. Fetch subtitles

Use `fetch_subs.py` when you want a subtitle without translating anything.

### First a dry run (searches and ranks, spends no download quota)

Always start here. It shows what would be downloaded without using a download:

```powershell
cd "C:\Users\Stanislav Slavov\Documents\opencode_projs\srt-translator"
.\fetch.cmd "Severance S01E05" --dry-run
```

Add `--verbose` to see why candidates were ranked or rejected:

```powershell
.\fetch.cmd "Severance S01E05" --dry-run --verbose
```

Typical output:

```text
Media:    Severance S01E05
Search:   bg
  bg: 4 usable candidate(s) of 7
     1.    92 pts  Severance.S01E05.1080p.WEB-DL...
        +45 downloads 1200, +20 not hearing impaired, +10 SRT format
Would download: Severance.S01E05.1080p.WEB-DL... [opensubtitles 123456]
  score: 92
  quota: 99 download(s) left today (limit 100)
```

Only the real download spends quota, and only once.

### Download for real

```powershell
.\fetch.cmd "Severance S01E05"
```

The subtitle is saved in the current directory:

```text
Severance.S01E05.bg.srt
```

If no usable Bulgarian subtitle exists, the English fallback is saved instead:

```text
Severance.S01E05.en.srt
```

Run `translate_srt.py` on that file to get Bulgarian (see section 3).

### Best accuracy: pass the video file

When you have the video, pass its path. The tool then uses the filename,
OpenSubtitles hash, and duration to find the right subtitle:

```powershell
.\fetch.cmd "D:\Shows\Severance\Severance.S01E05.1080p.WEB-DL.mkv"
```

The subtitle is saved next to the video file with the same base name.

### Input formats

`fetch_subs.py` accepts any of these:

| Input | Example |
| --- | --- |
| Video file | `"D:\Movies\Inception.2010.1080p.mkv"` |
| Title and year | `"Inception 2010"` |
| Show, season, episode | `"Breaking Bad S02E05"` |
| Title plus explicit flags | `"Severance" --season 1 --episode 5` |
| IMDb ID | `tt1375666` |
| TMDB ID | `tmdb:27205` |

### When the match is wrong

"Severance" is both a series and a 2006 film, so a text search can pick the
wrong title. Use an explicit ID:

```powershell
.\fetch.cmd "Severance S01E05" --tmdb 95396
```

Or use season/episode flags:

```powershell
.\fetch.cmd "Severance" --season 1 --episode 5
```

### Common fetch flags

| Flag | What it does |
| --- | --- |
| `--dry-run` | Search and rank without downloading |
| `--verbose` | Show scores and rejection reasons |
| `--lang bg` | Preferred language (default `bg`) |
| `--fallback-lang en` | Fallback language (default `en`) |
| `--tmdb 95396` / `--imdb tt1375666` | Force the title to a specific ID |
| `--season 1 --episode 5` | Override season and episode |
| `--year 2010` | Override the release year |
| `--overwrite` | Replace an existing subtitle file |
| `--min-score 50` | Minimum score for a candidate |
| `--max-attempts 2` | Try this many candidates if validation fails |
| `--allow-machine` | Accept machine/AI-translated subtitles |
| `--strict-duration` | Reject subtitles whose length does not match the video |
| `--no-hash` | Skip the OpenSubtitles file hash |
| `--no-clean` | Keep ad and watermark lines |
| `--no-cache` | Ignore cached downloads |
| `--max-downloads N` | Hard daily download cap |
| `--json` | Machine-readable output |
| `--work-dir PATH` | Store cache, quota, and log in `PATH` |

### Where files go

- Subtitle output: next to the video, or in the current directory for
  title/ID input.
- Cache, quota counter, and log:
  `%LOCALAPPDATA%\srt-translator\fetch\`
  (`~/Library/Caches/srt-translator/fetch/` on macOS,
  `~/.cache/srt-translator/fetch/` on Linux).
- Downloaded files are cached. Re-running the same search does not spend quota
  again.
- `quota.json` tracks the day's downloads. `fetch_log.json` records each run.

### About the daily limit

- Anonymous use allows 5 downloads per day.
- A signed-in OpenSubtitles account allows 20.
- If your API key reports a larger server-side allowance, that value is used.
- `--max-downloads` or `SUBS_MAX_DOWNLOADS` is always a hard cap.
- Use `--dry-run` to inspect candidates without spending anything.

---

## 3. Translate subtitles

Use `translate_srt.py` to turn an English subtitle into Bulgarian. The English
file is never modified; a new `*.bg.srt` file is written.

### Translate one file

```powershell
cd "C:\Users\Stanislav Slavov\Documents\opencode_projs\srt-translator"
.\run.cmd "D:\Subs\Severance.S01E05.en.srt"
```

Output, written next to the input:

```text
Severance.S01E05.bg.srt
```

The input name does not have to end in `.en.srt`. `Movie.srt` also becomes
`Movie.bg.srt`; common English suffixes are stripped first.

### Choose the output path

```powershell
.\run.cmd "D:\Subs\Severance.S01E05.en.srt" -o "D:\Subs\Severance.S01E05.bg.srt"
```

`-o` works only for a single input file.

### Translate a whole folder

```powershell
.\run.cmd "D:\Subs"
```

- Every `.srt` in that folder is translated, one at a time.
- Files already named `*.bg.srt` are skipped.
- Outputs are written beside their source files.
- A broken file does not stop the batch; a summary is printed at the end.

### Collect everything in one folder

```powershell
.\run.cmd "D:\Subs" --output-dir "D:\Subs\Bulgarian"
```

### Include subfolders

```powershell
.\run.cmd "D:\Subs" --recursive --output-dir "D:\Subs\Bulgarian"
```

Existing outputs are skipped. Add `--overwrite` to replace them.

### Check a file before spending API calls

```powershell
.\run.cmd "D:\Subs\Severance.S01E05.en.srt" --dry-run
```

This parses the file, checks structure and line lengths, and writes nothing.
It does not call Gemini and does not produce a translation.

### Use a glossary

Keep names and terms consistent with a small JSON file:

```json
{
  "Mark S.": "Марк С.",
  "Lumon": "Лумон",
  "Severance": "Разделяне"
}
```

```powershell
.\run.cmd "D:\Subs\Severance.S01E05.en.srt" --glossary glossary.json
```

Only glossary entries that appear in the current batch are sent, so requests
stay small. A starting example is in `glossary.example.json`.

### Quality checks

After translating, the tool reports cues that:

- have more than 2 lines, or lines longer than 42 characters
- exceed 17 characters per second
- are empty or contain no Cyrillic
- have formatting tags that differ from the source
- have invalid or zero-length timings

These are warnings. Use `--strict` to make warnings produce exit code 1, which
is useful in scripts. Long text is never truncated and timings are never
changed.

Write detailed reports next to the resume cache:

```powershell
.\run.cmd "D:\Subs\Severance.S01E05.en.srt" --report
```

### Resume an interrupted run

Successful batches are cached automatically. If the run stops because of a
rate limit, network error, or Ctrl+C, run the same command again and completed
batches are reused.

```powershell
.\run.cmd "D:\Subs" --verbose
```

`--verbose` shows cache hits. The cache lives in
`%LOCALAPPDATA%\srt-translator\` on Windows. Use `--no-cache` to start fresh or
`--work-dir PATH` to move the cache and reports.

### Common translator flags

| Flag | Default | What it does |
| --- | --- | --- |
| `-o`, `--output` | auto | Output file for a single input |
| `--output-dir` | beside input | Folder for translated files |
| `--recursive` | off | Include subfolders in folder mode |
| `--glossary` | none | Glossary JSON file |
| `--dry-run` | off | Parse and inspect without calling the API |
| `--report` | off | Write `qc.txt` and `qc.json` |
| `--strict` | off | Exit code 1 when QC warns |
| `--overwrite` | off | Replace existing output files |
| `--no-cache` | off | Disable the resume cache |
| `--work-dir PATH` | OS cache dir | Where cache and reports are stored |
| `--verbose` | off | Show cache hits and extra progress |
| `--model` | `gemini-3.5-flash-lite` | Gemini model ID |
| `--rpm 5` | `10` | Slower pacing for free-tier limits |
| `--batch-size 40` | `40` | Cues per API request |
| `--max-line 42` | `42` | Characters per subtitle line |
| `--max-lines 2` | `2` | Lines per cue |
| `--max-cps 17` | `17` | Characters per second before a warning |
| `--no-wrap` | off | Keep the translator's line breaks |
| `--input-encoding` | auto | Force an input encoding, e.g. `cp1252` |

### Translator notes

- Output is always UTF-8, so Cyrillic displays correctly.
- Index and timing lines are copied unchanged.
- Formatting such as `<i>`, `<b>`, and `{\an8}` is preserved.
- The model never sees timestamps, which protects cue alignment.
- Free-tier daily quotas reset at midnight Pacific time. Cached work survives.
- Free-tier content may be used by Google to improve its products. Do not send
  confidential or unreleased material.
- If quality is insufficient, try `--model gemini-3.8-flash`.

### Complete workflow

Fetch, then translate the English fallback if needed:

```powershell
.\fetch.cmd "Severance S01E05"
.\run.cmd "Severance.S01E05.en.srt"
```

Or, when a Bulgarian subtitle already exists, skip the translator entirely.

---

## 4. Troubleshooting

| Message or problem | Fix |
| --- | --- |
| `Gemini API key not found` | Create `.env` from `.env.example` and set `GEMINI_API_KEY` |
| `OPENSUBTITLES_API_KEY is not set` | Add your OpenSubtitles API key to `.env` |
| `429 RESOURCE_EXHAUSTED` | Lower `--rpm` (for example `--rpm 5`) and rerun; cached batches are reused |
| `daily download limit reached` | Wait for the reset, sign in to OpenSubtitles, or raise the cap |
| `no usable bg candidates` / `no usable subtitles` | Run with `--dry-run --verbose`; check season/episode, try `--allow-machine`, or pass `--tmdb`/`--imdb` |
| Wrong movie or show matched | Pass `--tmdb`/`--imdb`, or use the video file as input |
| `Output already exists` | Add `--overwrite` or choose another output path |
| `No .srt files found` | Check the folder path, or add `--recursive` |
| `block N has no valid timing line` | The input is not valid SRT; repair it in Subtitle Edit |
| Cyrillic looks wrong in a player | The file is UTF-8; switch the player's subtitle encoding to UTF-8 |
| OpenSubtitles login failed | Check `OPENSUBTITLES_USERNAME` / `OPENSUBTITLES_PASSWORD`; fetching still works anonymously |
| `ffprobe not available` | Optional; install FFmpeg and add `ffprobe` to `PATH` to enable duration checks |

For configuration details, all flags, and design notes, see
[README.md](README.md).
