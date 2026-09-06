"""What the public website may read.

Deliberately a separate router with its own response models rather than reusing
the admin ones. The admin serialisers carry a professional's direct phone
number, their notes, their pipeline stage and who is calling them — none of
which belongs on a public page. Sharing a schema between the two would mean one
forgotten field is a data leak, so there is no shared schema to forget.

Three rules hold everywhere in this file:
  * only published records are visible, ever
  * a rating is never returned without the source it came from, and an
    imported Google figure is never returned without the date it was read
  * a field is returned only when its key is visible for that record
    (app/domain/visibility.py: the professional's own list, else its
    profession's, else the default) — absent from the listing card and the
    profile alike, because hidden in one place and shown in the other would be
    a half-measure. The decision itself never travels in a response: which
    fields a buyer does not see is an editorial matter, not something a buyer
    needs to read. The badges are the one effect of that decision which
    legitimately does reach a buyer — a badge is itself the public claim — and
    even they arrive as plain words in `badges`, never as the keys or the
    list that chose them.

A published rating has two possible origins. When a record carries the whole
imported Google set — the stars, the review count and `rating_captured_on`, the
day someone read them off the listing — that is what the page shows, attributed
to "Google" and dated. When any of the three is missing the rating is computed
from the review rows this site holds, exactly as it was before, and no date
goes out with it. Either way the `rating` visibility key gates the whole set,
and no number travels without an attribution.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.fields import public_values
from app.domain.visibility import resolve
from app.models import (
    Introduction, IntroOutcome, Profession, Professional, ProfessionField, Review, ReviewKind,
)

router = APIRouter(prefix="/api/public", tags=["public"])


class PublicProfession(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    key: str
    label: str
    plural: str
    hint: str | None = None


class PublicReview(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    author: str
    stars: int
    text: str | None = None
    context: str | None = None
    source: str | None = None
    # Google-sourced and Vilaow-verified reviews must be visibly different on
    # the page. Only the second may be called verified: agreement clause 4
    # ("cannot be bought, edited or removed on request") is enforceable for
    # reviews Vilaow controls, and not for content Google owns.
    kind: str = "google"
    verified: bool = False


class PublicFieldValue(BaseModel):
    """One owner-defined answer the owner marked public."""
    key: str
    label: str
    type: str
    value: object


class PublicCard(BaseModel):
    """The listing card. No phone, no email — a buyer asks for a callback."""
    model_config = ConfigDict(from_attributes=True)
    slug: str
    name: str
    profession: str | None = None
    profession_key: str | None = None
    city: str | None = None
    region: str | None = None
    photo: str | None = None
    initials: str
    rating: float | None = None
    review_count: int | None = None
    rating_source: str | None = None
    # The day the published figure was read off Google, as an ISO date. Only
    # ever set when the imported Google set is what is being shown: a rating
    # computed from our own review rows has the rows themselves behind it and
    # needs no separate date. The card carries it as well as the profile, so
    # the directory can date the same number it prints.
    rating_captured_on: date | None = None
    years: int | None = None
    languages: list[str] | None = None
    # The year Vilaow vetted them, which his card prints as
    # "Verified by Vilaow · 2026". It was already on the record and simply not
    # sent, so the directory could not draw the chip his design has.
    verified_year: int | None = None
    # The trust badges this record may print, as their public words — one per
    # badge_* key in the resolved visible set, and an empty list when none is
    # switched on. The badge keys carry no column: the ticked key is itself
    # the fact, so the word is all there is to publish.
    badges: list[str] = []


class PublicProfile(PublicCard):
    subrole: str | None = None
    coverage: str | None = None
    bio: str | None = None
    specialties: list[str] | None = None
    # The chips under the headline — short selling points, not sentences. The
    # card does not carry them: a buyer skims the directory and reads the
    # profile.
    highlights: list[str] | None = None
    education: str | None = None
    costs: list | None = None
    cost_note: str | None = None
    faq: list | None = None
    reviews: list[PublicReview] = []
    # Owner-defined answers, already filtered to the public ones. Built from
    # the field definitions rather than from the stored keys, so a value with
    # no public definition behind it cannot appear here however it got saved.
    details: list[PublicFieldValue] = []
    # The two columns that have never been published. They are fields like the
    # others now, but their keys are not in the default set, so they reach a
    # buyer only when somebody deliberately switches them on — null otherwise.
    # They never appear on a listing card: a buyer who wants the numbers is
    # reading the profile, not skimming the directory.
    license: str | None = None
    vat_number: str | None = None


def _initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p[:1].isalpha()]
    return "".join(p[0].upper() for p in parts[:2]) or "V"


def _visible(p: Professional) -> frozenset[str]:
    """The field keys this one record may publish.

    `resolve` is the whole rule: the professional's own list when it has one,
    else its profession's, else the default. The profession is already
    reachable from the row — `_card` reads its label in the same breath — so
    resolving adds no query this page was not already issuing, in the listing
    as on the profile.
    """
    return resolve(
        p.visible_fields,
        p.profession.visible_fields if p.profession else None,
    )


# The three badge keys and the words they publish, in the order the card
# prints them. A badge key has no column behind it — the ticked key is the
# whole of the fact — so publishing one is turning the key into its word
# here, and this table is the only place that happens.
_TRUST_BADGES: tuple[tuple[str, str], ...] = (
    ("badge_licensed", "licensed"),
    ("badge_insured", "insured"),
    ("badge_interviewed", "interviewed"),
)


def _badges(visible: frozenset[str]) -> list[str]:
    """The badge words this record may print, in the card's order — never the
    order the keys happened to be stored in."""
    return [word for key, word in _TRUST_BADGES if key in visible]


def _card(p: Professional, visible: frozenset[str]) -> dict:
    # The public name is the person if we have one, the firm otherwise. His
    # spreadsheet lists businesses, and a buyer wants to know who they will
    # actually speak to.
    #
    # The rating is deliberately absent here: which of its two origins applies
    # is settled by _visible_rating, and each caller supplies the answer. The
    # listing holds the review rows as one aggregate and the profile holds them
    # loaded, so neither can do the other's work.
    #
    # A key that is not visible comes back None. It never changes another
    # field's value, and never turns a None into a value — withholding is the
    # only direction this moves in.
    name = p.contact_name or p.business_name
    return {
        "slug": p.slug,
        "name": name,
        "profession": p.profession.label if p.profession else None,
        "profession_key": p.profession.key if p.profession else None,
        "city": p.city,
        "region": p.region,
        "photo": p.photo if "photo" in visible else None,
        "initials": _initials(name),
        "years": p.years if "years" in visible else None,
        "languages": p.languages if "languages" in visible else None,
        "verified_year": p.verified_year if "verified_year" in visible else None,
        "badges": _badges(visible),
    }


def _imported_rating(
    p: Professional,
) -> tuple[float | None, int | None, str | None, date | None]:
    """The Google figure as imported, when there is a date standing behind it.

    These columns were withheld from public pages for a long time, and the
    reason was sound: the import carried whatever the spreadsheet said, so a
    record could claim 33 reviews while the page showed two, and stars nobody
    can account for should not be printed. The missing piece was never the
    number, it was the day someone read it. `rating_captured_on` supplies that,
    so the whole set is published together or not at all — the buyer sees the
    figure Google shows and can see how old it is.

    A review count of zero is treated as missing rather than published. "4.9
    from 0 reviews" is not something anybody read off a listing.
    """
    if p.rating is None or not p.review_count or p.rating_captured_on is None:
        return None, None, None, None
    return round(float(p.rating), 1), p.review_count, "Google", p.rating_captured_on


def _published_rating(
    p: Professional, count: int | None, average: float | None, sources: list[str | None],
) -> tuple[float | None, int | None, str | None, date | None]:
    """The rating, its count, its attribution, and the date behind it if any.

    The complete imported set wins, because it is the figure the buyer would
    find on Google themselves and the profile is meant to show that rather than
    a tally of whoever happened to be typed in here. Everything below it is the
    older rule, unchanged, and it still runs for every record without a capture
    date: the rating is computed from the review rows this site holds, and no
    date goes out with it.

    The file's standing rule decides the empty cases: a number never travels
    without saying whose it is. With no rows there is no number, and with rows
    but no source on any of them there is nobody to attribute the stars to —
    in both, everything comes back None and the page omits the rating rather
    than print stars it cannot account for.

    Rows that do carry a source are counted and averaged together with any
    that do not, so the count stays the number of reviews the page actually
    shows. That only matters for rows written by hand: both code paths that
    create a review set a source, so a sourceless row is legacy data, and one
    sitting beside sourced ones is a record to correct rather than a case to
    model here.
    """
    imported = _imported_rating(p)
    if imported[0] is not None:
        return imported
    if not count or average is None:
        return None, None, None, None
    attributed = sorted({s for s in sources if s})
    if not attributed:
        return None, None, None, None
    if len(attributed) == 1:
        source = attributed[0]
    else:
        # Two provenances in one average. Naming only one would let the other
        # platform's stars pass as the named one's, so the page says both.
        source = "Google and Vilaow buyers"
    return round(average, 1), count, source, None


def _visible_rating(
    visible: frozenset[str], p: Professional, count: int | None, average: float | None,
    sources: list[str | None],
) -> tuple[float | None, int | None, str | None, date | None]:
    """The published rating, or nothing at all when `rating` is not visible.

    Blanket rather than partial on purpose: the stars, the count, the
    attribution and the capture date are one claim, so one key takes all of
    them and never leaves a number standing without the count that sizes it,
    the source that legitimises it or the date that ages it. Both readers go
    through here so the listing and the profile cannot drift apart on what a
    hidden rating means.
    """
    if "rating" not in visible:
        return None, None, None, None
    return _published_rating(p, count, average, sources)


class PublicStats(BaseModel):
    """The homepage numbers, computed rather than asserted.

    His design ships placeholder copy — "300+ vetted professionals", "2,400+
    foreign purchases supported", "4.8 average verified rating" — against 4
    published profiles and no tracked purchases at all. Publishing those is a
    false claim to consumers, and in the EU that engages the Unfair Commercial
    Practices Directive rather than merely looking silly.

    So every figure here comes from the database, and any figure with nothing
    real behind it comes back as None for the page to omit entirely. A stat
    that cannot be substantiated does not get shown.
    """
    model_config = ConfigDict(from_attributes=True)
    vetted_professionals: int | None = None
    purchases_supported: int | None = None
    average_rating: float | None = None
    # Only true when the average is built from reviews Vilaow itself collected
    # from buyers it introduced. Google's numbers are not ours to call verified.
    rating_is_verified: bool = False
    rating_count: int | None = None


@router.get("/stats", response_model=PublicStats)
def public_stats(db: Session = Depends(get_db)) -> PublicStats:
    professionals = db.scalar(
        select(func.count()).select_from(Professional).where(Professional.published.is_(True))
    ) or 0

    # A purchase we can actually evidence: an introduction a caller closed as
    # the buyer having gone ahead.
    purchases = db.scalar(
        select(func.count()).select_from(Introduction)
        .where(Introduction.outcome == IntroOutcome.buyer_proceeded)
    ) or 0

    verified = db.execute(
        select(func.avg(Review.stars), func.count())
        .where(Review.kind == ReviewKind.vilaow_verified)
    ).one()

    average, count = (float(verified[0]), int(verified[1])) if verified[0] is not None else (None, 0)

    return PublicStats(
        vetted_professionals=professionals or None,
        purchases_supported=purchases or None,
        average_rating=round(average, 1) if average is not None else None,
        rating_is_verified=count > 0,
        rating_count=count or None,
    )


@router.get("/professions", response_model=list[PublicProfession])
def list_professions(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Profession).where(Profession.active.is_(True)).order_by(Profession.position)
    ).all()
    return [PublicProfession.model_validate(r) for r in rows]


@router.get("/professionals")
def list_professionals(
    region: str | None = None,
    role: str | None = Query(None, description="profession key"),
    city: str | None = None,
    language: str | None = Query(None, description="a language the professional speaks"),
    # Bounded at both ends. Postgres rejects a negative LIMIT, so `?limit=-1`
    # on a public endpoint was an unhandled 500 rather than a 422.
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Professional).where(Professional.published.is_(True))
    if region:
        stmt = stmt.where(Professional.region == region)
    if city:
        stmt = stmt.where(Professional.city == city)
    if role:
        stmt = stmt.join(Profession).where(Profession.key == role)
    if language:
        # Region, profession and language are the three filters a buyer gets.
        # Language is a core column rather than an owner-defined field for
        # exactly this reason: it has to work across every profession, so it
        # cannot live in a form the owner might not add to Notary.
        stmt = stmt.where(Professional.languages.any(language))

    # The total is counted from the professionals-only statement, before any
    # reviews are joined, so it stays a count of people and not of the
    # arithmetic attached to them.
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    # One aggregate over reviews rather than a query per professional. The
    # directory is the hot page, and the grouping keeps every card's rating to
    # one joined row: the average, the count, and the distinct sources —
    # array_agg(distinct ...) so the attribution arrives in the same pass.
    per_professional = select(
        Review.professional_id.label("professional_id"),
        func.avg(Review.stars).label("average"),
        func.count().label("review_count"),
        func.array_agg(distinct(Review.source)).label("sources"),
    ).group_by(Review.professional_id).subquery()

    rows = db.execute(
        stmt.add_columns(
            per_professional.c.average,
            per_professional.c.review_count,
            per_professional.c.sources,
        )
        .outerjoin(per_professional,
                   per_professional.c.professional_id == Professional.id)
        # Ordered by the number the buyer sees, not the imported column that
        # used to stand in for it; `id` keeps the order stable when averages
        # tie or are missing entirely.
        #
        # Visibility deliberately does not reach this ORDER BY, so a
        # professional whose rating is hidden still sorts by the same average
        # as everyone else. Where a record sits on the page is not a number
        # the page prints; the order is not a published claim.
        .order_by(per_professional.c.average.desc().nullslast(), Professional.id)
        .limit(limit).offset(offset)
    ).all()

    items = []
    for p, average, count, sources in rows:
        visible = _visible(p)
        card = _card(p, visible)
        (card["rating"], card["review_count"], card["rating_source"],
         card["rating_captured_on"]) = _visible_rating(
            visible, p, count, float(average) if average is not None else None, sources or []
        )
        items.append(card)
    return {"total": total, "items": items}


@router.get("/professionals/{slug}", response_model=PublicProfile)
def get_professional(slug: str, db: Session = Depends(get_db)):
    p = db.scalar(
        select(Professional).where(
            Professional.slug == slug, Professional.published.is_(True)
        )
    )
    if p is None:
        # 404 whether the record is missing or merely unpublished. Telling the
        # difference would let anyone enumerate who is in the pipeline.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    reviews = db.scalars(
        select(Review).where(Review.professional_id == p.id).order_by(Review.created_at.desc())
    ).all()

    visible = _visible(p)

    # The rating this list would produce, averaged and attributed — the same
    # computation the listing does in one aggregate query, done here in Python
    # because the rows are already loaded for the page below. It is only used
    # when the record has no imported Google figure to publish instead, which
    # is _published_rating's decision rather than this call site's.
    rating, review_count, rating_source, rating_captured_on = _visible_rating(
        visible, p,
        len(reviews),
        sum(r.stars for r in reviews) / len(reviews) if reviews else None,
        [r.source for r in reviews],
    )

    # Only the fields the owner marked public, in his display order. Note this
    # reads the *definitions* and looks values up, never the reverse — see
    # app/fields.public_values for why that direction is the safe one.
    fields = db.scalars(
        select(ProfessionField).where(
            ProfessionField.profession_id == p.profession_id,
            ProfessionField.active.is_(True),
            ProfessionField.public.is_(True),
        )
    ).all() if p.profession_id else []

    # The profile-only fields, each withheld under its own key — except
    # costs, where the note travels with the list because a note annotating
    # prices the page no longer shows would be a note about nothing.
    return PublicProfile(
        **_card(p, visible),
        rating=rating,
        review_count=review_count,
        rating_source=rating_source,
        rating_captured_on=rating_captured_on,
        subrole=p.subrole if "subrole" in visible else None,
        coverage=p.coverage if "coverage" in visible else None,
        bio=p.bio if "bio" in visible else None,
        specialties=p.specialties if "specialties" in visible else None,
        highlights=p.highlights if "highlights" in visible else None,
        education=p.education if "education" in visible else None,
        costs=p.costs if "costs" in visible else None,
        cost_note=p.cost_note if "costs" in visible else None,
        faq=p.faq if "faq" in visible else None,
        # Leaving `reviews` out empties the list of written reviews and leaves
        # the stars above them alone — the average and the words are separate
        # claims, and a professional may withdraw one and keep the other.
        reviews=[
            PublicReview(
                author=r.author, stars=r.stars, text=r.text, context=r.context,
                source=r.source, kind=r.kind.value,
                verified=r.kind is ReviewKind.vilaow_verified,
            )
            for r in reviews
        ] if "reviews" in visible else [],
        # Leaving `details` out withdraws the whole block of owner-defined
        # answers. What would have been in it is still decided by each field's
        # `public` flag above — the two layers compose rather than either
        # overriding the other.
        details=[PublicFieldValue(**d) for d in public_values(fields, p.custom)]
        if "details" in visible else [],
        # The two columns the default keeps off: they arrive here only when
        # their key was switched on, and null otherwise. On a card they never
        # arrive at all.
        license=p.license if "license" in visible else None,
        vat_number=p.vat_number if "vat_number" in visible else None,
    )
