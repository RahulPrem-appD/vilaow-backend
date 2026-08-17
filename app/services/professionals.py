"""The pipeline: what a caller and an owner may do to a record.

The publish gate is the reason this service exists. It used to be inline in the
handler, which meant the endpoint that publishes and the badge that says
"ready to publish" were two separate readings of the same rule and free to
disagree. Both now call `readiness`, and the rule itself is pure
(app/domain/publishing.py) with the fetching in a repository.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.errors import Conflict, Invalid, NotFound
from app.domain.fields import validate_custom
from app.domain.publishing import Readiness
from app.models import Event, Profession, Professional, Stage, Staff
from app.ports.clock import Clock
from app.repositories.publishing import profession_fields, readiness
from app.services.photos import photo_reference


# The main pipeline in order, so "forward" has a meaning. `declined` and
# `not_valid` are exits rather than positions and deliberately absent: a call
# does not move someone out of them by accident, only by an explicit stage.
_BEFORE_DETAILS = (Stage.imported, Stage.contacted)


@dataclass(frozen=True)
class ProfessionalFilters:
    stage: Stage | None = None
    region: str | None = None
    city: str | None = None
    profession_id: int | None = None
    assigned_to_id: int | None = None
    needs_attention: bool = False
    q: str | None = None


class ProfessionalService:
    def __init__(self, db: Session, *, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    def get(self, professional_id: int) -> Professional:
        professional = self._db.get(Professional, professional_id)
        if professional is None:
            raise NotFound("Professional not found")
        return professional

    # ── listing ─────────────────────────────────────────────────────────────
    def list(self, filters: ProfessionalFilters, *, limit: int, offset: int):
        stmt = select(Professional)
        if filters.stage is not None:
            stmt = stmt.where(Professional.stage == filters.stage)
        if filters.region is not None:
            stmt = stmt.where(Professional.region == filters.region)
        if filters.city is not None:
            stmt = stmt.where(Professional.city == filters.city)
        if filters.profession_id is not None:
            stmt = stmt.where(Professional.profession_id == filters.profession_id)
        if filters.assigned_to_id is not None:
            stmt = stmt.where(Professional.assigned_to_id == filters.assigned_to_id)
        if filters.q:
            like = f"%{filters.q}%"
            stmt = stmt.where(or_(
                Professional.business_name.ilike(like),
                Professional.contact_name.ilike(like),
                Professional.phone.ilike(like),
            ))

        if filters.needs_attention:
            return self._needs_attention(stmt, limit=limit, offset=offset)

        total = self._db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        items = self._db.scalars(
            stmt.order_by(Professional.created_at.desc()).limit(limit).offset(offset)
        ).all()
        return total, list(items)

    def _needs_attention(self, stmt, *, limit: int, offset: int):
        """Published profiles missing a required answer.

        The deliberate consequence of "adding a required field never
        unpublishes anyone": the gap has to surface somewhere or it is
        invisible forever.

        Filtered in Python rather than SQL because "required" lives in the
        field definitions and the answers live in JSONB; expressing that join
        in SQL would be a query nobody can read. It is bounded by the published
        set — what is on the website, not the whole pipeline — so revisit if
        that ever stops being small.
        """
        from app.domain.fields import missing_required

        stmt = stmt.where(Professional.published.is_(True))
        candidates = self._db.scalars(stmt.order_by(Professional.created_at.desc())).all()

        by_profession: dict[int, list] = {}
        flagged = []
        for candidate in candidates:
            if candidate.profession_id is None:
                continue
            if candidate.profession_id not in by_profession:
                by_profession[candidate.profession_id] = profession_fields(
                    self._db, candidate.profession_id
                )
            if missing_required(by_profession[candidate.profession_id], candidate.custom):
                flagged.append(candidate)

        return len(flagged), flagged[offset: offset + limit]

    # ── editing ─────────────────────────────────────────────────────────────
    def update(self, professional_id: int, data: dict) -> Professional:
        professional = self.get(professional_id)

        moving_profession = (
            data.get("profession_id") is not None
            and data["profession_id"] != professional.profession_id
        )
        if data.get("profession_id") is not None:
            if self._db.get(Profession, data["profession_id"]) is None:
                raise Invalid("Unknown profession_id")

        if moving_profession:
            # Answers belong to the profession whose form defined them. Keeping
            # them across a change lets one profession's key collide with
            # another's — "fees" on a Lawyer form marked internal, "fees" on a
            # Notary form marked public — and the old, private answer is
            # republished under the new field's rules without anyone touching
            # it. `public_values` reads definitions rather than stored data
            # precisely so that a field decides what is public; carrying orphan
            # answers across is the one way round that.
            #
            # Dropping them is the safe direction: a caller re-asking a
            # question costs a phone call, and the alternative silently
            # publishes something a professional gave in confidence.
            professional.custom = {}

        # Owner-defined answers go through the field definitions rather than
        # being written straight to the column: a value that does not satisfy
        # its own field would otherwise sit there until it reached a profile.
        if "custom" in data:
            submitted = data.pop("custom") or {}
            target = data.get("profession_id", professional.profession_id)
            fields = profession_fields(self._db, target)
            if not fields and submitted:
                raise Invalid("This professional's profession has no fields defined")
            merged, errors = validate_custom(
                fields, submitted, partial=True, existing=professional.custom or {}
            )
            if errors:
                raise Invalid(
                    "Some answers do not fit their field",
                    errors=[{"key": e.key, "detail": e.message} for e in errors],
                )
            professional.custom = merged

        # The same rule the signing path enforces. This one is not about
        # trusting staff: the caller's call form exposes `photo` as an editable
        # field, so a pasted external URL would be served from a public profile
        # under Vilaow's name, and a data: URL would ship in every list
        # response. Clearing it stays allowed — that is how a photo is removed.
        if data.get("photo"):
            data["photo"] = photo_reference(self._db, professional, data["photo"])

        for field, value in data.items():
            setattr(professional, field, value)

        self._db.commit()
        self._db.refresh(professional)
        return professional

    def readiness(self, professional_id: int) -> Readiness:
        return readiness(self._db, self.get(professional_id))

    # ── pipeline moves ──────────────────────────────────────────────────────
    def change_stage(self, professional_id: int, stage: Stage, note: str | None,
                     *, staff: Staff) -> Professional:
        professional = self.get(professional_id)
        previous = professional.stage
        professional.stage = stage
        self._record(professional.id, staff, "stage_change",
                     detail=note or f"{previous.value} -> {stage.value}")
        self._db.commit()
        self._db.refresh(professional)
        return professional

    def assign(self, professional_id: int, assigned_to_id: int, *, staff: Staff) -> Professional:
        professional = self.get(professional_id)
        assignee = self._db.get(Staff, assigned_to_id)
        if assignee is None:
            raise NotFound("Staff not found")
        professional.assigned_to_id = assignee.id
        self._record(professional.id, staff, "assigned", detail=f"assigned to {assignee.name}")
        self._db.commit()
        self._db.refresh(professional)
        return professional

    def record_call(self, professional_id: int, data: dict, *, stage: Stage | None,
                    notes: str | None, staff: Staff) -> Professional:
        """Logging a call also edits the record, so it reuses update() rather
        than repeating the custom-field validation. `stage` is handled here
        because a call moves someone forward by default."""
        professional = self.update(professional_id, {k: v for k, v in data.items()
                                                     if k != "stage"})
        professional.called_by_id = staff.id
        professional.called_at = self._clock.now()
        # Default forward, never backward. Without the ordering check, a caller
        # ringing a professional who had already signed — to correct a phone
        # number, say — reset them to details_collected and undid the pipeline
        # position their signature had earned. An explicit `stage` is still
        # honoured: moving someone back deliberately is a real action.
        if stage is not None:
            professional.stage = stage
        elif professional.stage in _BEFORE_DETAILS:
            professional.stage = Stage.details_collected
        self._record(professional.id, staff, "called", detail=notes)
        self._db.commit()
        self._db.refresh(professional)
        return professional

    # ── publishing ──────────────────────────────────────────────────────────
    def publish(self, professional_id: int, *, staff: Staff) -> Professional:
        professional = self.get(professional_id)

        # All four gates, not just the agreement. The owner's click is the
        # fourth and it is this request; refusing here is the only place that
        # actually holds the line.
        state = readiness(self._db, professional)
        if not state.ready:
            raise Conflict(
                "This profile is not ready to publish",
                context={"blockers": list(state.blockers)},
            )

        if not professional.slug:
            professional.slug = self._unique_slug(
                professional.contact_name or professional.business_name
            )
        professional.published = True
        professional.published_at = self._clock.now()
        self._record(professional.id, staff, "published")
        self._db.commit()
        self._db.refresh(professional)
        return professional

    def unpublish(self, professional_id: int, *, staff: Staff) -> Professional:
        professional = self.get(professional_id)
        professional.published = False
        professional.published_at = None
        self._record(professional.id, staff, "unpublished")
        self._db.commit()
        self._db.refresh(professional)
        return professional

    # ── internals ───────────────────────────────────────────────────────────
    # `Professional.slug` is String(140), and a long enough business name
    # overflowed it — publishing threw instead of publishing. Room is left for
    # the "-2" a collision appends.
    SLUG_MAX = 120

    def _slugify(self, text: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
        return (slug[:self.SLUG_MAX].rstrip("-") or "professional")

    def _unique_slug(self, base: str) -> str:
        root = self._slugify(base)
        candidate, n = root, 2
        while self._db.scalar(select(Professional).where(Professional.slug == candidate)):
            candidate, n = f"{root}-{n}", n + 1
        return candidate

    def _record(self, professional_id: int, staff: Staff, kind: str,
                detail: str | None = None) -> None:
        self._db.add(Event(
            professional_id=professional_id,
            actor_id=staff.id,
            actor_label=staff.name,
            kind=kind,
            detail=detail,
        ))
