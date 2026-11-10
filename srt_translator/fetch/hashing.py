from __future__ import annotations

import struct
from pathlib import Path

from .models import FetchError

CHUNK_SIZE = 65536
MIN_FILE_SIZE = CHUNK_SIZE * 2


def movie_hash(path: Path) -> tuple[str, int]:
    """Return the OpenSubtitles moviehash and the file size in bytes.

    The hash is the file size plus the sum of every 64-bit little-endian word
    in the first and last 64 KiB of the file, printed as 16 hex digits.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise FetchError(f"Could not read video file {path}: {exc}") from exc

    if size < MIN_FILE_SIZE:
        raise FetchError(
            f"{path.name} is too small for a moviehash "
            f"({size} bytes; at least {MIN_FILE_SIZE} required)"
        )

    checksum = 0
    try:
        with path.open("rb") as handle:
            for offset in (0, size - CHUNK_SIZE):
                handle.seek(offset)
                chunk = handle.read(CHUNK_SIZE)
                if len(chunk) != CHUNK_SIZE:
                    raise FetchError(f"Could not read a full chunk from {path}")
                checksum += sum(
                    struct.unpack(f"<{CHUNK_SIZE // 8}Q", chunk)
                )
    except OSError as exc:
        raise FetchError(f"Could not read video file {path}: {exc}") from exc

    return f"{(checksum + size) & 0xFFFFFFFFFFFFFFFF:016x}", size
