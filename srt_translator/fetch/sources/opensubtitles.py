from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from ..models import Candidate, DownloadResult, MediaInfo
from .base import SourceConfigError, SourceError

API_BASE = "https://api.opensubtitles.com/api/v1"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_BACKOFF = 30.0


def _seconds_from_retry_after(response: httpx.Response, fallback: float) -> float:
    value = response.headers.get("Retry-After")
    if not value:
        return fallback
    try:
        return min(float(value), MAX_BACKOFF)
    except ValueError:
        return fallback


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        if isinstance(message, str) and message.strip():
            return message.strip()
    text = response.text.strip()
    return text[:300] if text else response.reason_phrase


class OpenSubtitlesSource:
    """Adapter for the official OpenSubtitles REST API."""

    name = "opensubtitles"

    def __init__(
        self,
        api_key: str | None,
        *,
        username: str | None = None,
        password: str | None = None,
        client: httpx.Client | None = None,
        user_agent: str = "srt-translator",
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip() or None
        self.username = (username or "").strip() or None
        self.password = password or None
        self.user_agent = user_agent
        self.max_retries = max(0, max_retries)
        self.backoff = backoff
        self._sleep = sleep
        self._log = log or (lambda message: None)
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._token: str | None = None
        self._login_failed = False

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    @property
    def configured(self) -> bool:
        return self.api_key is not None

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise SourceConfigError(
                "OPENSUBTITLES_API_KEY is not set. Create an API key at "
                "https://www.opensubtitles.com/en/users/settings/api"
            )
        headers = {
            "Api-Key": self.api_key,
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send a request, retrying 429/5xx and network errors with backoff."""
        url = url if url.startswith("http") else f"{API_BASE}{url}"
        external = url.startswith("http") and not url.startswith(API_BASE)
        headers = {"User-Agent": self.user_agent} if external else self._headers()
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self._http().request(
                    method, url, headers=headers, **kwargs
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self._sleep(self.backoff * (2**attempt))
                continue

            if response.status_code not in RETRY_STATUSES or attempt >= self.max_retries:
                return response

            delay = _seconds_from_retry_after(
                response, self.backoff * (2**attempt)
            )
            self._log(
                f"OpenSubtitles returned {response.status_code}; retrying in "
                f"{delay:.1f}s"
            )
            response.close()
            self._sleep(delay)

        raise SourceError(f"OpenSubtitles request failed: {last_error}")

    def _check(self, response: httpx.Response, action: str) -> None:
        if response.is_success:
            return
        raise SourceError(
            f"OpenSubtitles {action} failed with HTTP {response.status_code}: "
            f"{_error_message(response)}"
        )

    def login(self) -> bool:
        """Log in when credentials are available. Failure is not fatal."""
        if self._token:
            return True
        if self._login_failed or not self.username or not self.password:
            return False
        try:
            response = self._request(
                "POST",
                "/login",
                json={"username": self.username, "password": self.password},
            )
            self._check(response, "login")
            payload = response.json()
        except (SourceError, ValueError) as exc:
            self._login_failed = True
            self._log(
                f"OpenSubtitles login failed, continuing with anonymous limits: {exc}"
            )
            return False

        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            self._login_failed = True
            self._log("OpenSubtitles login returned no token; using anonymous limits")
            return False

        self._token = token
        self._log("OpenSubtitles: logged in (20 downloads/day)")
        return True

    def _search_params(self, media: MediaInfo, language: str) -> dict[str, Any]:
        params: dict[str, Any] = {"languages": language}

        if media.is_episode:
            if media.parent_tmdb_id is not None:
                params["parent_tmdb_id"] = media.parent_tmdb_id
            if media.parent_imdb_id is not None:
                params["parent_imdb_id"] = media.parent_imdb_id
            if media.imdb_id is not None:
                params["imdb_id"] = media.imdb_id
            if media.season is not None:
                params["season_number"] = media.season
            if media.episode is not None:
                params["episode_number"] = media.episode
            if not any(
                key in params
                for key in ("parent_tmdb_id", "parent_imdb_id", "imdb_id")
            ):
                params["query"] = media.title
        else:
            if media.imdb_id is not None:
                params["imdb_id"] = media.imdb_id
            elif media.tmdb_id is not None:
                params["tmdb_id"] = media.tmdb_id
            else:
                params["query"] = media.title
                if media.year is not None:
                    params["year"] = media.year

        if media.file_hash:
            params["moviehash"] = media.file_hash

        return params

    def search(self, media: MediaInfo, language: str) -> list[Candidate]:
        response = self._request(
            "GET", "/subtitles", params=self._search_params(media, language)
        )
        self._check(response, "search")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(f"OpenSubtitles search returned invalid JSON: {exc}") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []

        candidates: list[Candidate] = []
        for item in data:
            candidate = self._candidate(item, language)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _candidate(self, item: object, language: str) -> Candidate | None:
        if not isinstance(item, dict):
            return None
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            return None

        files = attributes.get("files")
        first_file = files[0] if isinstance(files, list) and files else None
        if not isinstance(first_file, dict):
            return None

        file_id = first_file.get("file_id")
        subtitle_id = attributes.get("subtitle_id") or item.get("id")
        if file_id is None or subtitle_id is None:
            return None

        feature = attributes.get("feature_details")
        feature = feature if isinstance(feature, dict) else {}

        return Candidate(
            source=self.name,
            language=str(attributes.get("language") or language),
            candidate_id=str(subtitle_id),
            release=str(attributes.get("release") or ""),
            filename=str(first_file.get("file_name") or ""),
            download_count=int(attributes.get("download_count") or 0),
            rating=float(attributes.get("ratings") or 0.0),
            hearing_impaired=bool(attributes.get("hearing_impaired")),
            machine_translated=bool(attributes.get("machine_translated")),
            ai_translated=bool(attributes.get("ai_translated")),
            from_trusted=bool(attributes.get("from_trusted")),
            subtitle_format=str(attributes.get("format") or "srt").lower(),
            fps=float(attributes["fps"]) if attributes.get("fps") else None,
            moviehash_match=bool(attributes.get("moviehash_match")),
            season=feature.get("season_number") if isinstance(feature.get("season_number"), int) else None,
            episode=feature.get("episode_number") if isinstance(feature.get("episode_number"), int) else None,
            nb_cd=int(attributes.get("nb_cd") or len(files) or 1),
            foreign_parts_only=bool(attributes.get("foreign_parts_only")),
            url=attributes.get("url") if isinstance(attributes.get("url"), str) else None,
            extra={"file_id": int(file_id)},
        )

    def download(self, candidate: Candidate) -> DownloadResult:
        file_id = candidate.extra.get("file_id")
        if file_id is None:
            raise SourceError(
                f"Candidate {candidate.candidate_id} has no OpenSubtitles file id"
            )

        response = self._request("POST", "/download", json={"file_id": int(file_id)})
        self._check(response, "download")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(
                f"OpenSubtitles download returned invalid JSON: {exc}"
            ) from exc

        link = payload.get("link") if isinstance(payload, dict) else None
        if not isinstance(link, str) or not link:
            raise SourceError("OpenSubtitles download returned no file link")

        file_response = self._request("GET", link)
        self._check(file_response, "file download")

        remaining = payload.get("remaining")
        return DownloadResult(
            data=file_response.content,
            filename=str(payload.get("file_name") or candidate.filename),
            subtitle_format=str(payload.get("sub_format") or candidate.subtitle_format),
            remaining=remaining if isinstance(remaining, int) else None,
            reset_time_utc=(
                payload.get("reset_time_utc")
                if isinstance(payload.get("reset_time_utc"), str)
                else None
            ),
            requests=(
                payload.get("requests")
                if isinstance(payload.get("requests"), int)
                else None
            ),
        )
