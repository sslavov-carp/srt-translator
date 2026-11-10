from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx

from .models import FetchError, MediaInfo

TMDB_BASE = "https://api.themoviedb.org/3"
_IMDB_RE = re.compile(r"^tt(\d{5,10})$", re.IGNORECASE)
_TMDB_RE = re.compile(r"^tmdb[:=](\d+)$", re.IGNORECASE)
_SEASON_EPISODE_RE = re.compile(r"\bS(\d{1,2})E(\d{1,3})\b", re.IGNORECASE)
_ALT_SEASON_EPISODE_RE = re.compile(r"\b(\d{1,2})x(\d{2,3})\b")


class IdentifyError(FetchError):
    """Raised when an input cannot be turned into a movie or episode."""


def _first_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    if isinstance(value, Iterable):
        for item in value:
            result = _first_int(item)
            if result is not None:
                return result
    return None


def parse_season_episode(text: str) -> tuple[int | None, int | None]:
    """Extract SxxExx from text when guessit is not used."""
    match = _SEASON_EPISODE_RE.search(text) or _ALT_SEASON_EPISODE_RE.search(text)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _guessit(value: str) -> dict[str, Any]:
    from guessit import guessit

    try:
        return dict(guessit(value))
    except Exception:  # noqa: BLE001 - guessit raises a variety of internal errors
        return {}


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip(" .-_")


def identify(value: str, *, is_path: bool = False) -> MediaInfo:
    """Turn a file path, id, or title string into a MediaInfo."""
    raw = str(value).strip()
    if not raw:
        raise IdentifyError("No input given")

    path = Path(raw) if is_path else None
    stem = path.stem if path is not None else raw

    imdb_match = _IMDB_RE.match(raw)
    if imdb_match:
        return MediaInfo(title=f"tt{imdb_match.group(1)}", imdb_id=f"tt{imdb_match.group(1)}")

    tmdb_match = _TMDB_RE.match(raw)
    if tmdb_match:
        return MediaInfo(title=f"tmdb:{tmdb_match.group(1)}", tmdb_id=int(tmdb_match.group(1)))

    info = _guessit(stem)

    title = _clean_title(str(info.get("title") or ""))
    year = _first_int(info.get("year"))
    season = _first_int(info.get("season"))
    episode = _first_int(info.get("episode"))

    if season is None or episode is None:
        found_season, found_episode = parse_season_episode(stem)
        season = season if season is not None else found_season
        episode = episode if episode is not None else found_episode

    kind = "episode" if (season is not None and episode is not None) else "movie"

    if not title:
        # Titles made only of digits such as "1923.S02E01" are parsed as a year.
        if year is not None and kind == "episode":
            title = str(year)
            year = None
        else:
            title = _clean_title(stem.replace(".", " "))

    if not title:
        raise IdentifyError(f"Could not work out a title from {raw!r}")

    return MediaInfo(
        title=title,
        kind=kind,
        year=year,
        season=season if kind == "episode" else None,
        episode=episode if kind == "episode" else None,
        file_path=path,
        release_name=stem if path is not None else None,
    )


def identify_input(
    value: str,
    *,
    season: int | None = None,
    episode: int | None = None,
    year: int | None = None,
    imdb_id: str | None = None,
    tmdb_id: int | None = None,
) -> MediaInfo:
    """Identify an input and apply explicit CLI overrides."""
    path = Path(value)
    try:
        is_path = path.is_file()
    except OSError:
        is_path = False
    if not is_path and not value.strip():
        raise IdentifyError("No input given")

    media = identify(value, is_path=is_path)

    if imdb_id:
        imdb_id = imdb_id.strip()
        if not _IMDB_RE.match(imdb_id):
            raise IdentifyError(f"Invalid IMDb id {imdb_id!r}; expected something like tt1375666")
        media = replace(media, imdb_id=imdb_id.lower())

    if tmdb_id is not None:
        if media.kind == "episode":
            media = replace(media, parent_tmdb_id=tmdb_id)
        else:
            media = replace(media, tmdb_id=tmdb_id)

    if year is not None:
        media = replace(media, year=year)

    if season is not None or episode is not None:
        if season is None or episode is None:
            raise IdentifyError("--season and --episode must be used together")
        media = replace(
            media,
            kind="episode",
            season=season,
            episode=episode,
            tmdb_id=None,
        )
        if media.parent_tmdb_id is None and tmdb_id is not None:
            media = replace(media, parent_tmdb_id=tmdb_id)
        if media.imdb_id is not None and media.parent_imdb_id is None:
            media = replace(
                media,
                parent_imdb_id=media.imdb_id,
                imdb_id=None,
            )

    return media


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _year_of(item: dict[str, Any], key: str) -> int | None:
    value = item.get(key)
    if not isinstance(value, str) or len(value) < 4:
        return None
    year = value[:4]
    return int(year) if year.isdigit() else None


