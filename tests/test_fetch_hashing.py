from __future__ import annotations

from pathlib import Path

import pytest

from srt_translator.fetch.hashing import movie_hash
from srt_translator.fetch.models import FetchError

CHUNK = 65536


def test_hash_of_zero_filled_file(tmp_path: Path) -> None:
    path = tmp_path / "zeros.bin"
    path.write_bytes(bytes(CHUNK * 2))
    digest, size = movie_hash(path)
    assert size == CHUNK * 2
    assert digest == "0000000000020000"


def test_hash_uses_first_and_last_chunk(tmp_path: Path) -> None:
    data = bytearray(CHUNK * 2)
    data[0] = 1
    data[-8] = 1
    path = tmp_path / "video.mkv"
    path.write_bytes(bytes(data))

    digest, size = movie_hash(path)

    assert size == CHUNK * 2
    assert digest == f"{CHUNK * 2 + 2:016x}"


def test_hash_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "video.mkv"
    path.write_bytes(bytes(range(256)) * 1024)

    assert movie_hash(path) == movie_hash(path)


def test_hash_rejects_tiny_files(tmp_path: Path) -> None:
    path = tmp_path / "small.mkv"
    path.write_bytes(b"not a real video")

    with pytest.raises(FetchError):
        movie_hash(path)


def test_hash_reports_missing_files(tmp_path: Path) -> None:
    with pytest.raises(FetchError):
        movie_hash(tmp_path / "missing.mkv")
