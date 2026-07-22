"""upload_shape_stats — measured bytes-per-row for row-ceiling calibration

Revision ID: f4a2c07be913
Revises: e3d9b5c1a740
Create Date: 2026-07-22 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f4a2c07be913'
down_revision: str | None = 'e3d9b5c1a740'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Single-row aggregate (id is always 1) over every successful upload, so the
    # plan row ceilings can be sized against measured data instead of the
    # assumed ~100 bytes/row they started with (issue #24).
    op.create_table(
        'upload_shape_stats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uploads', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('total_bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('total_rows', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('min_bytes_per_row', sa.Float(), nullable=False, server_default='0'),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('upload_shape_stats')
