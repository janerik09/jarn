"""Off-host copies of database dumps and audit archives (SFTP). Off by default.

Revision ID: 0005_offsite_backup
Revises: 0004_audit_trail
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
import app.models.types

revision = "0005_offsite_backup"
down_revision = "0004_audit_trail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_settings", sa.Column("offsite_config", app.models.types.SealedJSON(astext_type=Text()),
                                               nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("system_settings", "offsite_config")
