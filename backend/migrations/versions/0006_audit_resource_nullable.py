"""Audit events that are not about a resource (system start, worker start, sign-in) have no resource type.

Revision ID: 0006_audit_resource_nullable
Revises: 0005_offsite_backup
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_audit_resource_nullable"
down_revision = "0005_offsite_backup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rows written before this revision were all inserted with a resource type, so nothing needs backfilling.
    # Changing a column does not touch the hash chain, which covers field values rather than column constraints.
    op.alter_column("audit_logs", "resource_type", existing_type=sa.String(100), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE audit_logs SET resource_type = 'system' WHERE resource_type IS NULL")
    op.alter_column("audit_logs", "resource_type", existing_type=sa.String(100), nullable=False)
