from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .cache import JsonFileCache
from .config import Settings


class SecClientError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class SecClient:
    """Small fair-access client with cache, throttling, and bounded concurrency."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache = JsonFileCache(settings.cache_dir, settings.cache_ttl_seconds)
        self.semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0

    async def _throttle(self) -> None:
        async with self._rate_lock:
            interval = 1 / self.settings.requests_per_second
            delay = interval - (time.monotonic() - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()

    async def get_json(self, url: str) -> dict[str, Any]:
        cached = self.cache.get(url)
        if cached is not None:
            return cached
        user_agent = self.settings.validated_user_agent()
        async with self.semaphore:
            await self._throttle()
            try:
                async with httpx.AsyncClient(
                    timeout=self.settings.timeout_seconds,
                    headers={
                        "User-Agent": user_agent,
                        "Accept-Encoding": "gzip, deflate",
                        "Accept": "application/json",
                    },
                    follow_redirects=True,
                ) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    payload = response.json()
            except httpx.TimeoutException as exc:
                raise SecClientError("timeout", "SEC request timed out", True) from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                raise SecClientError(
                    f"http_{status}",
                    f"SEC returned HTTP {status}",
                    status in {429, 500, 502, 503, 504},
                ) from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise SecClientError("invalid_response", "SEC response was unavailable or invalid", True) from exc
        if not isinstance(payload, dict):
            raise SecClientError("invalid_response", "SEC response was not a JSON object")
        self.cache.put(url, payload)
        return payload

