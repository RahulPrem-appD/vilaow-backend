"""What may become a professional's public photo.

One rule, in one place, because it was previously enforced on one of the two
paths that write this column.

The signing endpoint was hardened when the photo upload moved into object
storage: the value has to name an asset we stored, for that professional. The
staff `PATCH /api/professionals/{id}` was not, and it takes `photo` as a free
string — which the caller's own call form still exposes as an editable field.
So a caller could put an arbitrary external URL on a public profile, and it
would be served from a page carrying Vilaow's name, fetching from a host nobody
here controls. A few hundred kilobytes of base64 in the same column would ship
in every list response, fifty rows at a time.

Both callers now go through `photo_reference`. It is a service-layer rule
rather than a domain one because deciding whether an asset exists and belongs to
this professional needs the database; `parse_asset_reference` is the pure half.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.domain.errors import Invalid
from app.models import Asset, AssetKind, Professional

# `/api/assets/12` — the only shape a stored photo reference ever takes.
_ASSET_REFERENCE = re.compile(r"^/api/assets/(\d+)$")


def parse_asset_reference(value: str) -> int | None:
    """The asset id in a stored reference, or None if it is not one."""
    match = _ASSET_REFERENCE.match(value.strip())
    return int(match.group(1)) if match else None


def photo_reference(db: Session, professional: Professional, value: str) -> str:
    """Validate a photo value, returning the reference to store.

    Refuses anything that is not an asset we hold for this professional:
    an external URL, a data: URL, a document (a licence scan must never become
    a public photo), a deleted asset, or another professional's photo.
    """
    asset_id = parse_asset_reference(value)
    if asset_id is not None:
        asset = db.get(Asset, asset_id)
        if (
            asset is not None
            and asset.deleted_at is None
            and asset.kind is AssetKind.photo
            and asset.professional_id == professional.id
        ):
            return f"/api/assets/{asset.id}"

    raise Invalid(
        "A photo has to be uploaded first — POST the file to "
        "/api/professionals/{id}/photo and store the reference it returns."
    )
