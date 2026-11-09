from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Callable

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .errors import MalformedResponseError, TranslationError
from .models import BatchItem

PROMPT_VERSION = "bg-subtitles-v1"

RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "The id of the input cue.",
                    },
                    "text": {
                        "type": "string",
                        "description": "The Bulgarian translation of that cue.",
                    },
                },
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are a professional subtitle translator working from English into Bulgarian.

Translate every cue in the user's JSON. Return only the JSON object required by
the response schema.

Rules:
1. Return exactly one translation for every object in "cues", with the same
   numeric id. Never merge, split, add, or omit cues.
2. "context_before" and "context_after" are surrounding dialogue for
   understanding only. Never translate or return them.
3. Never output timestamps, cue numbers, commentary, notes, or explanations.
4. Write natural, concise, idiomatic Bulgarian suitable for film and television
   subtitles. Match the tone, register, and character voice. Avoid literal
   English word order.
5. Placeholders such as ⟦T0⟧ represent HTML or ASS formatting tags. Preserve each
   placeholder exactly once, in the same order. Never translate, remove,
   duplicate, or move a placeholder.
6. Preserve meaningful explicit line breaks. The application will reflow lines
   to fit subtitle width.
7. Keep character names, recurring terms, and terminology consistent with the
   glossary. Use the glossary translations when the term appears.
8. Translate the whole cue as one unit. Do not censor or soften the meaning.
9. Use Bulgarian punctuation where natural: an em dash (—) for dialogue and
   Bulgarian quotation marks („…“) for quotations.
10. Do not wrap the output in Markdown code fences.

Glossary (JSON object mapping English terms to preferred Bulgarian terms):
{glossary}
"""

USER_PROMPT = """\
Source language: English
Target language: Bulgarian

Translate every cue in the following JSON and return only the response JSON.

