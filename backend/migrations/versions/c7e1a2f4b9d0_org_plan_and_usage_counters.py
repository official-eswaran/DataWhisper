"""org plan column + per-tenant usage_counters

Revision ID: c7e1a2f4b9d0
Revises: bd548f798ec3
Create Date: 2026-07-20 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c7e1a2f4b9d0'
down_revision: str | None = 'bd548f798ec3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default keeps this safe on tables that already contain rows
    # (Postgres rejects a NOT NULL column added without a default).
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('plan', sa.String(length=20), nullable=False, server_default='free')
        )

    op.create_table(
        'usage_counters',
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('metric', sa.String(length=32), nullable=False),
        sa.Column('count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('org_id', 'period', 'metric'),
    )


def downgrade() -> None:
    op.drop_table('usage_counters')
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('plan')
