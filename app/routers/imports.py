"""Turn his spreadsheet into Professional rows.

app.importer already handles the sheet's grouped layout (region/city bands,
repeated headers) and knows which category text maps to which profession
key. This router's job is the database side: dedupe against what's already
there, dedupe within the batch itself, and leave an audit trail — one
ImportBatch summarising the run, one Event per row actually added.
"""
from __future__ import annotations

import io
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.importer import normalise_phone, parse_workbook
from app.models import Event, ImportBatch, Profession, Professional, Staff
from app.schemas import ImportBatchOut, ImportSkipBreakdown, ImportSummary
from app.security import current_staff

log = logging.getLogger("vilaow.imports")

router = APIRouter(prefix="/api/imports", tags=["imports"])


@router.post("", response_model=ImportSummary, status_code=status.HTTP_201_CREATED)
async def create_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    staff: Staff = Depends(current_staff),
) -> ImportSummary:
    raw = await file.read()
    try:
        result = parse_workbook(io.BytesIO(raw))
    except Exception as exc:  # openpyxl raises assorted errors on a bad file
        # The message is deliberately not the exception's. openpyxl reflects
        # internal paths and structure detail back to the caller, which tells
        # an attacker about the server and tells the owner nothing they can act
        # on. The real error goes to the log.
        log.warning("import: could not read %s: %s", file.filename, exc)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Could not read that file. It needs to be an .xlsx workbook saved by Excel "
            "or Google Sheets.",
        ) from exc

    professions_by_key = {p.key: p for p in db.scalars(select(Profession))}
    existing_phones = {
        normalise_phone(phone)
        for phone in db.scalars(select(Professional.phone).where(Professional.phone.is_not(None)))
    }

    batch = ImportBatch(filename=file.filename or "upload.xlsx", imported_by_id=staff.id, rows_seen=result.seen)
    db.add(batch)
    db.flush()  # need batch.id for the rows below

    seen_in_batch: set[str] = set()
    added = 0
    skipped_unmapped_profession = 0
    skipped_duplicate_existing = 0
    skipped_duplicate_batch = 0

    for row in result.rows:
        profession = professions_by_key.get(row.profession_key)
        if profession is None:
            # A category the importer recognises but that has no matching
            # Profession row yet (professions are owner-managed, separately).
            skipped_unmapped_profession += 1
            continue

        phone_key = normalise_phone(row.phone)
        if phone_key in existing_phones:
            skipped_duplicate_existing += 1
            continue
        if phone_key in seen_in_batch:
            skipped_duplicate_batch += 1
            continue
        seen_in_batch.add(phone_key)

        notes = " | ".join(part for part in (row.status, row.notes) if part) or None

        professional = Professional(
            business_name=row.business_name,
            phone=row.phone,
            address=row.address,
            city=row.city,
            region=row.region,
            profession_id=profession.id,
            rating=row.rating,
            review_count=row.review_count,
            source="Google" if row.rating is not None else None,
            batch_id=batch.id,
            notes=notes,
        )
        db.add(professional)
        db.flush()  # need professional.id for the Event below
        added += 1

        db.add(Event(
            professional_id=professional.id,
            actor_id=staff.id,
            actor_label=staff.name,
            kind="imported",
            detail=f"batch #{batch.id} ({batch.filename})",
        ))

    batch.rows_added = added
    batch.rows_skipped = (
        result.skipped_no_phone
        + result.skipped_unknown_category
        + skipped_unmapped_profession
        + skipped_duplicate_existing
        + skipped_duplicate_batch
    )
    batch.note = (
        f"no_phone={result.skipped_no_phone} unknown_category={result.skipped_unknown_category} "
        f"unmapped_profession={skipped_unmapped_profession} "
        f"duplicate_existing={skipped_duplicate_existing} duplicate_batch={skipped_duplicate_batch}"
    )

    db.commit()
    db.refresh(batch)

    return ImportSummary(
        batch=ImportBatchOut.model_validate(batch),
        skipped=ImportSkipBreakdown(
            no_phone=result.skipped_no_phone,
            unknown_category=result.skipped_unknown_category,
            unmapped_profession=skipped_unmapped_profession,
            duplicate_existing=skipped_duplicate_existing,
            duplicate_batch=skipped_duplicate_batch,
        ),
    )


@router.get("", response_model=list[ImportBatchOut])
def list_imports(db: Session = Depends(get_db), _staff: Staff = Depends(current_staff)) -> list[ImportBatchOut]:
    batches = db.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc())).all()
    return [ImportBatchOut.model_validate(b) for b in batches]
