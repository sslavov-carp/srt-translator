from __future__ import annotations

from pathlib import Path

import pytest

from srt_translator.fetch.identify import (
    IdentifyError,
    identify,
    identify_input,
    parse_season_episode,
)
from srt_translator.fetch.naming import media_stem, output_path, sanitize_title


def test_identifies_movie_release_name() -> None:
    media = identify("Inception.2010.1080p.BluRay.x264-GRP")

    assert media.title == "Inception"
    assert media.year == 2010
    assert media.kind == "movie"
    assert media.season is None


def test_identifies_episode_release_name() -> None:
    media = identify("Breaking.Bad.S02E05.720p.HDTV.x264-KILLERS")

    assert media.title == "Breaking Bad"
    assert media.kind == "episode"
    assert (media.season, media.episode) == (2, 5)


def test_identifies_text_input() -> None:
    media = identify_input("Breaking Bad S02E05")

    assert media.title == "Breaking Bad"
    assert media.kind == "episode"


def test_identifies_imdb_and_tmdb_ids() -> None:
    imdb = identify_input("tt1375666")
    tmdb = identify_input("tmdb:27205")

    assert imdb.imdb_id == "tt1375666"
    assert tmdb.tmdb_id == 27205


def test_explicit_tmdb_id_for_episode_becomes_parent_id() -> None:
    media = identify_input("Breaking Bad S02E05", tmdb_id=1396)

    assert media.tmdb_id is None
    assert media.parent_tmdb_id == 1396


def test_episode_override_turns_movie_into_episode() -> None:
    media = identify_input("Some Show", season=1, episode=3)

    assert media.kind == "episode"
    assert (media.season, media.episode) == (1, 3)


def test_season_override_requires_episode() -> None:
    with pytest.raises(IdentifyError):
        identify_input("Some Show", season=1)


def test_numeric_title_is_kept_for_episode() -> None:
    media = identify_input("1923.S02E01.1080p")

    assert media.title == "1923"
    assert media.kind == "episode"
    assert media.year is None


def test_parse_season_episode_variants() -> None:
    assert parse_season_episode("Show S02E05") == (2, 5)
    assert parse_season_episode("Show 2x05") == (2, 5)
    assert parse_season_episode("Inception 2010") == (None, None)


def test_file_input_sets_path_and_release_name(tmp_path: Path) -> None:
    video = tmp_path / "The.Matrix.1999.2160p.UHD.BluRay.x265-TERMiNAL.mkv"
    video.write_bytes(b"x")

    media = identify_input(str(video))

    assert media.file_path == video
    assert media.release_name == video.stem
    assert media.title == "The Matrix"
    assert media.year == 1999


def test_media_stem_for_movie_and_episode() -> None:
    movie = identify_input("Inception 2010")
    episode = identify_input("Breaking Bad S02E05")

    assert media_stem(movie) == "Inception.2010"
    assert media_stem(episode) == "Breaking.Bad.S02E05"


def test_output_path_suffixes() -> None:
    movie = identify_input("Inception 2010")
    directory = Path("C:/Movies")

    assert output_path(movie, "bg", directory=directory).name == "Inception.2010.bg.srt"
    assert output_path(movie, "en", directory=directory).name == "Inception.2010.en.srt"
    assert (
        output_path(movie, "bg", directory=directory, machine=True).name
        == "Inception.2010.bg.machine.srt"
    )


def test_sanitize_title_handles_invalid_characters() -> None:
    assert sanitize_title('Movie: "Name" / Part 2') == "Movie.Name.Part.2"
    assert sanitize_title("   ") == "subtitles"
    assert sanitize_title("CON") == "CON_"


def test_title_without_path_uses_current_directory() -> None:
    movie = identify_input("Inception 2010")

    path = output_path(movie, "bg")

    assert path.parent == Path.cwd()
