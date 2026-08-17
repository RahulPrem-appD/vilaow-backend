"""Local disk. Development only.

app/services/assets.py refuses to select this in production, because Render's
filesystem is ephemeral: every photo and licence scan would disappear on the
next deploy, silently, and only be noticed weeks later as a broken image.
"""
from __future__ import annotations

from pathlib import Path

from app.domain.errors import StorageFailure
from app.ports.storage import Stored


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        target = (self.root / path).resolve()
        # Refuse anything that escapes the root, however it was spelled. Keys
        # are generated rather than taken from the browser, so this should be
        # unreachable — which is precisely why it is cheap to keep.
        if not target.is_relative_to(self.root.resolve()):
            raise StorageFailure("that path escapes the storage root")
        return target

    def put(self, data: bytes, *, key: str, content_type: str) -> Stored:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return Stored(path=key, content_type=content_type, size_bytes=len(data))

    def get(self, path: str) -> bytes:
        target = self._resolve(path)
        if not target.exists():
            raise StorageFailure("file not found")
        return target.read_bytes()

    def delete(self, path: str) -> None:
        target = self._resolve(path)
        if target.exists():
            target.unlink()
