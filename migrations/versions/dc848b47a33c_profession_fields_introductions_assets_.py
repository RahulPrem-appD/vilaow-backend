"""profession fields, introductions, assets, signing evidence

Revision ID: dc848b47a33c
Revises: 2bc351640e80
Create Date: 2026-08-13 00:15:27.021195

Hand-corrected after autogenerate, which got four things wrong against a
database that already holds rows:

  * `otp_attempts` and `reviews.kind` came out NOT NULL with no server default,
    which fails immediately on any existing agreement or review.
  * `signature_svg` was dropped without moving what was in it — that column is
    signature evidence, so it is copied across first.
  * the downgrade left all five new enum types behind, so re-running the
    migration would fail on "type already exists". Same leak as the first
    migration had.
  * `professionals.custom` is filtered on, so it wants a GIN index.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'dc848b47a33c'
down_revision: Union[str, Sequence[str], None] = '2bc351640e80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum types added to *existing* tables have to be created explicitly; the ones
# used only by new tables are created by create_table.
review_kind = postgresql.ENUM('google', 'vilaow_verified', name='review_kind')


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # ── the owner-defined form ──────────────────────────────────────────────
    op.create_table(
        'profession_fields',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('profession_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=60), nullable=False),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('help_text', sa.String(length=240), nullable=True),
        sa.Column(
            'type',
            sa.Enum('short_text', 'long_text', 'select', 'multi_select', 'number', 'date', 'file',
                    name='profession_field_type'),
            nullable=False,
        ),
        sa.Column('options', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=True),
        sa.Column('required', sa.Boolean(), server_default=sa.false(), nullable=False),
        # Fields are internal until the owner deliberately publishes them.
        sa.Column('public', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['profession_id'], ['professions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('profession_id', 'key', name='uq_profession_field_key'),
    )
    op.create_index(op.f('ix_profession_fields_profession_id'), 'profession_fields', ['profession_id'], unique=False)

    # ── uploaded files ──────────────────────────────────────────────────────
    op.create_table(
        'assets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('professional_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Enum('photo', 'document', name='asset_kind'), nullable=False),
        sa.Column('field_key', sa.String(length=60), nullable=True),
        sa.Column('storage_path', sa.String(length=500), nullable=False),
        sa.Column('content_type', sa.String(length=120), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('original_filename', sa.String(length=255), nullable=True),
        sa.Column('uploaded_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['professional_id'], ['professionals.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by_id'], ['staff.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_assets_deleted_at'), 'assets', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_assets_field_key'), 'assets', ['field_key'], unique=False)
    op.create_index(op.f('ix_assets_kind'), 'assets', ['kind'], unique=False)
    op.create_index(op.f('ix_assets_professional_id'), 'assets', ['professional_id'], unique=False)

    # ── buyers asking to be introduced ──────────────────────────────────────
    op.create_table(
        'introductions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('professional_id', sa.Integer(), nullable=False),
        sa.Column('professional_name', sa.String(length=200), nullable=True),
        sa.Column('professional_role', sa.String(length=80), nullable=True),
        sa.Column('city', sa.String(length=80), nullable=True),
        sa.Column('buyer_name', sa.String(length=160), nullable=False),
        sa.Column('buyer_email', sa.String(length=255), nullable=False),
        sa.Column('buyer_phone', sa.String(length=60), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        # The lawful basis for mailing a stranger's details to a third party,
        # captured at the moment they ticked it.
        sa.Column('consent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('consent_text', sa.Text(), nullable=True),
        sa.Column('source_page', sa.String(length=255), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('status', sa.Enum('new', 'chased', 'closed', name='intro_status'),
                  server_default='new', nullable=False),
        sa.Column('outcome', sa.Enum('professional_contacted', 'buyer_proceeded', 'buyer_went_elsewhere',
                                     'no_response', name='intro_outcome'), nullable=True),
        sa.Column('assigned_to_id', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_by_id', sa.Integer(), nullable=True),
        sa.Column('review_token', sa.String(length=64), nullable=True),
        sa.Column('review_requested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('review_submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['assigned_to_id'], ['staff.id'], ),
        sa.ForeignKeyConstraint(['closed_by_id'], ['staff.id'], ),
        sa.ForeignKeyConstraint(['professional_id'], ['professionals.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_introductions_assigned_to_id'), 'introductions', ['assigned_to_id'], unique=False)
    op.create_index(op.f('ix_introductions_created_at'), 'introductions', ['created_at'], unique=False)
    op.create_index(op.f('ix_introductions_due_at'), 'introductions', ['due_at'], unique=False)
    op.create_index(op.f('ix_introductions_outcome'), 'introductions', ['outcome'], unique=False)
    op.create_index(op.f('ix_introductions_professional_id'), 'introductions', ['professional_id'], unique=False)
    op.create_index(op.f('ix_introductions_review_token'), 'introductions', ['review_token'], unique=True)
    op.create_index(op.f('ix_introductions_status'), 'introductions', ['status'], unique=False)

    # ── signing evidence ────────────────────────────────────────────────────
    op.add_column('agreements', sa.Column('terms_text', sa.Text(), nullable=True))
    op.add_column('agreements', sa.Column('signed_email', sa.String(length=255), nullable=True))
    op.add_column('agreements', sa.Column('signature_image', sa.Text(), nullable=True))
    op.add_column('agreements', sa.Column('signed_fields', postgresql.JSONB(astext_type=sa.Text()),
                                          server_default=sa.text("'{}'::jsonb"), nullable=True))
    op.add_column('agreements', sa.Column('otp_hash', sa.String(length=255), nullable=True))
    op.add_column('agreements', sa.Column('otp_sent_at', sa.DateTime(timezone=True), nullable=True))
    # server_default, or this fails on every agreement already in the table.
    op.add_column('agreements', sa.Column('otp_attempts', sa.Integer(), server_default='0', nullable=False))
    op.add_column('agreements', sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))

    # Carry the existing signatures across before dropping the old column.
    # This is evidence of a signed agreement; losing it to a rename would be
    # losing the only proof the signature ever happened.
    op.execute('UPDATE agreements SET signature_image = signature_svg WHERE signature_svg IS NOT NULL')
    op.drop_column('agreements', 'signature_svg')

    # ── answers to the owner-defined fields ─────────────────────────────────
    op.add_column('professionals', sa.Column('custom', postgresql.JSONB(astext_type=sa.Text()),
                                             server_default=sa.text("'{}'::jsonb"), nullable=True))
    # Filtered on by key, so it earns a GIN index rather than a sequential scan.
    op.create_index('ix_professionals_custom', 'professionals', ['custom'],
                    unique=False, postgresql_using='gin')

    # ── review provenance ───────────────────────────────────────────────────
    review_kind.create(bind, checkfirst=True)
    # Everything already in the table was copied from a public listing by a
    # caller, so 'google' is the factually correct backfill — and the server
    # default is what lets this run at all against existing rows.
    op.add_column('reviews', sa.Column('kind', review_kind, server_default='google', nullable=False))
    op.add_column('reviews', sa.Column('introduction_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_reviews_introduction_id'), 'reviews', ['introduction_id'], unique=False)
    op.create_index(op.f('ix_reviews_kind'), 'reviews', ['kind'], unique=False)
    op.create_foreign_key('fk_reviews_introduction_id', 'reviews', 'introductions',
                          ['introduction_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()

    op.drop_constraint('fk_reviews_introduction_id', 'reviews', type_='foreignkey')
    op.drop_index(op.f('ix_reviews_kind'), table_name='reviews')
    op.drop_index(op.f('ix_reviews_introduction_id'), table_name='reviews')
    op.drop_column('reviews', 'introduction_id')
    op.drop_column('reviews', 'kind')

    op.drop_index('ix_professionals_custom', table_name='professionals')
    op.drop_column('professionals', 'custom')

    op.add_column('agreements', sa.Column('signature_svg', sa.TEXT(), autoincrement=False, nullable=True))
    op.execute('UPDATE agreements SET signature_svg = signature_image WHERE signature_image IS NOT NULL')
    op.drop_column('agreements', 'email_verified_at')
    op.drop_column('agreements', 'otp_attempts')
    op.drop_column('agreements', 'otp_sent_at')
    op.drop_column('agreements', 'otp_hash')
    op.drop_column('agreements', 'signed_fields')
    op.drop_column('agreements', 'signature_image')
    op.drop_column('agreements', 'signed_email')
    op.drop_column('agreements', 'terms_text')

    op.drop_index(op.f('ix_introductions_status'), table_name='introductions')
    op.drop_index(op.f('ix_introductions_review_token'), table_name='introductions')
    op.drop_index(op.f('ix_introductions_professional_id'), table_name='introductions')
    op.drop_index(op.f('ix_introductions_outcome'), table_name='introductions')
    op.drop_index(op.f('ix_introductions_due_at'), table_name='introductions')
    op.drop_index(op.f('ix_introductions_created_at'), table_name='introductions')
    op.drop_index(op.f('ix_introductions_assigned_to_id'), table_name='introductions')
    op.drop_table('introductions')

    op.drop_index(op.f('ix_assets_professional_id'), table_name='assets')
    op.drop_index(op.f('ix_assets_kind'), table_name='assets')
    op.drop_index(op.f('ix_assets_field_key'), table_name='assets')
    op.drop_index(op.f('ix_assets_deleted_at'), table_name='assets')
    op.drop_table('assets')

    op.drop_index(op.f('ix_profession_fields_profession_id'), table_name='profession_fields')
    op.drop_table('profession_fields')

    # Drop the types too. Postgres keeps enum types after their tables go, so
    # without this a re-run dies on "type already exists" — the same leak the
    # first migration had.
    for enum_name in ('review_kind', 'intro_outcome', 'intro_status', 'asset_kind', 'profession_field_type'):
        op.execute(sa.text(f'DROP TYPE IF EXISTS {enum_name}'))
