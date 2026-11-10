from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(value: str) -> str:
    cleaned = _SAFE_RE.sub("_", value).strip("._-")
    return cleaned[:80] or "item"


class DownloadCache:
    """Store downloaded subtitle bytes so the same file never costs quota twice."""

    def __init__(self, root: Path, *, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled

    def path_for(self, source: str, candidate_id: str) -> Path:
        return self.root / _safe(source) / f"{_safe(candidate_id)}.srt"

    def metadata_path(self, source: str, candidate_id: str) -> Path:
        return self.path_for(source, candidate_id).with_suffix(".json")

    def get(self, source: str, candidate_id: str) -> bytes | None:
        if not self.enabled:
            return None
        path = self.path_for(source, candidate_id)
        try:
            return path.read_bytes()
        except OSError:
            return None

    def info(self, source: str, candidate_id: str) -> dict[str, object]:
        path = self.metadata_path(source, candidate_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def put(
        self,
        source: str,
        candidate_id: str,
        data: bytes,
        *,
        filename: str = "",
        language: str = "",
        release: str = "",
    ) -> Path:
        path = self.path_for(source, candidate_id)
        if not self.enabled:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

        metadata = {
            "source": source,
            "candidate_id": candidate_id,
            "filename": filename,
            "language": language,
            "release": release,
            "saved_at": datetime.now(UTC).isoformat(),
            "size": len(data),
        }
        meta_path = self.metadata_path(source, candidate_id)
        meta_tmp = meta_path.with_name(meta_path.name + ".tmp")
        try:
            meta_tmp.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(meta_tmp, meta_path)
        finally:
            if meta_tmp.exists():
                try:
                    meta_tmp.unlink()
                except OSError:
                    pass
        return path

    def clear(self) -> int:
        removed = 0
        if not self.root.exists():
            return 0
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.is_file():
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            self.root.rmdir()
        except OSError:
            pass
        return removed
