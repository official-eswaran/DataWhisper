"""email verification gate (issue #21)

Revision ID: b92c4d17ae03
Revises: a81e5f30c6d2
Create Date: 2026-08-05 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b92c4d17ae03'
down_revision: str | None = 'a81e5f30c6d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Added with server_default '1' so existing accounts are grandfathered in:
    # this gate is being switched on under organizations that signed up before
    # it existed, and locking them out of their own data would be a far worse
    # bug than the abuse it prevents. The default is then dropped, so *new*
    # rows fall back to the model's default of false and must verify.
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'email_verified',
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('email_verified', server_default=None)

    op.create_table(
        'email_verification_tokens',
        # Only the SHA-256 is stored — the plaintext token lives in the mail and
        # nowhere else, so a dumped metadata DB doesn't hand over every pending
        # account.
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('username', sa.String(length=50), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint('token_hash'),
    )
    op.create_index(
        'ix_email_verification_username', 'email_verification_tokens', ['username']
    )


def downgrade() -> None:
    op.drop_index('ix_email_verification_username', table_name='email_verification_tokens')
    op.drop_table('email_verification_tokens')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('email_verified')
