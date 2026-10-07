"""Introductions: the WhatsApp follow-up

Revision ID: 5a8e3d1f7c62
Revises: c3f7a1d9e2b4

Item 12 of the client's change requests of 5 October: when a buyer asks to be
put in touch, thank them and send their details to the professional on
WhatsApp, remind him until he says he made contact, and check with the buyer
after two days — every step saved with its time. See
app/models/introduction_step.py and app/services/follow_ups.py.

A new table, and four empty columns on introductions. Nothing existing is
changed, and downgrade removes exactly what this adds.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "5a8e3d1f7c62"
down_revision = "c3f7a1d9e2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("introductions", sa.Column("locale", sa.String(5), nullable=True))
    op.add_column("introductions", sa.Column("pro_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("introductions", sa.Column("buyer_answer", sa.String(8), nullable=True))
    op.add_column("introductions", sa.Column("buyer_answered_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "introduction_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "introduction_id", sa.Integer(),
            sa.ForeignKey("introductions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("recipient", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("number", sa.String(20), nullable=True),
        sa.Column("transport", sa.String(10), nullable=True),
        sa.Column("provider_id", sa.String(64), nullable=True),
        sa.Column("answer", sa.String(8), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_introduction_steps_introduction_id", "introduction_steps", ["introduction_id"])
    op.create_index("ix_introduction_steps_due", "introduction_steps", ["status", "due_at"])
    op.create_index("ix_introduction_steps_number", "introduction_steps", ["number"])
    op.create_index("ix_introduction_steps_provider_id", "introduction_steps", ["provider_id"],
                    unique=True)


def downgrade() -> None:
    op.drop_table("introduction_steps")
    op.drop_column("introductions", "buyer_answered_at")
    op.drop_column("introductions", "buyer_answer")
    op.drop_column("introductions", "pro_confirmed_at")
    op.drop_column("introductions", "locale")
