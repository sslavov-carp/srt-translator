from __future__ import annotations

from srt_translator.tags import protect_tags, restore_tags, visible_length
from srt_translator.wrap import wrap_protected


def test_existing_fitting_lines_are_preserved() -> None:
    assert wrap_protected("Първи ред\nВтори ред", 42, 2) == ["Първи ред", "Втори ред"]


def test_long_text_is_wrapped_to_two_lines() -> None:
    text = (
        "Това е един доста дълъг български субтитър, който трябва да бъде "
        "пренареден в два реда без да се губи текст."
    )
    lines = wrap_protected(text, 42, 2)
    assert len(lines) == 2
    assert " ".join(lines) == text


def test_wrapping_does_not_count_tag_placeholders() -> None:
    protected = protect_tags("<i>Hello world</i>")
    lines = wrap_protected(protected.text, 6, 2)
    restored = restore_tags(protected, "\n".join(lines))
    assert restored == "<i>Hello\nworld</i>"
    assert all(visible_length(line) <= 6 for line in lines)


def test_one_line_mode() -> None:
    lines = wrap_protected("Един дълъг ред, който трябва да остане един", 10, 1)
    assert len(lines) == 1
    assert lines[0].startswith("Един")
