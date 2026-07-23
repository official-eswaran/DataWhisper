"""signed audit chain checkpoints + running entry count

Revision ID: a81e5f30c6d2
Revises: f4a2c07be913
Create Date: 2026-07-23 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a81e5f30c6d2'
down_revision: str | None = 'f4a2c07be913'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Running count of chain entries, so checkpointing every N appends costs no
    # COUNT(*). Existing rows start at 0: the counter only drives *when* the
    # next checkpoint is written, so an under-count delays one checkpoint on an
    # already-populated chain and nothing more.
    with op.batch_alter_table('audit_chain_state', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('entries', sa.BigInteger(), nullable=False, server_default='0')
        )

    # Signed anchors for bounded verification (issue #30).
    op.create_table(
        'audit_checkpoints',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('last_id', sa.Integer(), nullable=False),
        sa.Column('last_hash', sa.String(length=64), nullable=False),
        sa.Column('entries', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('signature', sa.String(length=64), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_audit_ckpt_org_last', 'audit_checkpoints', ['org_id', 'last_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_audit_ckpt_org_last', table_name='audit_checkpoints')
    op.drop_table('audit_checkpoints')
    with op.batch_alter_table('audit_chain_state', schema=None) as batch_op:
        batch_op.drop_column('entries')
