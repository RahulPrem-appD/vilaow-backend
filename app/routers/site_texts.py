"""HTTP for the corrections to the website's text (change request 10).

The website reads one language's corrections publicly: they are words on public
pages, and nothing else is in the row it sends. Any staff member may list them;
only an owner may change one, as with the professions' lists, because a
correction changes what every visitor reads.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select

from app.api.deps import DbDep, StaffDep
from app.domain.errors import Invalid
from app.models import SiteText, Staff
from app.schemas.site_texts import PublicSiteTexts, SiteLocale, SiteTextIn, SiteTextOut
from app.security import require_owner

router = APIRouter(prefix="/api/site-texts", tags=["site-texts"])
public_router = APIRouter(prefix="/api/public/site-texts", tags=["public"])


def _out(row: SiteText, names: dict[int, str]) -> SiteTextOut:
    out = SiteTextOut.model_validate(row)
    out.updated_by = names.get(row.updated_by_id) if row.updated_by_id else None
    return out


@public_router.get("", response_model=PublicSiteTexts)
def public_texts(locale: SiteLocale, db: DbDep, response: Response) -> PublicSiteTexts:
    rows = db.scalars(select(SiteText).where(SiteText.locale == locale))
    # The website caches these for a minute too; a correction shows within it.
    response.headers["Cache-Control"] = "public, max-age=60"
    return PublicSiteTexts(locale=locale, texts={row.key: row.text for row in rows})


@router.get("", response_model=list[SiteTextOut])
def list_texts(db: DbDep, _staff: StaffDep) -> list[SiteTextOut]:
    rows = list(db.scalars(select(SiteText).order_by(SiteText.key, SiteText.locale)))
    names = {s.id: s.name for s in db.scalars(select(Staff))}
    return [_out(row, names) for row in rows]


@router.put("", response_model=SiteTextOut)
def save_text(
    payload: SiteTextIn, db: DbDep, staff: Staff = Depends(require_owner),
) -> SiteTextOut:
    if payload.locale != "en" and not payload.source_hash:
        # Without it the admin cannot tell later whether the English moved on.
        raise Invalid("A translation must say which English it was written against.")
    row = db.scalar(
        select(SiteText).where(SiteText.key == payload.key, SiteText.locale == payload.locale)
    )
    if row is None:
        row = SiteText(key=payload.key, locale=payload.locale)
        db.add(row)
    row.text = payload.text
    row.source_hash = payload.source_hash if payload.locale != "en" else None
    row.updated_by_id = staff.id
    db.commit()
    db.refresh(row)
    return _out(row, {staff.id: staff.name})


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def remove_text(
    key: str, locale: SiteLocale, db: DbDep, _staff: Staff = Depends(require_owner),
) -> Response:
    """Back to the stored translation, or the English. Removing nothing is fine."""
    row = db.scalar(select(SiteText).where(SiteText.key == key, SiteText.locale == locale))
    if row is not None:
        db.delete(row)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
