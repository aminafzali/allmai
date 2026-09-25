"""Local-disk storage (MVP). Base dir from STORAGE_LOCAL_DIR.

Keys are namespaced (`workspaces/...`) and resolved strictly inside the
base dir — `..`, absolute paths and drive prefixes are rejected.
Writes are atomic (tmp file + rename).
"""

import os
from pathlib import Path

from app.core.config import get_settings


class PathTraversalError(ValueError):
    pass


class LocalStorageProvider:
    provider_name = "local"

    def __init__(self, base_dir: str | None = None) -> None:
        root = base_dir or get_settings().STORAGE_LOCAL_DIR
        self.base = Path(root).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        if not key or key.startswith(("/", "\\")) or ".." in Path(key).parts:
            raise PathTraversalError(f"unsafe storage key: {key!r}")
        if len(key) > 1024:
            raise PathTraversalError("storage key too long")
        target = (self.base / key).resolve()
        if target != self.base and self.base not in target.parents:
            raise PathTraversalError(f"storage key escapes base dir: {key!r}")
        return target

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        return key

    def get(self, key: str) -> bytes:
        target = self._resolve(key)
        if not target.is_file():
            raise FileNotFoundError(f"object not found: {key!r}")
        return target.read_bytes()

    def delete(self, key: str) -> None:
        target = self._resolve(key)
        try:
            target.unlink()
        except FileNotFoundError:
            pass

    def exists(self, key: str) -> bool:
        try:
            return self._resolve(key).is_file()
        except PathTraversalError:
            return False
