from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from srt_translator.fetch.models import Candidate, MediaInfo
from srt_translator.fetch.sources.base import SourceConfigError, SourceError
from srt_translator.fetch.sources.opensubtitles import OpenSubtitlesSource

SEARCH_PAYLOAD: dict[str, Any] = {
    "data": [
        {
            "id": "107689",
            "attributes": {
                "subtitle_id": "107689",
                "language": "bg",
                "download_count": 15000,
                "ratings": 8.5,
                "hearing_impaired": False,
                "fps": 23.976,
                "from_trusted": True,
                "foreign_parts_only": False,
                "ai_translated": False,
                "machine_translated": False,
                "nb_cd": 1,
                "release": "Movie.2010.1080p.BluRay.x264-GRP",
                "moviehash_match": True,
                "format": "srt",
                "feature_details": {
                    "season_number": None,
                    "episode_number": None,
                    "year": 2010,
                    "imdb_id": 1375666,
                    "tmdb_id": 27205,
                    "title": "Movie",
                },
                "url": "https://www.opensubtitles.com/en/subtitles/107689",
                "files": [
                    {
                        "file_id": 111674,
                        "cd_number": 1,
                        "file_name": "Movie.2010.1080p.srt",
                    }
                ],
            },
        }
    ]
}

DOWNLOAD_PAYLOAD = {
    "link": "https://cdn.example.com/sub.srt",
    "file_name": "Movie.2010.bg.srt",
    "requests": 1,
    "remaining": 19,
    "message": "ok",
    "reset_time_utc": "2026-11-11T00:00:00Z",
}

SUBTITLE_BYTES = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_parses_candidates_and_sends_params() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    media = MediaInfo(
        title="Show",
        kind="episode",
        season=2,
        episode=5,
        parent_tmdb_id=1396,
        file_hash="abc123",
    )
    source = OpenSubtitlesSource("key", client=_client(handler))

    candidates = source.search(media, "bg")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.candidate_id == "107689"
    assert candidate.language == "bg"
    assert candidate.moviehash_match is True
    assert candidate.extra["file_id"] == 111674

    params = seen[0].url.params
    assert params["languages"] == "bg"
    assert params["parent_tmdb_id"] == "1396"
    assert params["season_number"] == "2"
    assert params["episode_number"] == "5"
    assert params["moviehash"] == "abc123"
    assert seen[0].headers["api-key"] == "key"


def test_search_by_imdb_for_movie() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    source = OpenSubtitlesSource("key", client=_client(handler))
    source.search(MediaInfo(title="Movie", imdb_id="tt1375666"), "en")

    params = seen[0].url.params
    assert params["imdb_id"] == "tt1375666"
    assert "query" not in params


def test_search_falls_back_to_title_query() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    source = OpenSubtitlesSource("key", client=_client(handler))
    source.search(MediaInfo(title="Inception", year=2010), "bg")

    params = seen[0].url.params
    assert params["query"] == "Inception"
    assert params["year"] == "2010"


def test_download_returns_bytes_and_quota() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/download":
            return httpx.Response(200, json=DOWNLOAD_PAYLOAD)
        if request.url.host == "cdn.example.com":
            return httpx.Response(200, content=SUBTITLE_BYTES)
        return httpx.Response(404)

    source = OpenSubtitlesSource("key", client=_client(handler))
    candidate = Candidate(
        source="opensubtitles",
        language="bg",
        candidate_id="107689",
        extra={"file_id": 111674},
    )
    result = source.download(candidate)

    assert result.data == SUBTITLE_BYTES
    assert result.filename == "Movie.2010.bg.srt"
    assert result.remaining == 19
    assert result.reset_time_utc == "2026-11-11T00:00:00Z"


def test_api_key_is_not_sent_to_the_download_host() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/download":
            return httpx.Response(200, json=DOWNLOAD_PAYLOAD)
        return httpx.Response(200, content=SUBTITLE_BYTES)

    source = OpenSubtitlesSource("secret-key", client=_client(handler))
    source.download(
        Candidate(
            source="opensubtitles",
            language="bg",
            candidate_id="1",
            extra={"file_id": 5},
        )
    )

    api_request, cdn_request = seen
    assert api_request.headers["api-key"] == "secret-key"
    assert "api-key" not in cdn_request.headers


def test_login_adds_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/login":
            return httpx.Response(200, json={"token": "token-123"})
        return httpx.Response(200, json={"data": []})

    source = OpenSubtitlesSource(
        "key",
        username="user",
        password="pass",
        client=_client(handler),
    )

    assert source.login() is True
    source.search(MediaInfo(title="Movie"), "bg")

    assert json.loads(seen[0].content) == {"username": "user", "password": "pass"}
    assert seen[1].headers["authorization"] == "Bearer token-123"


def test_login_failure_continues_anonymously() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "bad credentials"})

    source = OpenSubtitlesSource(
        "key",
        username="user",
        password="wrong",
        client=_client(handler),
    )

    assert source.login() is False
    assert source.authenticated is False


def test_retries_429_and_honours_retry_after() -> None:
    calls = {"count": 0}
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"data": []})

    source = OpenSubtitlesSource(
        "key",
        client=_client(handler),
        max_retries=3,
        sleep=delays.append,
    )

    assert source.search(MediaInfo(title="Movie"), "bg") == []
    assert calls["count"] == 3
    assert delays == [0.0, 0.0]


def test_http_error_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    source = OpenSubtitlesSource("key", client=_client(handler))

    with pytest.raises(SourceError, match="Unauthorized"):
        source.search(MediaInfo(title="Movie"), "bg")


def test_missing_api_key_is_a_config_error() -> None:
    source = OpenSubtitlesSource(None)

    with pytest.raises(SourceConfigError):
        source.search(MediaInfo(title="Movie"), "bg")
