"""stripe billing linkage on organizations + webhook idempotency table

Revision ID: e3d9b5c1a740
Revises: c7e1a2f4b9d0
Create Date: 2026-07-21 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e3d9b5c1a740'
down_revision: str | None = 'c7e1a2f4b9d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default so this is safe against tables that already have rows —
    # Postgres rejects a NOT NULL column added without one.
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('stripe_customer_id', sa.String(length=64),
                      nullable=False, server_default='')
        )
        batch_op.add_column(
            sa.Column('stripe_subscription_id', sa.String(length=64),
                      nullable=False, server_default='')
        )
        batch_op.add_column(
            sa.Column('billing_status', sa.String(length=20),
                      nullable=False, server_default='none')
        )

    # Webhook handlers reverse-look-up the org by customer id on every event.
    op.create_index(
        'ix_orgs_stripe_customer', 'organizations', ['stripe_customer_id'], unique=False
    )

    op.create_table(
        'stripe_events',
        sa.Column('event_id', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False, server_default=''),
        sa.Column(
            'received_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint('event_id'),
    )


def downgrade() -> None:
    op.drop_table('stripe_events')
    op.drop_index('ix_orgs_stripe_customer', table_name='organizations')
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('billing_status')
        batch_op.drop_column('stripe_subscription_id')
        batch_op.drop_column('stripe_customer_id')
