"""Small JSON file cache for research tools.

Search and page-fetch results are cached on disk so repeated agent runs do not
re-hit free APIs or re-download the same page. The cache is deliberately simple:
one JSON file per key, atomic writes, and a TTL. A corrupt or unreadable entry is
treated as a miss rather than an error.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FileCache:
    """A tiny on-disk JSON cache with a time-to-live."""

    def __init__(self, directory: str | Path, ttl_seconds: int):
        self.directory = Path(directory)
        self.ttl_seconds = max(0, int(ttl_seconds))

    @property
    def enabled(self) -> bool:
        return self.ttl_seconds > 0

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        path = self._path(key)
        try:
            if not path.exists():
                return None
            age = time.time() - path.stat().st_mtime
            if age > self.ttl_seconds:
                path.unlink(missing_ok=True)
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.debug("Cache read failed for %s: %s", path.name, exc)
            return None

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temp.write_text(
                json.dumps(value, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            os.replace(temp, path)
        except (OSError, TypeError) as exc:
            logger.debug("Cache write failed for %s: %s", path.name, exc)
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def clear(self) -> int:
        """Remove every cached entry; returns the number of files deleted."""
        if not self.directory.exists():
            return 0
        deleted = 0
        for path in self.directory.glob("*.json"):
            try:
                path.unlink()
                deleted += 1
            except OSError:
                continue
        return deleted
