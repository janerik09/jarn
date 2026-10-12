"""Single sign-on settings (OpenID Connect providers and LDAP), stored encrypted.

Revision ID: 0007_sso
Revises: 0006_audit_resource_nullable
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
import app.models.types

revision = "0007_sso"
down_revision = "0006_audit_resource_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_settings", sa.Column("sso_config", app.models.types.SealedJSON(astext_type=Text()),
                                               nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("system_settings", "sso_config")
