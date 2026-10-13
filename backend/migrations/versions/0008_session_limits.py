"""Session idle and absolute limits: record sign-in time and last renewal on each session.

Revision ID: 0008_session_limits
Revises: 0007_sso
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_session_limits"
down_revision = "0007_sso"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing sessions take their creation time as both the sign-in time and the last renewal
    op.add_column("session_tokens", sa.Column("signed_in_at", sa.DateTime(), nullable=True))
    op.add_column("session_tokens", sa.Column("last_used_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE session_tokens SET signed_in_at = created_at, last_used_at = created_at")
    op.alter_column("session_tokens", "signed_in_at", nullable=False, server_default=sa.text("now()"))
    op.alter_column("session_tokens", "last_used_at", nullable=False, server_default=sa.text("now()"))


def downgrade() -> None:
    op.drop_column("session_tokens", "last_used_at")
    op.drop_column("session_tokens", "signed_in_at")
