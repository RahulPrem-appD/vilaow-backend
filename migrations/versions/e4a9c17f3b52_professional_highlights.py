"""professional highlights

Revision ID: e4a9c17f3b52
Revises: dc848b47a33c
Create Date: 2026-09-03 14:05:11.862361

One column: the selling points shown as chips on a public profile ("Fixed
fee", "Golden Visa", "Terms in English"). Nullable and array-typed exactly as
`specialties` is, because it is the same kind of value — a short list a
caller types in, that a profile with none of simply shows nothing.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e4a9c17f3b52'
down_revision: Union[str, Sequence[str], None] = 'dc848b47a33c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('professionals',
                  sa.Column('highlights', postgresql.ARRAY(sa.String()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('professionals', 'highlights')