class TmdbClient:
    """Minimal TMDB client used to turn titles into ids.

    The client is optional: when no key is configured, resolution is skipped
    and the OpenSubtitles query stays title-based.
    """

    def __init__(
        self,
        api_key: str | None,
        *,
        client: httpx.Client | None = None,
        timeout: float = 20.0,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip() or None
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._log = log or (lambda message: None)

    @property
    def enabled(self) -> bool:
        return self.api_key is not None

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if not self.api_key:
            return None

        headers = {"Accept": "application/json"}
        query = dict(params)
        if self.api_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            query["api_key"] = self.api_key

        try:
            response = self._http().get(f"{TMDB_BASE}{path}", params=query, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            self._log(f"TMDB lookup failed for {path}: {exc}")
            return None
        return payload if isinstance(payload, dict) else None

    def _pick(
        self,
        results: list[dict[str, Any]],
        title: str,
        year: int | None,
        *,
        name_key: str,
        date_key: str,
    ) -> dict[str, Any] | None:
        if not results:
            return None
        wanted = _normalize(title)

        def rank(item: dict[str, Any]) -> tuple[int, int]:
            name = _normalize(str(item.get(name_key) or item.get("original_title") or ""))
            name_score = 2 if name == wanted else 1 if wanted and wanted in name else 0
            item_year = _year_of(item, date_key)
            year_score = 0
            if year is not None and item_year is not None:
                year_score = 1 if item_year in (year, year - 1, year + 1) else -1
            return name_score, year_score

        return max(results, key=rank)

    def resolve(self, media: MediaInfo) -> tuple[MediaInfo, str | None]:
        """Fill in TMDB/IMDb ids. Returns the media and an optional warning."""
        if not self.enabled:
            return media, "TMDB_API_KEY is not set; searching by title only"

        if media.is_episode:
            return self._resolve_episode(media)
        return self._resolve_movie(media)

    def _resolve_movie(self, media: MediaInfo) -> tuple[MediaInfo, str | None]:
        if media.tmdb_id is None and media.imdb_id:
            payload = self._get(
                f"/find/{media.imdb_id}", {"external_source": "imdb_id"}
            )
            results = (payload or {}).get("movie_results") or []
            if results:
                tmdb_id = _first_int(results[0].get("id"))
                if tmdb_id is not None:
                    title = str(results[0].get("title") or media.title)
                    year = _year_of(results[0], "release_date") or media.year
                    return (
                        replace(media, tmdb_id=tmdb_id, title=title, year=year),
                        None,
                    )

        if media.tmdb_id is not None:
            return media, None

        params: dict[str, Any] = {"query": media.title, "include_adult": "false"}
        if media.year is not None:
            params["year"] = media.year
            params["primary_release_year"] = media.year
        payload = self._get("/search/movie", params)
        results = (payload or {}).get("results") or []
        item = self._pick(
            results, media.title, media.year,
            name_key="title", date_key="release_date",
        )
        if item is None:
            return media, f"TMDB found no movie for {media.title!r}"

        tmdb_id = _first_int(item.get("id"))
        year = _year_of(item, "release_date") or media.year
        title = str(item.get("title") or media.title)
        return replace(media, tmdb_id=tmdb_id, year=year, title=title), None

    def _resolve_episode(self, media: MediaInfo) -> tuple[MediaInfo, str | None]:
        if media.parent_tmdb_id is None and media.parent_imdb_id:
            payload = self._get(
                f"/find/{media.parent_imdb_id}", {"external_source": "imdb_id"}
            )
            results = (payload or {}).get("tv_results") or []
            if results:
                tmdb_id = _first_int(results[0].get("id"))
                if tmdb_id is not None:
                    title = str(results[0].get("name") or media.title)
                    year = _year_of(results[0], "first_air_date") or media.year
                    return (
                        replace(
                            media,
                            parent_tmdb_id=tmdb_id,
                            title=title,
                            year=year,
                        ),
                        None,
                    )

        if media.parent_tmdb_id is not None:
            return media, None

        params: dict[str, Any] = {"query": media.title, "include_adult": "false"}
        if media.year is not None:
            params["first_air_date_year"] = media.year
        payload = self._get("/search/tv", params)
        results = (payload or {}).get("results") or []
        item = self._pick(
            results, media.title, media.year,
            name_key="name", date_key="first_air_date",
        )
        if item is None:
            return media, f"TMDB found no series for {media.title!r}"

        tmdb_id = _first_int(item.get("id"))
        year = _year_of(item, "first_air_date") or media.year
        title = str(item.get("name") or media.title)
        return (
            replace(media, parent_tmdb_id=tmdb_id, year=year, title=title),
            None,
        )
