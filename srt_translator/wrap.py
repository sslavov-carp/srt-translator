from __future__ import annotations

from .tags import visible_length

_PUNCTUATION_ENDINGS = (".", ",", "!", "?", ";", ":", "…", "—", "–", "-", ")")


def _merge_zero_width_words(words: list[str]) -> list[str]:
    """Keep tag placeholders attached to the nearest visible word."""
    merged: list[str] = []
    pending = ""
    for word in words:
        if visible_length(word) == 0:
            pending += word
            continue
        merged.append(pending + word)
        pending = ""
    if pending:
        if merged:
            merged[-1] += pending
        else:
            merged.append(pending)
    return merged


def _ends_with_punctuation(text: str) -> bool:
    stripped = text.rstrip()
    return bool(stripped) and stripped.endswith(_PUNCTUATION_ENDINGS)


def _wrap_two(words: list[str], max_line: int) -> list[str]:
    if len(words) <= 1:
        return [" ".join(words)] if words else []

    best_index = 1
    best_score = float("inf")

    for index in range(1, len(words)):
        line1 = " ".join(words[:index])
        line2 = " ".join(words[index:])
        len1 = visible_length(line1)
        len2 = visible_length(line2)

        overflow = max(0, len1 - max_line) + max(0, len2 - max_line)
        score = overflow * 10_000.0 + abs(len1 - len2) * 0.5

        if _ends_with_punctuation(line1):
            score -= 35.0
        if index == len(words) - 1 and len(words) > 2:
            score += 1_000.0
        if len1 == 0 or len2 == 0:
            score += 10_000.0

        if score < best_score:
            best_score = score
            best_index = index

    return [
        " ".join(words[:best_index]),
        " ".join(words[best_index:]),
    ]


def _wrap_greedy(words: list[str], max_line: int) -> list[str]:
    """Fallback for a custom max-lines value above 2."""
    lines: list[str] = []
    current: list[str] = []
    current_length = 0

    for word in words:
        word_length = visible_length(word)
        candidate_length = word_length if not current else current_length + 1 + word_length
        if current and candidate_length > max_line:
            lines.append(" ".join(current))
            current = [word]
            current_length = word_length
        else:
            current.append(word)
            current_length = candidate_length

    if current:
        lines.append(" ".join(current))
    return lines


def wrap_protected(text: str, max_line: int = 42, max_lines: int = 2) -> list[str]:
    """Wrap protected text (tags replaced by placeholders) into subtitle lines.

    Existing line breaks are preserved when they already fit the configured
    limits. Otherwise the text is reflowed, preferring punctuation boundaries.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    existing = [line.strip() for line in text.split("\n") if line.strip()]
    if (
        1 <= len(existing) <= max_lines
        and all(visible_length(line) <= max_line for line in existing)
    ):
        return existing

    words = _merge_zero_width_words(text.split())
    if not words:
        return []

    if max_lines <= 1:
        return [" ".join(words)]
    if max_lines == 2:
        return _wrap_two(words, max_line)
    return _wrap_greedy(words, max_line)
