"""visible_fields on professions and professionals

Revision ID: c5d9e4a1b7f3
Revises: e4a9c17f3b52
Create Date: 2026-09-03 11:05:00.000000

Two columns, one rule: which built-in fields a public page may show. The
profession's list is the default for everyone in it; the professional's
overrides it for one record. Both nullable string arrays, modelled on
`specialties`, because that is what each of them is — a short list of keys a
caller manages.

Null on both means no decision was made, and a record with no decision keeps
publishing exactly what it published before this migration existed: the
default lives in app/domain/visibility.py, not in the database, so nothing is
backfilled and no server default is set here. An empty list is a storable
answer meaning "show nothing", and is deliberately left alone rather than
normalised to null — null and empty say different things, and a migration is
the last place that difference should be flattened.

The keys themselves live in that module rather than here: a migration fixes
storage, and the vocabulary has to change in step with the code that reads it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c5d9e4a1b7f3'
down_revision: Union[str, Sequence[str], None] = 'e4a9c17f3b52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('professions',
                  sa.Column('visible_fields', postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column('professionals',
                  sa.Column('visible_fields', postgresql.ARRAY(sa.String()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('professionals', 'visible_fields')
    op.drop_column('professions', 'visible_fields')
