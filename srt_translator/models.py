from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BatchItem:
    id: int
    text: str
    tags: list[str] = field(default_factory=list)


@dataclass
class CueRecord:
    position: int
    label: str
    timing: str
    source_text: str
    output_text: str
    translated: bool
    start: float
    end: float
