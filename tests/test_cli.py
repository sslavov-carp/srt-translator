from __future__ import annotations

import shutil
from pathlib import Path

from srt_translator import cli
from srt_translator.cli import _default_output_path, main

FIXTURE = Path(__file__).parent / "fixtures" / "sample.en.srt"


class FakeTranslator:
    def __init__(self, **kwargs) -> None:
        self.model = kwargs.get("model", "fake-model")

    def translate(self, items, context_before, context_after, glossary):
        return {item.id: "БГ " + item.text for item in items}


def _make_folder(tmp_path: Path, *names: str) -> Path:
    folder = tmp_path / "subs"
    folder.mkdir()
    for name in names:
        shutil.copyfile(FIXTURE, folder / name)
    return folder


def test_default_output_path() -> None:
    assert _default_output_path(Path("movie.en.srt")) == Path("movie.bg.srt")
    assert _default_output_path(Path("movie.eng.srt")) == Path("movie.bg.srt")
    assert _default_output_path(Path("movie.srt")) == Path("movie.bg.srt")


def test_dry_run_does_not_need_api_key(capsys, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    code = main([str(FIXTURE), "--dry-run"])

    captured = capsys.readouterr()
    assert code == 0
    assert "5 cues parsed successfully" in captured.out


def test_folder_dry_run_ignores_targets_and_non_srt(
    tmp_path: Path, capsys
) -> None:
    folder = _make_folder(tmp_path, "a.en.srt", "b.en.srt", "old.bg.srt")
    (folder / "notes.txt").write_text("not subtitles", encoding="utf-8")

    code = main([str(folder), "--dry-run"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.count("Dry run:") == 2
    assert "a.en.srt" in captured.out
    assert "b.en.srt" in captured.out
    assert "old.bg.srt" not in captured.out
    assert "Done: 2 checked." in captured.out


def test_folder_dry_run_recursive(tmp_path: Path, capsys) -> None:
    folder = _make_folder(tmp_path, "a.en.srt")
    nested = folder / "season 1"
    nested.mkdir()
    shutil.copyfile(FIXTURE, nested / "c.en.srt")

    code = main([str(folder), "--dry-run", "--recursive"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.count("Dry run:") == 2
    assert "Done: 2 checked." in captured.out


def test_reports_go_to_work_dir_not_beside_source(
    tmp_path: Path, capsys
) -> None:
    folder = _make_folder(tmp_path, "movie.en.srt")
    work = tmp_path / "work"

    code = main(
        [str(folder), "--dry-run", "--report", "--work-dir", str(work)]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert sorted(path.name for path in work.rglob("qc.*")) == ["qc.json", "qc.txt"]
    assert not list(folder.glob("*.qc.*"))
    assert "QC report:" in captured.out


def test_folder_batch_translates_with_fake_translator(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    folder = _make_folder(tmp_path, "a.en.srt", "b.en.srt", "old.bg.srt")
    output = tmp_path / "out"
    work = tmp_path / "work"

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(cli, "GeminiTranslator", FakeTranslator)

    code = main(
        [
            str(folder),
            "--output-dir",
            str(output),
            "--work-dir",
            str(work),
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert sorted(path.name for path in output.iterdir()) == [
        "a.bg.srt",
        "b.bg.srt",
    ]
    assert (output / "a.bg.srt").read_text(encoding="utf-8").startswith("1\n")
    assert not list(folder.glob("*.cache.json"))
    assert not list(folder.glob("*.qc.*"))
    assert sorted(path.name for path in work.rglob("cache.json")) == [
        "cache.json",
        "cache.json",
    ]
    assert "Done: 2 translated." in captured.out


def test_batch_skips_existing_output_unless_overwrite(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    folder = _make_folder(tmp_path, "a.en.srt", "b.en.srt")
    output = tmp_path / "out"
    output.mkdir()
    existing = output / "a.bg.srt"
    existing.write_text("keep me", encoding="utf-8")

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(cli, "GeminiTranslator", FakeTranslator)

    code = main(
        [
            str(folder),
            "--output-dir",
            str(output),
            "--work-dir",
            str(tmp_path / "work"),
            "--no-cache",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert existing.read_text(encoding="utf-8") == "keep me"
    assert (output / "b.bg.srt").exists()
    assert "Skipping a.en.srt" in captured.out
    assert "Done: 1 translated, 1 skipped." in captured.out


def test_folder_without_srt_is_an_error(tmp_path: Path, capsys) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()

    code = main([str(folder), "--dry-run"])

    captured = capsys.readouterr()
    assert code == 1
    assert "No .srt files found" in captured.err


def test_output_flag_is_rejected_for_folder(tmp_path: Path, capsys) -> None:
    folder = _make_folder(tmp_path, "a.en.srt")

    code = main([str(folder), "-o", str(tmp_path / "out.srt")])

    captured = capsys.readouterr()
    assert code == 1
    assert "--output only works with a single input file" in captured.err
