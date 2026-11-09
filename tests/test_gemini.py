from __future__ import annotations

import json

import pytest
from google.genai import errors as genai_errors

from srt_translator.errors import MalformedResponseError, TranslationError
from srt_translator.gemini import GeminiTranslator
from srt_translator.models import BatchItem


class FakeResponse:
    def __init__(self, *, text: str | None = None, parsed: object | None = None) -> None:
        self.text = text
        self.parsed = parsed


class FakeModels:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def generate_content(self, *, model: str, contents: str, config: object) -> object:
        self.calls.append({"model": model, "contents": contents, "config": config})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.models = FakeModels(responses)


def _translator(monkeypatch, responses: list[object], **kwargs) -> GeminiTranslator:
    client = FakeClient(responses)
    monkeypatch.setattr(
        "srt_translator.gemini.genai.Client",
        lambda api_key: client,
    )
    translator = GeminiTranslator(api_key="test-key", model="gemini-test", rpm=0, **kwargs)
    translator.client = client
    return translator


ITEMS = [
    BatchItem(id=0, text="Hello"),
    BatchItem(id=1, text="<i>World</i>"),
]


def test_successful_parsed_response(monkeypatch) -> None:
    payload = {
        "translations": [
            {"id": 0, "text": "Здравей"},
            {"id": 1, "text": "<i>Свят</i>"},
        ]
    }
    translator = _translator(monkeypatch, [FakeResponse(parsed=payload)])

    result = translator.translate(ITEMS, [], [], {})

    assert result == {0: "Здравей", 1: "<i>Свят</i>"}
    call = translator.client.models.calls[0]
    assert call["model"] == "gemini-test"
    assert '"id": 0' in call["contents"]
    assert "-->" not in call["contents"]
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema is not None


def test_text_fallback_strips_code_fence(monkeypatch) -> None:
    body = {
        "translations": [
            {"id": "0", "text": "Здравей"},
            {"id": "1", "text": "<i>Свят</i>"},
        ]
    }
    translator = _translator(
        monkeypatch,
        [FakeResponse(text="```json\n" + json.dumps(body) + "\n```")],
    )

    assert translator.translate(ITEMS, [], [], {}) == {
        0: "Здравей",
        1: "<i>Свят</i>",
    }


def test_missing_id_is_rejected(monkeypatch) -> None:
    payload = {"translations": [{"id": 0, "text": "Здравей"}]}
    translator = _translator(monkeypatch, [FakeResponse(parsed=payload)])

    with pytest.raises(MalformedResponseError):
        translator.translate(ITEMS, [], [], {})


def test_duplicate_id_is_rejected(monkeypatch) -> None:
    payload = {
        "translations": [
            {"id": 0, "text": "Едно"},
            {"id": 0, "text": "Две"},
            {"id": 1, "text": "<i>Свят</i>"},
        ]
    }
    translator = _translator(monkeypatch, [FakeResponse(parsed=payload)])

    with pytest.raises(MalformedResponseError):
        translator.translate(ITEMS, [], [], {})


def test_retryable_api_error_is_retried(monkeypatch) -> None:
    payload = {
        "translations": [
            {"id": 0, "text": "Здравей"},
            {"id": 1, "text": "<i>Свят</i>"},
        ]
    }
    error = genai_errors.APIError(
        429,
        {"error": {"message": "quota", "details": [{"retryDelay": "0s"}]}},
    )
    translator = _translator(
        monkeypatch,
        [error, FakeResponse(parsed=payload)],
        max_retries=1,
    )
    monkeypatch.setattr("srt_translator.gemini.time.sleep", lambda _seconds: None)

    assert translator.translate(ITEMS, [], [], {})[0] == "Здравей"
    assert len(translator.client.models.calls) == 2


def test_non_retryable_api_error_fails(monkeypatch) -> None:
    error = genai_errors.APIError(403, {"error": {"message": "denied"}})
    translator = _translator(monkeypatch, [error], max_retries=3)

    with pytest.raises(TranslationError):
        translator.translate(ITEMS, [], [], {})
    assert len(translator.client.models.calls) == 1


def test_glossary_is_filtered_to_relevant_terms(monkeypatch) -> None:
    payload = {
        "translations": [
            {"id": 0, "text": "Здравей"},
            {"id": 1, "text": "<i>Свят</i>"},
        ]
    }
    translator = _translator(monkeypatch, [FakeResponse(parsed=payload)])

    translator.translate(
        ITEMS,
        [],
        [],
        {"Hello": "Здравей", "Unrelated": "Несвързано"},
    )

    system = translator.client.models.calls[0]["config"].system_instruction
    assert "Здравей" in system
    assert "Несвързано" not in system
