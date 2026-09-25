"""StorageProvider protocol: files live behind OUR backend, never public.

MVP backend is the server's local disk (LocalStorageProvider). S3-compatible
storage implements the same protocol for later. Access is always gated by
workspace membership in the API layer (`sources/{id}/download`).
"""

from typing import Protocol


class StorageProvider(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        ...

    def get(self, key: str) -> bytes:
        ...

    def delete(self, key: str) -> None:
        ...

    def exists(self, key: str) -> bool:
        ...
