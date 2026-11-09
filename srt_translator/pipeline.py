from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .cache import TranslationCache, make_cache_key
from .errors import MalformedResponseError, TranslationError
from .gemini import PROMPT_VERSION
from .models import BatchItem, CueRecord
from .srt_io import Cue, SrtDocument
from .tags import (
    ProtectedText,
    TagError,
    protect_tags,
    restore_tags,
    validate_tags,
    visible_text,
)
from .wrap import wrap_protected


class Translator(Protocol):
    model: str

    def translate(
        self,
        items: list[BatchItem],
        context_before: list[str],
        context_after: list[str],
        glossary: dict[str, str],
    ) -> dict[int, str]: ...


@dataclass
class PreparedCue:
    position: int
    cue: Cue
    protected: ProtectedText
    source_visible: str
    cache_key: str
    translation: str | None = None
    cached: bool = False


@dataclass
class PipelineResult:
    records: list[CueRecord]
    total_cues: int
    translated_cues: int
    cached_cues: int
    skipped_cues: int
    api_calls: int
    splits: int


class Pipeline:
    def __init__(
        self,
        translator: Translator,
        *,
        model: str | None = None,
        glossary: dict[str, str] | None = None,
        cache_path: Path | None = None,
        use_cache: bool = True,
        batch_size: int = 40,
        context: int = 2,
        malformed_retries: int = 2,
        retry_delay: float = 0.8,
        max_line: int = 42,
        max_lines: int = 2,
        no_wrap: bool = False,
        verbose: bool = False,
        log: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.translator = translator
        self.model = model or getattr(translator, "model", "")
        self.glossary = glossary or {}
        self.batch_size = max(1, batch_size)
        self.context = max(0, context)
        self.malformed_retries = max(0, malformed_retries)
        self.retry_delay = max(0.0, retry_delay)
        self.max_line = max(1, max_line)
        self.max_lines = max(1, max_lines)
        self.no_wrap = no_wrap
        self.verbose = verbose
        self.log = log or (lambda _message: None)
        self.sleep = sleep
        self.cache = TranslationCache(
            cache_path,
            enabled=use_cache,
            log=self.log,
        )
        self._visible: list[str] = []
        self._api_calls = 0
        self._splits = 0

    def _collect_before(self, position: int) -> list[str]:
        result: list[str] = []
        index = position - 1
        while index >= 0 and len(result) < self.context:
            if self._visible[index]:
                result.append(self._visible[index])
            index -= 1
        result.reverse()
        return result

    def _collect_after(self, position: int) -> list[str]:
        result: list[str] = []
        index = position + 1
        while index < len(self._visible) and len(result) < self.context:
            if self._visible[index]:
                result.append(self._visible[index])
            index += 1
        return result

    def _context_for_chunk(self, chunk: list[PreparedCue]) -> tuple[list[str], list[str]]:
        positions = [item.position for item in chunk]
        return self._collect_before(min(positions)), self._collect_after(max(positions))

    def _prepare(self, document: SrtDocument) -> list[PreparedCue]:
        self._visible = [visible_text(cue.text).strip() for cue in document.cues]
        prepared: list[PreparedCue] = []

        for position, cue in enumerate(document.cues):
            source_visible = self._visible[position]
            if not source_visible:
                continue

            protected = protect_tags(cue.text)
            before = self._collect_before(position)
            after = self._collect_after(position)
            key = make_cache_key(
                model=self.model,
                prompt_version=PROMPT_VERSION,
                glossary=self.glossary,
                source=protected.text,
                context_before=before,
                context_after=after,
            )
            item = PreparedCue(
                position=position,
                cue=cue,
                protected=protected,
                source_visible=source_visible,
                cache_key=key,
            )

            cached = self.cache.get(key)
            if cached is not None:
                try:
                    validate_tags(protected, cached)
                except TagError as exc:
                    self.log(
                        f"Discarding invalid cache entry for cue {cue.label}: {exc}"
                    )
                    self.cache.delete(key)
                else:
                    if cached.strip():
                        item.translation = cached.replace("\r\n", "\n").replace("\r", "\n")
                        item.cached = True
                        if self.verbose:
                            self.log(f"Cache hit for cue {cue.label}")

            prepared.append(item)
        return prepared

    def _normalise_mapping(
        self,
        mapping: dict[int, str],
        expected_ids: set[int],
    ) -> dict[int, str]:
        if not isinstance(mapping, dict):
            raise MalformedResponseError("translator did not return a dictionary")

        result: dict[int, str] = {}
        for key, value in mapping.items():
            try:
                item_id = int(key)
            except (TypeError, ValueError) as exc:
                raise MalformedResponseError(
                    f"translator returned a non-integer id {key!r}"
                ) from exc
            if item_id in result:
                raise MalformedResponseError(f"duplicate translation id {item_id}")
            if not isinstance(value, str):
                raise MalformedResponseError(
                    f"translation for id {item_id} is not a string"
                )
            result[item_id] = value.replace("\r\n", "\n").replace("\r", "\n")

        missing = expected_ids - set(result)
        extra = set(result) - expected_ids
        if missing or extra:
            raise MalformedResponseError(
                "translation ids do not match the request: "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        return result

    def _validate_chunk(
        self,
        chunk: list[PreparedCue],
        mapping: dict[int, str],
    ) -> dict[int, str]:
        items = [
            BatchItem(id=index, text=item.protected.text, tags=item.protected.tags)
            for index, item in enumerate(chunk)
        ]
        normalised = self._normalise_mapping(mapping, {item.id for item in items})

        for item, prepared_cue in zip(items, chunk):
            text = normalised[item.id]
            if not text.strip():
                raise MalformedResponseError(
                    f"empty translation for cue {prepared_cue.cue.label}"
                )
            try:
                validate_tags(prepared_cue.protected, text)
            except TagError as exc:
                raise MalformedResponseError(
                    f"cue {prepared_cue.cue.label}: {exc}"
                ) from exc
        return normalised

    def _try_chunk(self, chunk: list[PreparedCue]) -> None:
        before, after = self._context_for_chunk(chunk)
        items = [
            BatchItem(id=index, text=item.protected.text, tags=item.protected.tags)
            for index, item in enumerate(chunk)
        ]

        last_error: MalformedResponseError | None = None
        for attempt in range(self.malformed_retries + 1):
            self._api_calls += 1
            try:
                mapping = self.translator.translate(
                    items,
                    before,
                    after,
                    self.glossary,
                )
                normalised = self._validate_chunk(chunk, mapping)
            except MalformedResponseError as exc:
                last_error = exc
                if attempt < self.malformed_retries:
                    self.log(
                        f"Malformed translation response ({exc}); retrying "
                        f"({attempt + 1}/{self.malformed_retries + 1})."
                    )
                    if self.retry_delay:
                        self.sleep(self.retry_delay)
                    continue
                raise
            else:
                for index, prepared_cue in enumerate(chunk):
                    prepared_cue.translation = normalised[index]
                    self.cache.put(prepared_cue.cache_key, normalised[index])
                self.cache.save()
                return

        raise last_error or MalformedResponseError("unknown translation failure")

    def _translate_with_recovery(self, chunk: list[PreparedCue]) -> None:
        try:
            self._try_chunk(chunk)
        except MalformedResponseError as exc:
            if len(chunk) <= 1:
                cue = chunk[0].cue
                raise TranslationError(
                    f"Could not obtain a valid translation for cue {cue.label}: {exc}"
                ) from exc

            self._splits += 1
            midpoint = len(chunk) // 2
            self.log(
                f"Splitting a {len(chunk)}-cue batch after validation failure: {exc}"
            )
            self._translate_with_recovery(chunk[:midpoint])
            self._translate_with_recovery(chunk[midpoint:])

    def run(self, document: SrtDocument) -> PipelineResult:
        source_texts = [cue.text for cue in document.cues]
        prepared = self._prepare(document)
        pending = [item for item in prepared if item.translation is None]

        cached_count = sum(1 for item in prepared if item.cached)
        if prepared:
            self.log(
                f"{len(prepared)} translatable cues; "
                f"{cached_count} restored from cache; {len(pending)} to translate."
            )

        if pending:
            batches = [
                pending[index : index + self.batch_size]
                for index in range(0, len(pending), self.batch_size)
            ]
            for number, chunk in enumerate(batches, start=1):
                self.log(
                    f"Batch {number}/{len(batches)}: translating {len(chunk)} cues "
                    f"({chunk[0].cue.label}-{chunk[-1].cue.label})."
                )
                self._translate_with_recovery(chunk)

        prepared_by_position = {item.position: item for item in prepared}
        records: list[CueRecord] = []
        for position, cue in enumerate(document.cues):
            item = prepared_by_position.get(position)
            source_text = source_texts[position]

            if item is None or item.translation is None:
                output_text = cue.text
                translated = False
            else:
                if self.no_wrap:
                    output_text = restore_tags(item.protected, item.translation).strip()
                else:
                    lines = wrap_protected(
                        item.translation,
                        max_line=self.max_line,
                        max_lines=self.max_lines,
                    )
                    output_text = restore_tags(item.protected, "\n".join(lines)).strip()
                cue.text = output_text
                translated = True

            start, end = cue.bounds()
            records.append(
                CueRecord(
                    position=position,
                    label=cue.label,
                    timing=cue.timing_line,
                    source_text=source_text,
                    output_text=cue.text,
                    translated=translated,
                    start=start,
                    end=end,
                )
            )

        return PipelineResult(
            records=records,
            total_cues=len(document.cues),
            translated_cues=sum(1 for item in prepared if item.translation is not None),
            cached_cues=cached_count,
            skipped_cues=len(document.cues) - len(prepared),
            api_calls=self._api_calls,
            splits=self._splits,
        )
