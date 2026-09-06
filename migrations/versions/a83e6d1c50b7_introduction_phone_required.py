"""introductions.buyer_phone becomes required

Revision ID: a83e6d1c50b7
Revises: b71c2f0a94d5
Create Date: 2026-09-06 09:45:00.000000

The buyer's phone was optional on the introduction form and nullable in the
column. It is required now, on the client's instruction and for a plain reason:
the promise made on that form is that a professional will ring the buyer back,
and an introduction with no number is one nobody can keep that promise on.
Lead has always had a NOT NULL `buyer_phone` for the same reason, so this only
brings the second of the two buyer forms into line with the first.

Rows already in the table may hold NULL, so the constraint cannot simply be
added. The backfill writes an empty string rather than a placeholder number.
Two reasons for that choice:

  * a placeholder like 'unknown' or '000' sits in a field a caller reads off
    the queue and dials. An empty string cannot be mistaken for a number.
  * every reader of this column already guards on truthiness — the email that
    carries the buyer's details to the professional omits the phone line when
    it is empty (app/adapters/email/templates.py) — so a backfilled row renders
    exactly as it did while it was NULL. Nothing on a screen changes.

The downgrade only drops the constraint. It does not turn the empty strings
back into NULLs: which of them were NULL before this ran is not recoverable
from the data, and inventing that distinction would corrupt rows written after
this migration, where an empty string cannot occur at all.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a83e6d1c50b7'
down_revision: Union[str, Sequence[str], None] = 'b71c2f0a94d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE introductions SET buyer_phone = '' WHERE buyer_phone IS NULL")
    op.alter_column('introductions', 'buyer_phone',
                    existing_type=sa.String(length=60), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('introductions', 'buyer_phone',
                    existing_type=sa.String(length=60), nullable=True)
