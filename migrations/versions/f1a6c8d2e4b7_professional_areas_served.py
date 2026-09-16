"""professional areas served as a fixed multi-select

Revision ID: f1a6c8d2e4b7
Revises: e4c2a7b9d013

The existing city and region remain the single office location. Areas served
are stored separately because one professional can work across several areas.

The legacy free-text ``coverage`` column is intentionally left untouched. Its
values cannot be mapped reliably without a human decision (for example, "All
of Crete" may or may not mean every listed Crete area), so deployment must not
silently rewrite or discard them.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1a6c8d2e4b7"
down_revision = "e4c2a7b9d013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "professionals",
        sa.Column("areas_served", sa.ARRAY(sa.String()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("professionals", "areas_served")
