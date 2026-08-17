"""HTTP for uploading and serving files.

Files are always served through the API rather than from a public bucket URL.
A public URL on a licence scan would make the owner-only check decorative: the
link would work for anyone who ever saw it, forever, regardless of role.

Who may read what is decided in app/services/assets.py. This file reads
multipart bodies and sets headers.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response

from app.api.deps import AssetServiceDep, DbDep
from app.models import AssetKind, Staff
from app.schemas import AssetOut
from app.security import current_staff, require_owner
from app.services.assets import Upload

router = APIRouter(tags=["assets"])


def optional_staff(request: Request, db: DbDep) -> Staff | None:
    """Signed-in staff if there is a valid session, otherwise nobody.

    A photo has to be readable by a stranger and a document must not be, so
    this endpoint cannot simply demand a session — it has to tell them apart.
    """
    try:
        return current_staff(request, db)
    except HTTPException:
        return None


def _read(file: UploadFile) -> Upload:
    return Upload(
        filename=file.filename,
        content_type=file.content_type or "application/octet-stream",
        data=file.file.read(),
    )


@router.post("/api/professionals/{professional_id}/photo", response_model=AssetOut,
             status_code=status.HTTP_201_CREATED)
def upload_photo(
    professional_id: int,
    service: AssetServiceDep,
    file: UploadFile = File(...),
    staff: Staff = Depends(current_staff),
) -> AssetOut:
    """The photo buyers will see. Required before a profile can be published."""
    return AssetOut.model_validate(
        service.upload_photo(professional_id, _read(file), staff=staff)
    )


@router.post("/api/professionals/{professional_id}/files/{field_key}", response_model=AssetOut,
             status_code=status.HTTP_201_CREATED)
def upload_field_file(
    professional_id: int,
    field_key: str,
    service: AssetServiceDep,
    file: UploadFile = File(...),
    staff: Staff = Depends(current_staff),
) -> AssetOut:
    """A document answering a `file` field on this professional's profession."""
    return AssetOut.model_validate(
        service.upload_field_file(professional_id, field_key, _read(file), staff=staff)
    )


@router.get("/api/assets/{asset_id}")
def get_asset(
    asset_id: int,
    service: AssetServiceDep,
    staff: Staff | None = Depends(optional_staff),
) -> Response:
    asset, data = service.read(asset_id, staff=staff)
    return Response(
        content=data,
        media_type=asset.content_type or "application/octet-stream",
        headers={
            # Photos are on public pages and change rarely; documents must
            # never sit in a shared cache.
            "Cache-Control": "public, max-age=86400" if asset.kind is AssetKind.photo
                             else "private, no-store",
            "Content-Disposition": f'inline; filename="{asset.original_filename or "file"}"',
        },
    )


@router.delete("/api/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(
    asset_id: int,
    service: AssetServiceDep,
    staff: Staff = Depends(require_owner),
) -> None:
    service.delete(asset_id, staff=staff)
