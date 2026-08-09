from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class JsonFileCache:
    """Operational HTTP cache, deliberately separate from research storage."""

    def __init__(self, root: Path, ttl_seconds: int) -> None:
        self.root = root
        self.ttl_seconds = ttl_seconds

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def get(self, url: str) -> dict[str, Any] | None:
        path = self._path(url)
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        if time.time() - float(cached.get("cached_at", 0)) > self.ttl_seconds:
            return None
        payload = cached.get("payload")
        return payload if isinstance(payload, dict) else None

    def put(self, url: str, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self._path(url)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"cached_at": time.time(), "payload": payload}),
            encoding="utf-8",
        )
        temporary.replace(destination)

