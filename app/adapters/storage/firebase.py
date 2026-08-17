"""Firebase Storage, which is Google Cloud Storage underneath.

The SDK is imported lazily so the app still starts — and the whole test suite
still runs — on a machine with no Firebase credentials and no SDK installed.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

from app.domain.errors import StorageFailure
from app.ports.storage import Stored

log = logging.getLogger("vilaow.storage")


@contextmanager
def _as_storage_failure(what: str):
    """Every escape route out of this adapter is a StorageFailure.

    Without this, a bucket outage, a revoked credential or a network blip came
    out of the SDK as its own exception type, sailed past the handlers that
    exist precisely for this, and reached the client as a bare 500 with a
    traceback. On the public photo route that is a broken profile page for
    every visitor during an outage — and no Event recording that it happened.

    The domain error is what the rest of the app is written against: assets.py
    turns a failed read into a 404, and a failed delete into a completed
    erasure with the record still written.
    """
    try:
        yield
    except StorageFailure:
        raise
    except Exception as exc:                  # noqa: BLE001 — deliberate boundary
        log.exception("storage: %s failed", what)
        raise StorageFailure(f"storage {what} failed: {exc}") from exc


class FirebaseStorage:
    def __init__(self, bucket_name: str, credentials_file: str | None = None) -> None:
        try:
            from google.cloud import storage as gcs  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise StorageFailure(
                "google-cloud-storage is not installed — add it to pyproject "
                "dependencies to use the Firebase backend"
            ) from exc

        client = (
            gcs.Client.from_service_account_json(credentials_file)
            if credentials_file
            # Falls back to GOOGLE_APPLICATION_CREDENTIALS / workload identity.
            else gcs.Client()
        )
        self._bucket = client.bucket(bucket_name)
        log.info("storage: firebase bucket %s", bucket_name)

    def put(self, data: bytes, *, key: str, content_type: str) -> Stored:
        with _as_storage_failure("upload"):
            blob = self._bucket.blob(key)
            blob.upload_from_string(data, content_type=content_type)
        # Deliberately not made public. Reads go back through the API so the
        # owner-only rule on documents is actually enforced; a public bucket
        # URL would work for anyone who ever saw it, forever.
        return Stored(path=key, content_type=content_type, size_bytes=len(data))

    def get(self, path: str) -> bytes:
        with _as_storage_failure("read"):
            blob = self._bucket.blob(path)
            if not blob.exists():
                raise StorageFailure("file not found")
            return blob.download_as_bytes()

    def delete(self, path: str) -> None:
        with _as_storage_failure("delete"):
            blob = self._bucket.blob(path)
            if blob.exists():
                blob.delete()
