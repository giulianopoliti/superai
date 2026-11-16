import hashlib
import io
import os

import pytest


def test_private_atomic_upload_and_digest(storage):
    content = b"%PDF" * 400000
    upload = storage.save(io.BytesIO(content))
    assert upload.sha256 == hashlib.sha256(content).hexdigest()
    assert storage.path(upload.key).read_bytes() == content
    assert not list(storage.root.glob("*.upload"))
    if os.name != "nt":
        assert storage.path(upload.key).stat().st_mode & 0o777 == 0o600


def test_empty_and_interrupted_uploads_never_leave_partial_files(storage):
    with pytest.raises(ValueError):
        storage.save(io.BytesIO(b""))

    class BrokenStream:
        def read(self, _):
            raise OSError("Interrupted upload")

    with pytest.raises(OSError):
        storage.save(BrokenStream())
    assert list(storage.root.iterdir()) == []


@pytest.mark.parametrize(
    "key", ["../secret", "/etc/passwd", "x", "../00000000-0000-0000-0000-000000000000"]
)
def test_storage_keys_cannot_select_arbitrary_paths(storage, key):
    with pytest.raises(ValueError):
        storage.path(key)
