from __future__ import annotations

from pathlib import Path

import pytest

from srt_translator.errors import MalformedResponseError, TranslationError
from srt_translator.models import BatchItem
from srt_translator.pipeline import Pipeline
from srt_translator.srt_io import parse_srt

FIXTURE = Path(__file__).parent / "fixtures" / "sample.en.srt"


class FakeTranslator:
    model = "fake-model"

    def __init__(
        self,
        *,
        fail_multi: bool = False,
        break_tag_cue: bool = False,
    ) -> None:
        self.fail_multi = fail_multi
        self.break_tag_cue = break_tag_cue
        self.calls: list[list[str]] = []

    def translate(
        self,
        items: list[BatchItem],
        context_before: list[str],
        context_after: list[str],
        glossary: dict[str, str],
    ) -> dict[int, str]:
        self.calls.append([item.text for item in items])
        if self.fail_multi and len(items) > 1:
            raise MalformedResponseError("simulated batch alignment failure")

        result: dict[int, str] = {}
        for item in items:
            if self.break_tag_cue and "important" in item.text:
                result[item.id] = "Счупен превод"
            else:
                result[item.id] = "БГ " + item.text
        return result


def _document():
    return parse_srt(FIXTURE.read_text(encoding="utf-8"))


def test_pipeline_preserves_timings_and_skips_empty_cues(tmp_path: Path) -> None:
    document = _document()
    timings = [cue.timing_line for cue in document.cues]
    translator = FakeTranslator()
    pipeline = Pipeline(
        translator,
        model="fake-model",
        cache_path=tmp_path / "cache.json",
        batch_size=40,
        no_wrap=True,
        max_line=100,
        log=lambda _message: None,
    )

    result = pipeline.run(document)

    assert result.total_cues == 5
    assert result.translated_cues == 4
    assert result.skipped_cues == 1
    assert [cue.timing_line for cue in document.cues] == timings
    assert document.cues[0].text == "БГ Hello, world!"
    assert document.cues[1].text == "БГ <i>This is important.</i>"
    assert document.cues[2].text == "БГ {\\an8}Top line\nSecond line."
    assert document.cues[3].text == ""
    assert document.cues[4].text == "БГ SARAH: We need to leave now."
    assert all(record.source_text for record in result.records[:3])


def test_cache_avoids_second_api_call(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    translator = FakeTranslator()

    first_document = _document()
    first_pipeline = Pipeline(
        translator,
        model="fake-model",
        cache_path=cache_path,
        no_wrap=True,
        max_line=100,
        log=lambda _message: None,
    )
    first = first_pipeline.run(first_document)
    assert first.api_calls == 1
    assert first.cached_cues == 0

    second_document = _document()
    second_pipeline = Pipeline(
        translator,
        model="fake-model",
        cache_path=cache_path,
        no_wrap=True,
        max_line=100,
        log=lambda _message: None,
    )
    second = second_pipeline.run(second_document)

    assert len(translator.calls) == 1
    assert second.api_calls == 0
    assert second.cached_cues == 4
    assert second_document.cues[0].text == "БГ Hello, world!"


def test_malformed_batch_is_split(tmp_path: Path) -> None:
    document = _document()
    translator = FakeTranslator(fail_multi=True)
    pipeline = Pipeline(
        translator,
        model="fake-model",
        cache_path=tmp_path / "cache.json",
        batch_size=4,
        malformed_retries=0,
        retry_delay=0,
        no_wrap=True,
        log=lambda _message: None,
        sleep=lambda _seconds: None,
    )

    result = pipeline.run(document)

    assert result.translated_cues == 4
    assert result.splits == 3
    assert result.api_calls == 7
    assert document.cues[0].text == "БГ Hello, world!"


def test_invalid_tags_fail_clearly(tmp_path: Path) -> None:
    document = _document()
    translator = FakeTranslator(break_tag_cue=True)
    pipeline = Pipeline(
        translator,
        model="fake-model",
        cache_path=tmp_path / "cache.json",
        batch_size=4,
        malformed_retries=0,
        retry_delay=0,
        no_wrap=True,
        log=lambda _message: None,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(TranslationError):
        pipeline.run(document)

    # The output file is never written by the pipeline; the CLI writes only
    # after this succeeds. The source cue remains untouched.
    assert document.cues[1].text == "<i>This is important.</i>"
