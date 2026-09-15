"""profession sub-roles: the title under a name, chosen from the trade's list

Revision ID: e4c2a7b9d013
Revises: d7b3e1a94c26

His lists, per trade, in his words and his order, sent on 15 September. One
per professional — it is the bold line under the name on the card — so the
record editor makes a dropdown of it where the services are tick boxes.

The seed only fills a trade that has no list yet, so re-running this never
overwrites an edit the owner has since made in admin, and a trade whose key is
not here is left alone rather than emptied.

What callers have already typed into the box is not touched here. Mapping it
onto these lists is a separate, reportable step — backend/scripts/map_subroles.py
— because it is a judgement about real people's records and should not happen
silently inside a deploy.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e4c2a7b9d013"
down_revision = "d7b3e1a94c26"
branch_labels = None
depends_on = None


SEED: dict[str, list[str]] = {
    "agent": [
        "Residential / Vacation Homes",
        "Commercial",
        "Foreign Buyer Specialist (Golden Visa, multilingual)",
        "Luxury",
        "Land & New Development Projects",
        "Rental Property Management",
        "Off-Plan / Pre-Construction Sales",
    ],
    "lawyer": [
        "Real Estate Lawyer",
        "Golden Visa / Investment Visa",
        "Inheritance & Title Transfer",
        "Litigation",
        "Corporate / Company Formation",
    ],
    "architect": [
        "Planning & Licensing (Πολεοδομία)",
        "Renovation & Interior Design",
        "Historic Preservation",
        "New Build / Ground-Up Design",
        "Landscape Architecture",
    ],
    "engineer": [
        # His list said "Civil Engineering", which under the trade "Civil
        # engineer" says nothing twice. Rahul chose "General" over dropping it.
        "General Civil Engineering",
        "Structural Inspection",
        "Structural Design",
        "Land Surveying & Cadastre",
        "MEP Engineering",
        "Construction Supervision",
        "Energy Certificates (EPC)",
    ],
    "accountant": [
        "Tax Returns",
        "Non-Resident Tax",
        "Property Tax",
        "Rental Income Tax",
        "AFM & Tax Registration",
        "Company Accounting",
    ],
    "contractor": [
        "New Construction",
        "Renovation & Remodeling",
        "Turnkey Projects",
        "Project Management",
        "Property Maintenance",
        "Pools & Landscaping",
    ],
}


def upgrade() -> None:
    op.add_column(
        "professions",
        sa.Column("subroles", sa.ARRAY(sa.String()), nullable=True),
    )
    bind = op.get_bind()
    for key, options in SEED.items():
        bind.execute(
            sa.text(
                "UPDATE professions SET subroles = :options "
                "WHERE key = :key AND subroles IS NULL"
            ),
            {"key": key, "options": options},
        )


def downgrade() -> None:
    op.drop_column("professions", "subroles")
