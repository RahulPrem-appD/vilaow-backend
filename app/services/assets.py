"""Uploading files, and deciding who may read them back.

The rules moved here from storage.py deliberately. A storage backend's job is
bytes in, bytes out; whether a 20MB executable is an acceptable profile photo
is a business decision, and business decisions do not belong in the thing that
talks to a bucket. Swapping Firebase for S3 should not risk changing what the
product accepts.

Two kinds, with different access rules:

  * **photo** — ends up on a public profile, so anyone may read it.
  * **document** — a licence scan or insurance certificate. Owner-only, even
    for the caller who uploaded it, and answered with 404 rather than 403 so
    that probing ids cannot reveal which documents exist.
"""
from __future__ import annotations

import mimetypes
import secrets
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.errors import Invalid, NotFound, StorageFailure
from app.models import Asset, AssetKind, Event, FieldType, Professional, ProfessionField, Role, Staff
from app.ports.clock import Clock
from app.ports.storage import StorageBackend

# Generous for a photo, mean enough that nobody uploads a video by accident.
MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_DOCUMENT_TYPES = ALLOWED_PHOTO_TYPES | {"application/pdf"}


@dataclass(frozen=True)
class Upload:
    filename: str | None
    content_type: str
    data: bytes


def _safe_key(professional_id: int, kind: AssetKind, content_type: str) -> str:
    """A generated name, never the one the browser sent.

    An upload filename is attacker-controlled: it can carry path separators,
    null bytes, or a second extension. None of it is needed — the original is
    kept on the row for display, and this is only the key.
    """
    suffix = mimetypes.guess_extension(content_type) or ""
    if suffix == ".jpe":
        suffix = ".jpg"
    return f"professionals/{professional_id}/{kind.value}/{secrets.token_urlsafe(16)}{suffix}"


class AssetService:
    def __init__(self, db: Session, *, storage: StorageBackend, clock: Clock) -> None:
        self._db = db
        self._storage = storage
        self._clock = clock

    def _professional(self, professional_id: int) -> Professional:
        professional = self._db.get(Professional, professional_id)
        if professional is None:
            raise NotFound("Professional not found")
        return professional

    def _store(self, professional: Professional, upload: Upload, kind: AssetKind,
               field_key: str | None, staff: Staff | None,
               actor_label: str | None = None) -> Asset:
        allowed = ALLOWED_PHOTO_TYPES if kind is AssetKind.photo else ALLOWED_DOCUMENT_TYPES
        limit = MAX_PHOTO_BYTES if kind is AssetKind.photo else MAX_DOCUMENT_BYTES

        if upload.content_type not in allowed:
            raise Invalid(f"{upload.content_type} is not an accepted file type")
        if not upload.data:
            raise Invalid("the file is empty")
        if len(upload.data) > limit:
            raise Invalid(f"the file is larger than {limit // (1024 * 1024)}MB")

        stored = self._storage.put(
            upload.data,
            key=_safe_key(professional.id, kind, upload.content_type),
            content_type=upload.content_type,
        )

        asset = Asset(
            professional_id=professional.id,
            kind=kind,
            field_key=field_key,
            storage_path=stored.path,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            original_filename=upload.filename,
            uploaded_by_id=staff.id if staff else None,
        )
        self._db.add(asset)
        self._db.add(Event(
            professional_id=professional.id,
            actor_id=staff.id if staff else None,
            # The professional uploading their own photo while signing has no
            # staff account, so the label carries who it was instead.
            actor_label=actor_label or (staff.name if staff else "professional (signing)"),
            kind="file_uploaded",
            detail=f"{kind.value}{f' ({field_key})' if field_key else ''}: {upload.filename}",
        ))
        self._db.commit()
        self._db.refresh(asset)
        return asset

    def upload_photo(self, professional_id: int, upload: Upload, *,
                     staff: Staff | None = None, actor_label: str | None = None) -> Asset:
        """Used by both photo paths.

        A caller uploads on behalf of someone on the phone; a professional
        uploads their own while signing, with a token rather than a session.
        Both must end up in object storage — the signing path used to embed a
        base64 data: URL in the professionals row instead, which put a few
        hundred kilobytes into every list response that selects the column.
        """
        professional = self._professional(professional_id)
        asset = self._store(professional, upload, AssetKind.photo, None, staff,
                            actor_label=actor_label)
        # The column the public serialiser reads points at the endpoint, so
        # replacing a photo is one write and old files stay addressable.
        professional.photo = f"/api/assets/{asset.id}"
        self._db.commit()
        return asset

    def upload_field_file(
        self, professional_id: int, field_key: str, upload: Upload, *, staff: Staff,
    ) -> Asset:
        professional = self._professional(professional_id)
        field = self._db.scalar(
            select(ProfessionField).where(
                ProfessionField.profession_id == professional.profession_id,
                ProfessionField.key == field_key,
                ProfessionField.active.is_(True),
            )
        )
        if field is None:
            raise NotFound("This profession has no such field")
        if field.type is not FieldType.file:
            raise Invalid(f"{field.label} does not take a file")

        asset = self._store(professional, upload, AssetKind.document, field_key, staff)
        # The answer to a file field is the asset id — see domain/fields.coerce.
        professional.custom = {**(professional.custom or {}), field_key: asset.id}
        self._db.commit()
        return asset

    def read(self, asset_id: int, *, staff: Staff | None) -> tuple[Asset, bytes]:
        asset = self._db.get(Asset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise NotFound("Not found")

        if asset.kind is AssetKind.document and (staff is None or staff.role is not Role.owner):
            # 404, not 403 — a caller probing ids should not learn which
            # documents exist.
            raise NotFound("Not found")

        try:
            return asset, self._storage.get(asset.storage_path)
        except StorageFailure:
            raise NotFound("Not found") from None

    def delete(self, asset_id: int, *, staff: Staff) -> None:
        """Erasure. Nothing expires on its own, so deletion has to be a real,
        auditable action rather than a row quietly disappearing."""
        asset = self._db.get(Asset, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise NotFound("Not found")

        try:
            self._storage.delete(asset.storage_path)
        except StorageFailure:
            # The bytes may already be gone; the record of the deletion still
            # matters, so this is not fatal.
            pass

        asset.deleted_at = self._clock.now()

        professional = self._db.get(Professional, asset.professional_id)
        if professional is not None:
            if asset.kind is AssetKind.photo and professional.photo == f"/api/assets/{asset.id}":
                professional.photo = None
            if asset.field_key and (professional.custom or {}).get(asset.field_key) == asset.id:
                professional.custom = {
                    k: v for k, v in (professional.custom or {}).items() if k != asset.field_key
                }

        self._db.add(Event(
            professional_id=asset.professional_id,
            actor_id=staff.id,
            actor_label=staff.name,
            kind="file_deleted",
            detail=f"{asset.kind.value}: {asset.original_filename or asset.storage_path}",
        ))
        self._db.commit()
