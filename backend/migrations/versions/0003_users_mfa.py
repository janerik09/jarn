"""Users: optional email, TOTP multi-factor authentication, last login. Removes the seeded default admin account.

Revision ID: 0003_users_mfa
Revises: 0002_align_schema
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
import app.models.types

revision = "0003_users_mfa"
down_revision = "0002_align_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "email", existing_type=sa.String(255), nullable=True)
    op.add_column("users", sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("mfa", app.models.types.SealedJSON(astext_type=Text()), nullable=False,
                                     server_default=sa.text("'{}'::jsonb")))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(), nullable=True))
    # The default account created by the old seed script had a known password. The first visit to the app now
    # creates the admin account, so the seeded account is removed. Only the exact seeded row is deleted.
    op.execute("DELETE FROM users WHERE username = 'admin' AND email = 'admin@example.com'")


def downgrade() -> None:
    raise NotImplementedError("Restore from a database dump to go back before this revision.")
