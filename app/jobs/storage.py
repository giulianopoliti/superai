import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4


@dataclass(frozen=True)
class StoredUpload:
    key: str
    sha256: str
    size: int


class JobFileStorage:
    """Private durable files. Browser filenames are never used as storage paths."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        public = Path(__file__).resolve().parents[1] / "static"
        if self.root == public or public in self.root.parents:
            raise ValueError("Job storage must not be inside public assets")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.root.chmod(0o700)

    def path(self, key: str) -> Path:
        if str(UUID(key)) != key:
            raise ValueError("Invalid storage key")
        return self.root / (key + ".bin")

    def save(self, stream) -> StoredUpload:
        key = str(uuid4())
        target = self.path(key)
        temporary = target.with_suffix(".upload")
        digest = hashlib.sha256()
        size = 0
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as destination:
                while chunk := stream.read(1024 * 1024):
                    destination.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if size == 0:
                raise ValueError("El archivo está vacío.")
            os.replace(temporary, target)
            self.sync_directory()
        finally:
            temporary.unlink(missing_ok=True)
        return StoredUpload(key, digest.hexdigest(), size)

    def remove(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)
        self.sync_directory()

    def sync_directory(self) -> None:
        if os.name != "nt":
            descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
