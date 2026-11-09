from __future__ import annotations

from pathlib import Path

from srt_translator.cli import _default_output_path, main

FIXTURE = Path(__file__).parent / "fixtures" / "sample.en.srt"


def test_default_output_path() -> None:
    assert _default_output_path(Path("movie.en.srt")) == Path("movie.bg.srt")
    assert _default_output_path(Path("movie.eng.srt")) == Path("movie.bg.srt")
    assert _default_output_path(Path("movie.srt")) == Path("movie.bg.srt")


def test_dry_run_does_not_need_api_key(capsys, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    code = main([str(FIXTURE), "--dry-run", "--no-report"])

    captured = capsys.readouterr()
    assert code == 0
    assert "5 cues parsed successfully" in captured.out
    assert "No API calls made" in captured.out
