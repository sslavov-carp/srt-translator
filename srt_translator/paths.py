from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

APP_DIR_NAME = "srt-translator"
_INVALID_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def cache_root() -> Path:
    """Return the per-user directory for cache and QC files."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_DIR_NAME

    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    return Path.home() / ".cache" / APP_DIR_NAME


def fetch_dir() -> Path:
    """Return the per-user directory for fetched subtitle data."""
    return cache_root() / "fetch"


def work_dir_for(input_path: Path, root: Path | None = None) -> Path:
    """Return a stable per-input directory for cache and reports.

    The short hash keeps files with the same name from different folders
    apart, while the stem makes the directory easy to recognize.
    """
    resolved = Path(input_path).resolve()
    normalized = os.path.normcase(str(resolved))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    name = _INVALID_NAME_RE.sub("_", resolved.stem).strip(" .") or "subtitles"
    base = Path(root) if root is not None else cache_root()
    return base / f"{name}-{digest}"
