"""Move photos that were stored as base64 into object storage.

Before the signing form uploaded its photo, it posted a `data:` URL and the
value was written onto `professionals.photo` verbatim. That column is selected
by every list response — the worklist returns fifty rows at a time — so a
handful of signed professionals put megabytes into a page that renders nothing
larger than a 40px avatar.

The code no longer produces these. This clears the ones already stored, so the
column holds nothing but `/api/assets/N` and the reader has one case to handle.

    uv run python -m scripts.migrate_photo_data_urls            # report only
    uv run python -m scripts.migrate_photo_data_urls --apply

Against production, with a bucket configured:

    VILAOW_ALLOW_HOSTED_DB=1 DATABASE_URL="$PRODUCTION_DATABASE_URL" \
      PUBLIC_SITE_URL=https://vilaow.com \
      uv run python -m scripts.migrate_photo_data_urls --apply

Safe to run twice: a row is only touched if its photo still begins with
`data:`, and the original value is left in the event log so a bad conversion
can be traced.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import re
import sys

from sqlalchemy import select

from app.adapters.storage.firebase import FirebaseStorage
from app.adapters.storage.local import LocalStorage
from app.config import get_settings
from app.db import SessionLocal
from app.models import Asset, AssetKind, Event, Professional
from app.services.assets import ALLOWED_PHOTO_TYPES, MAX_PHOTO_BYTES, _safe_key

# data:image/jpeg;base64,/9j/4AAQ...
DATA_URL = re.compile(r"^data:([\w.+-]+/[\w.+-]+);base64,(.+)$", re.S)


def _storage():
    """The same backend the app uses, chosen the same way.

    Deliberately not the FastAPI dependency: this runs without an app, and a
    migration that wrote somewhere other than where the app reads from would be
    worse than not running it at all.
    """
    settings = get_settings()
    if settings.firebase_bucket:
        return FirebaseStorage(settings.firebase_bucket,
                               settings.firebase_credentials_file or None)
    if settings.is_production:
        sys.exit("FIREBASE_BUCKET is unset. Refusing to write photos to a local disk "
                 "that production cannot read back.")
    from pathlib import Path
    return LocalStorage(Path(__file__).resolve().parents[1] / ".uploads")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes; without it, only report")
    args = parser.parse_args()

    storage = _storage()
    moved = failed = 0

    with SessionLocal() as db:
        rows = db.scalars(
            select(Professional).where(Professional.photo.like("data:%"))
        ).all()

        if not rows:
            print("Nothing to do: no photo column holds a data: URL.")
            return 0

        total = sum(len(p.photo) for p in rows)
        print(f"{len(rows)} professional(s) with an inline photo, "
              f"{total / 1024 / 1024:.1f}MB of column data.\n")

        for professional in rows:
            label = f"#{professional.id} {professional.business_name}"
            match = DATA_URL.match(professional.photo)
            if not match:
                print(f"  SKIP  {label}: data: URL is not base64-encoded")
                failed += 1
                continue

            content_type = match.group(1).lower()
            try:
                data = base64.b64decode(match.group(2), validate=True)
            except (binascii.Error, ValueError) as exc:
                print(f"  SKIP  {label}: undecodable ({exc})")
                failed += 1
                continue

            if content_type not in ALLOWED_PHOTO_TYPES:
                print(f"  SKIP  {label}: {content_type} is not an accepted photo type")
                failed += 1
                continue
            if not data or len(data) > MAX_PHOTO_BYTES:
                print(f"  SKIP  {label}: {len(data)} bytes is outside the photo limit")
                failed += 1
                continue

            print(f"  MOVE  {label}: {len(professional.photo) / 1024:.0f}KB of base64 "
                  f"-> {len(data) / 1024:.0f}KB {content_type}")
            if not args.apply:
                moved += 1
                continue

            stored = storage.put(
                data,
                key=_safe_key(professional.id, AssetKind.photo, content_type),
                content_type=content_type,
            )
            asset = Asset(
                professional_id=professional.id,
                kind=AssetKind.photo,
                storage_path=stored.path,
                content_type=stored.content_type,
                size_bytes=stored.size_bytes,
                original_filename="signed-photo.jpg",
            )
            db.add(asset)
            db.flush()
            professional.photo = f"/api/assets/{asset.id}"
            db.add(Event(
                professional_id=professional.id,
                actor_label="migration",
                kind="file_uploaded",
                detail=f"photo moved out of the professionals row into {stored.path}",
            ))
            moved += 1

        if args.apply:
            db.commit()

    verb = "moved" if args.apply else "would move"
    print(f"\n{verb} {moved}, skipped {failed}.")
    if not args.apply:
        print("Nothing was written. Re-run with --apply.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
