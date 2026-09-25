"""Local storage tests: roundtrip, traversal rejection, factory default."""

import pytest

from app.core.config import get_settings
from app.storage.local import LocalStorageProvider, PathTraversalError
from app.storage.s3 import S3Storage, get_storage


@pytest.fixture()
def store(tmp_path):
    return LocalStorageProvider(base_dir=str(tmp_path / "data"))


def test_put_get_delete_exists(store):
    assert store.exists("workspaces/a/f.bin") is False
    store.put("workspaces/a/f.bin", b"\x00\x01binary")
    assert store.exists("workspaces/a/f.bin") is True
    assert store.get("workspaces/a/f.bin") == b"\x00\x01binary"
    store.delete("workspaces/a/f.bin")
    assert store.exists("workspaces/a/f.bin") is False
    with pytest.raises(FileNotFoundError):
        store.get("workspaces/a/f.bin")


def test_traversal_rejected(store):
    for bad in ["../evil", "..\\evil", "/abs/path", "a/../../b", "", "x" * 2000]:
        with pytest.raises(PathTraversalError):
            store.put(bad, b"x")
        assert store.exists(bad) is False


def test_protocol_parity():
    for cls in (LocalStorageProvider, S3Storage):
        for method in ("put", "get", "delete", "exists"):
            assert callable(getattr(cls, method, None)), f"{cls.__name__}.{method}"


def test_factory_defaults_to_local():
    if get_settings().STORAGE_BACKEND == "local":
        assert isinstance(get_storage(), LocalStorageProvider)
