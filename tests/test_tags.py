from __future__ import annotations

import pytest

from srt_translator.tags import (
    TagError,
    protect_tags,
    restore_tags,
    visible_length,
    visible_text,
)


def test_html_tag_round_trip() -> None:
    protected = protect_tags("<i>Hello</i> world")
    assert protected.text.count("⟦T") == 2

    translated = protected.text.replace("Hello", "Здравей").replace("world", "свят")
    assert restore_tags(protected, translated) == "<i>Здравей</i> свят"


def test_ass_tag_round_trip() -> None:
    protected = protect_tags("{\\an8}Hello")
    translated = protected.text.replace("Hello", "Здравей")
    assert restore_tags(protected, translated) == "{\\an8}Здравей"


def test_missing_tag_is_rejected() -> None:
    protected = protect_tags("<i>Hello</i>")
    with pytest.raises(TagError):
        restore_tags(protected, "Здравей")


def test_reordered_tags_are_rejected() -> None:
    protected = protect_tags("<i>Hello</i> <b>world</b>")
    token0 = protected.token(0)
    token1 = protected.token(1)
    token2 = protected.token(2)
    token3 = protected.token(3)
    bad = f"{token1}Здравей{token0} {token3}свят{token2}"
    with pytest.raises(TagError):
        restore_tags(protected, bad)


def test_token_collision_uses_new_prefix() -> None:
    protected = protect_tags("⟦T0⟧ <i>Hello</i>")
    assert protected.prefix != "⟦T"
    translated = protected.text.replace("Hello", "Здравей")
    restored = restore_tags(protected, translated)
    assert "⟦T0⟧" in restored
    assert "<i>Здравей</i>" in restored


def test_visible_helpers() -> None:
    assert visible_text("<i>Hello</i>") == "Hello"
    assert visible_length("<i>Hello</i>") == 5
    assert visible_length("⟦T0⟧Hello⟦T1⟧") == 5
