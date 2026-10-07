"""Site texts: hand corrections to the website's words

Revision ID: c3f7a1d9e2b4
Revises: b8d4f2a6c913

Item 10 of the client's change requests of 5 October: the site in English,
Greek and French, translated once and stored, with "a simple way to correct a
translation by hand". The stored translations live in the website's code; a
correction is a row here, laid over them. See app/models/site_text.py.

A new, empty table: nothing existing is touched, and downgrade simply drops it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3f7a1d9e2b4"
down_revision = "b8d4f2a6c913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_texts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(300), nullable=False),
        sa.Column("locale", sa.String(5), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(16), nullable=True),
        sa.Column(
            "updated_by_id", sa.Integer(),
            sa.ForeignKey("staff.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("key", "locale", name="uq_site_texts_key_locale"),
    )


def downgrade() -> None:
    op.drop_table("site_texts")
