"""profession specializations, and how many of them one professional may show

Revision ID: d7b3e1a94c26
Revises: a83e6d1c50b7

His list, per trade, with his own limits — five for an agent, four for an
architect, six for a tax advisor. The Civil engineer's is five: he sent six and
corrected it in the next message.

The seed only fills a trade that has no list yet, so re-running this never
overwrites an edit the owner has since made in admin. A trade whose key is not
here is left alone rather than emptied: adding a profession is a thing the
owner can do, and it must not come back from a deploy with a vocabulary it
never asked for.

Nobody's existing specialties are touched here. Mapping what callers already
typed onto these lists is a separate, reportable step —
backend/scripts/map_specialties.py — because it is a judgement about data and
should not happen silently inside a deploy.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d7b3e1a94c26"
down_revision = "a83e6d1c50b7"
branch_labels = None
depends_on = None


SEED: dict[str, tuple[list[str], int]] = {
    "agent": ([
        "Property Investment",
        "Residential Properties",
        "Commercial Properties",
        "Luxury Properties",
        "Land Acquisition",
        "Hotel & Tourism Properties",
        "Renovation Projects",
        "Rentals",
        "Property Management",
    ], 5),
    "lawyer": ([
        "Golden Visa",
        "Real Estate Law",
        "Property Transactions",
        "Legal Due Diligence",
        "Corporate Law",
        "Tax Law",
        "Contract Law",
    ], 5),
    "engineer": ([
        "Structural Engineering",
        "Construction Supervision",
        "Building Inspections",
        "Building Permits & Licensing",
        "Project Management",
        "Infrastructure",
        "Cost Estimation",
        "Defect Inspection",
    ], 5),
    "architect": ([
        "Luxury Villas",
        "Residential Design",
        "Hotel Design",
        "Interior Architecture",
        "Urban Planning",
        "Building Permits & Licensing",
        "Preservation Projects",
    ], 4),
    "accountant": ([
        "Real Estate Taxation",
        "International Taxation",
        "Corporate Taxation",
        "Tax Planning",
        "Foreign Investor Advisory",
        "Accounting Services",
        "Financial Audits",
        "Company Formation in Greece",
        "Greek Tax Filings",
        "Annual Tax Returns",
    ], 6),
    "contractor": ([
        "Project Management & Execution",
        "Villa Construction",
        "Residential Building Construction",
        "Hotel Construction",
        "Commercial Construction",
        "Preservation Projects",
        "Renovation Projects",
        "Turnkey Construction",
        "Construction Supervision",
        "Defect Inspection",
    ], 6),
}


def upgrade() -> None:
    op.add_column(
        "professions",
        sa.Column("specializations", sa.ARRAY(sa.String()), nullable=True),
    )
    op.add_column(
        "professions",
        sa.Column("max_specializations", sa.Integer(), nullable=True),
    )

    bind = op.get_bind()
    for key, (options, cap) in SEED.items():
        bind.execute(
            sa.text(
                "UPDATE professions "
                "SET specializations = :options, max_specializations = :cap "
                "WHERE key = :key AND specializations IS NULL"
            ),
            {"key": key, "options": options, "cap": cap},
        )


def downgrade() -> None:
    op.drop_column("professions", "max_specializations")
    op.drop_column("professions", "specializations")
