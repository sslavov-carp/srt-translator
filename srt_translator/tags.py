from __future__ import annotations

import re
from dataclasses import dataclass

# HTML/XML-style tags used by SRT, plus ASS/SSA override tags such as
# {\an8} and {\i1}. The whole tag is protected so it is never translated.
TAG_RE = re.compile(r"(<[^>]*>|\{\\[^}]*\})")
ANY_TOKEN_RE = re.compile(r"⟦[^⟧]*\d+⟧")


class TagError(Exception):
    """Raised when formatting tags were lost, duplicated, or reordered."""


@dataclass(frozen=True)
class ProtectedText:
    text: str
    tags: list[str]
    prefix: str

    def token(self, index: int) -> str:
        return f"{self.prefix}{index}⟧"


def _token_prefix(text: str) -> str:
    prefix = "⟦T"
    while prefix in text:
        # Extremely unlikely, but keep placeholders unambiguous if the source
        # already contains a token-shaped string.
        prefix = "⟦" + prefix[1:] + "Z"
    return prefix


def protect_tags(text: str) -> ProtectedText:
    tags: list[str] = []
    prefix = _token_prefix(text)

    def replace(match: re.Match[str]) -> str:
        index = len(tags)
        tags.append(match.group(0))
        return f"{prefix}{index}⟧"

    protected = TAG_RE.sub(replace, text)
    return ProtectedText(text=protected, tags=tags, prefix=prefix)


def _found_indexes(text: str, prefix: str) -> list[int]:
    regex = re.compile(re.escape(prefix) + r"(\d+)⟧")
    return [int(value) for value in regex.findall(text)]


def validate_tags(protected: ProtectedText, translated: str) -> None:
    found = _found_indexes(translated, protected.prefix)
    expected = list(range(len(protected.tags)))
    if found != expected:
        raise TagError(
            "formatting placeholders changed: "
            f"expected {expected}, found {found}"
        )


def restore_tags(protected: ProtectedText, translated: str) -> str:
    validate_tags(protected, translated)
    result = translated
    for index, tag in enumerate(protected.tags):
        result = result.replace(protected.token(index), tag)
    return result


def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text)


def strip_tokens(text: str) -> str:
    return ANY_TOKEN_RE.sub("", text)


def visible_text(text: str) -> str:
    """Return text with formatting tags and safety placeholders removed."""
    return strip_tokens(strip_tags(text))


def visible_length(text: str) -> int:
    return len(visible_text(text))
