"""rating_captured_on on professionals

Revision ID: b71c2f0a94d5
Revises: c5d9e4a1b7f3
Create Date: 2026-09-06 09:40:00.000000

One column: the day someone read a professional's rating and review count off
their Google listing.

`rating` and `review_count` have been on this table since the import and have
never been published, because the import carried whatever the spreadsheet said
and a record could claim 33 reviews while the page showed two. The number was
never the problem — the missing thing was any statement of when it was true.
This column is that statement, and the public serialiser publishes the imported
figure only when all three are filled in (app/routers/public.py).

Nullable, with nothing backfilled. Every record that exists predates the
column, and guessing a date for a number nobody has checked would defeat the
whole point of adding it: a null here means "not verified", which is exactly
the state those rows are in. They keep publishing the rating computed from
their review rows, as they did before this migration existed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b71c2f0a94d5'
down_revision: Union[str, Sequence[str], None] = 'c5d9e4a1b7f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('professionals', sa.Column('rating_captured_on', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('professionals', 'rating_captured_on')
