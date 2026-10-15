"""WhatsApp: the staff inbox

Revision ID: 9d2b6f4a1c38
Revises: 5a8e3d1f7c62

Every message to and from Vilaow's WhatsApp number, so staff can read and
answer them in the admin. See app/models/inbox_message.py and
app/services/whatsapp_inbox.py.

A new table. Also widens introduction_steps.provider_id from 64 characters to
128: Meta's message ids are about 60 characters, longer for long numbers, and
one that did not fit would have failed the step that carried it. Downgrade
removes the table and narrows the column again.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9d2b6f4a1c38"
down_revision = "5a8e3d1f7c62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("template", sa.String(40), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("provider_id", sa.String(128), nullable=True, unique=True),
        sa.Column("sent_by", sa.String(12), nullable=True),
        sa.Column("staff_id", sa.Integer(),
                  sa.ForeignKey("staff.id", ondelete="SET NULL"), nullable=True),
        sa.Column("introduction_id", sa.Integer(),
                  sa.ForeignKey("introductions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("profile_name", sa.String(120), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_whatsapp_messages_number_at", "whatsapp_messages", ["number", "at"])
    op.create_index("ix_whatsapp_messages_introduction_id", "whatsapp_messages",
                    ["introduction_id"])
    op.alter_column("introduction_steps", "provider_id",
                    type_=sa.String(128), existing_type=sa.String(64), existing_nullable=True)


def downgrade() -> None:
    op.alter_column("introduction_steps", "provider_id",
                    type_=sa.String(64), existing_type=sa.String(128), existing_nullable=True)
    op.drop_table("whatsapp_messages")