{payload}
"""


def _filter_glossary(
    glossary: dict[str, str], items: list[BatchItem], context: list[str]
) -> dict[str, str]:
    if not glossary:
        return {}
    haystack = "\n".join(context + [item.text for item in items]).casefold()
    return {
        source: target
        for source, target in glossary.items()
        if source.casefold() in haystack
    }


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json_payload(text: str) -> dict:
    cleaned = _strip_code_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise MalformedResponseError("response did not contain a JSON object")
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise MalformedResponseError(f"response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise MalformedResponseError("response JSON root must be an object")
    return data


def _coerce_id(value: object) -> int:
    if isinstance(value, bool):
        raise MalformedResponseError("translation id must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    raise MalformedResponseError(f"translation id is not an integer: {value!r}")


def _parse_translations(payload: dict, expected_ids: set[int]) -> dict[int, str]:
    translations = payload.get("translations")
    if not isinstance(translations, list):
        raise MalformedResponseError("response must contain a translations array")

    result: dict[int, str] = {}
    for entry in translations:
        if not isinstance(entry, dict):
            raise MalformedResponseError("each translation must be an object")
        item_id = _coerce_id(entry.get("id"))
        text = entry.get("text")
        if not isinstance(text, str):
            raise MalformedResponseError(f"translation text for id {item_id} is not a string")
        if item_id in result:
            raise MalformedResponseError(f"duplicate translation id {item_id}")
        result[item_id] = text

    missing = expected_ids - set(result)
    extra = set(result) - expected_ids
    if missing or extra:
        raise MalformedResponseError(
            "translation ids do not match the request: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return result


def _retry_delay_seconds(error: genai_errors.APIError, attempt: int) -> float:
    def walk(value: object):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "retryDelay" and isinstance(item, str):
                    yield item
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    for value in walk(getattr(error, "details", None)):
        match = re.fullmatch(r"(\d+(?:\.\d+)?)s", value.strip())
        if match:
            return min(120.0, float(match.group(1)) + 1.0)
    return min(120.0, 5.0 * (2**attempt) + random.uniform(0.0, 1.0))


def _is_retryable_api_error(error: genai_errors.APIError) -> bool:
    return error.code in {408, 429} or 500 <= error.code < 600


def _is_retryable_exception(error: Exception) -> bool:
    message = str(error).casefold()
    needles = (
        "timeout",
        "timed out",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "network",
    )
    return any(needle in message for needle in needles)


class GeminiTranslator:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        rpm: float = 10.0,
        max_retries: int = 5,
        max_output_tokens: int = 16_384,
        log: Callable[[str], None] | None = None,
    ) -> None:
        if not api_key:
            raise TranslationError("A Gemini API key is required")
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.rpm = rpm
        self.max_retries = max(0, max_retries)
        self.max_output_tokens = max_output_tokens
        self.log = log or (lambda _message: None)
        self._last_request_at = 0.0
        self._schema_enabled = True

    def _pace(self) -> None:
        if self.rpm <= 0:
            return
        minimum_interval = 60.0 / self.rpm
        now = time.monotonic()
        wait = minimum_interval - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _build_prompts(
        self,
        items: list[BatchItem],
        context_before: list[str],
        context_after: list[str],
        glossary: dict[str, str],
    ) -> tuple[str, str]:
        relevant_glossary = _filter_glossary(
            glossary, items, context_before + context_after
        )
        system = SYSTEM_PROMPT.format(
            glossary=json.dumps(
                relevant_glossary, ensure_ascii=False, indent=2, sort_keys=True
            )
        )
        payload = {
            "context_before": context_before,
            "cues": [{"id": item.id, "text": item.text} for item in items],
            "context_after": context_after,
        }
        user = USER_PROMPT.format(
            payload=json.dumps(payload, ensure_ascii=False, indent=2)
        )
        return system, user

    def _extract_payload(self, response: object) -> dict:
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            if hasattr(parsed, "model_dump"):
                parsed = parsed.model_dump()
            if isinstance(parsed, dict):
                return parsed

        text = getattr(response, "text", None)
        if not text:
            raise TranslationError(
                "Gemini returned no text. The request may have been blocked by "
                "safety filters or exceeded the output limit."
            )
        return _parse_json_payload(text)

    def _request(
        self,
        *,
        system: str,
        user: str,
    ) -> dict:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.temperature,
            top_p=0.95,
            response_mime_type="application/json",
            response_json_schema=RESPONSE_SCHEMA if self._schema_enabled else None,
            max_output_tokens=self.max_output_tokens,
        )
        response = self.client.models.generate_content(
            model=self.model,
            contents=user,
            config=config,
        )
        return self._extract_payload(response)

    def translate(
        self,
        items: list[BatchItem],
        context_before: list[str],
        context_after: list[str],
        glossary: dict[str, str],
    ) -> dict[int, str]:
        system, user = self._build_prompts(
            items, context_before, context_after, glossary
        )
        expected_ids = {item.id for item in items}
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self._pace()
            try:
                payload = self._request(system=system, user=user)
                return _parse_translations(payload, expected_ids)
            except MalformedResponseError:
                # The pipeline retries malformed output and splits the batch
                # when necessary.
                raise
            except genai_errors.APIError as exc:
                last_error = exc
                message = str(exc)
                if (
                    self._schema_enabled
                    and exc.code == 400
                    and "response" in message.casefold()
                    and ("schema" in message.casefold() or "mime" in message.casefold())
                ):
                    self._schema_enabled = False
                    self.log(
                        "Model rejected structured-output settings; retrying with "
                        "JSON mime type only."
                    )
                    continue
                if attempt < self.max_retries and _is_retryable_api_error(exc):
                    delay = _retry_delay_seconds(exc, attempt)
                    self.log(
                        f"Gemini API error {exc.code}; retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{self.max_retries + 1})."
                    )
                    time.sleep(delay)
                    continue
                raise TranslationError(f"Gemini API error: {exc}") from exc
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries and _is_retryable_exception(exc):
                    delay = min(60.0, 5.0 * (2**attempt))
                    self.log(
                        f"Network/API failure; retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{self.max_retries + 1})."
                    )
                    time.sleep(delay)
                    continue
                raise TranslationError(f"Gemini request failed: {exc}") from exc

        raise TranslationError(f"Gemini request failed: {last_error}")
