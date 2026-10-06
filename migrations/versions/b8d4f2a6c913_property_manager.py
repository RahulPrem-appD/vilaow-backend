"""Property manager: a seventh trade, with its titles and its services

Revision ID: b8d4f2a6c913
Revises: f1a6c8d2e4b7

Item 2 of the client's change requests of 5 October: a Property Manager trade,
on the homepage after Contractor and before Tax Advisor, with a main title to
choose (one) and areas of responsibility (all that apply).

His words are in sentence case ("Holiday home management"). They are stored in
the Title Case every other trade's lists use, because they print as a title
under a name and as service chips beside the others.

Inserted only when no trade has this key, so a deploy never duplicates or
overwrites one the owner has created or edited in the admin. The trades after
Contractor move down one place to make room; with no Contractor, it goes last.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8d4f2a6c913"
down_revision = "f1a6c8d2e4b7"
branch_labels = None
depends_on = None

KEY = "property_manager"

# The main title: one per professional.
SUBROLES = [
    "Holiday Home Management",
    "Long-Term Rental Management",
    "Full-Service Property Management",
]

# Areas of responsibility: "choose all that apply", so the cap is all six.
SERVICES = [
    "Tenant Search & Rent Collection",
    "Short-Term Rental Management",
    "Cleaning & Guest Check-In",
    "Maintenance & Contractor Coordination",
    "Pool & Garden Care",
    "Bills & Owner Reports",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM professions LIMIT 1")).first() is None:
        # An empty database: `python -m app.seed` creates every trade, this
        # one included, in order. Inserting it here would take position 0.
        return
    if bind.execute(sa.text("SELECT 1 FROM professions WHERE key = :key"), {"key": KEY}).first():
        return

    contractor = bind.execute(
        sa.text("SELECT position FROM professions WHERE key = 'contractor'")
    ).scalar()
    if contractor is None:
        position = bind.execute(
            sa.text("SELECT COALESCE(MAX(position), -1) + 1 FROM professions")
        ).scalar()
    else:
        position = contractor + 1
        bind.execute(
            sa.text("UPDATE professions SET position = position + 1 WHERE position >= :position"),
            {"position": position},
        )

    bind.execute(
        sa.text(
            "INSERT INTO professions "
            "(key, label, plural, hint, position, active, subroles, specializations, "
            " max_specializations) "
            "VALUES (:key, :label, :plural, :hint, :position, true, :subroles, :services, :cap)"
        ),
        {
            "key": KEY,
            "label": "Property manager",
            "plural": "Property managers",
            "hint": "Care & rentals",
            "position": position,
            "subroles": SUBROLES,
            "services": SERVICES,
            "cap": len(SERVICES),
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT id, position FROM professions WHERE key = :key"), {"key": KEY}
    ).first()
    if row is None:
        return
    if bind.execute(
        sa.text("SELECT 1 FROM professionals WHERE profession_id = :id LIMIT 1"), {"id": row.id}
    ).first():
        # Deleting the trade would orphan real records. Move them first.
        raise RuntimeError("Professionals are filed under Property manager; move them first.")
    bind.execute(sa.text("DELETE FROM professions WHERE id = :id"), {"id": row.id})
    bind.execute(
        sa.text("UPDATE professions SET position = position - 1 WHERE position > :position"),
        {"position": row.position},
    )
