from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from srt_translator.paths import cache_root, work_dir_for


def test_work_dir_is_stable_and_distinct(tmp_path: Path) -> None:
    one = tmp_path / "one" / "movie.en.srt"
    two = tmp_path / "two" / "movie.en.srt"

    assert work_dir_for(one, tmp_path / "work") == work_dir_for(one, tmp_path / "work")
    assert work_dir_for(one, tmp_path / "work") != work_dir_for(two, tmp_path / "work")
    assert work_dir_for(one, tmp_path / "work").name.startswith("movie.en-")


def test_cache_root_uses_localappdata_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "nt":
        pytest.skip("Windows-specific cache location")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert cache_root() == tmp_path / "srt-translator"


def test_cache_root_uses_xdg_on_unix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name == "nt" or sys.platform == "darwin":
        pytest.skip("Unix-specific cache location")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cache_root() == tmp_path / "srt-translator"
