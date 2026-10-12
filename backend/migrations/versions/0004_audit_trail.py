"""Audit trail: categories, outcomes, correlation IDs, details and a hash chain for tamper evidence.

Revision ID: 0004_audit_trail
Revises: 0003_users_mfa
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_audit_trail"
down_revision = "0003_users_mfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("seq", sa.BigInteger(), nullable=True))
    op.execute("CREATE SEQUENCE IF NOT EXISTS audit_logs_seq_seq")
    op.execute("UPDATE audit_logs SET seq = nextval('audit_logs_seq_seq') WHERE seq IS NULL")
    op.execute("ALTER TABLE audit_logs ALTER COLUMN seq SET DEFAULT nextval('audit_logs_seq_seq')")
    op.execute("ALTER TABLE audit_logs ALTER COLUMN seq SET NOT NULL")
    op.create_index("ix_audit_logs_seq", "audit_logs", ["seq"], unique=True)
    op.add_column("audit_logs", sa.Column("category", sa.String(50), nullable=True))
    op.add_column("audit_logs", sa.Column("outcome", sa.String(20), nullable=True))
    op.add_column("audit_logs", sa.Column("source", sa.String(20), nullable=False, server_default="api"))
    op.add_column("audit_logs", sa.Column("correlation_id", sa.String(64), nullable=True))
    op.add_column("audit_logs", sa.Column("actor_name", sa.String(255), nullable=True))
    op.add_column("audit_logs", sa.Column("details", postgresql.JSONB(), nullable=True))
    op.add_column("audit_logs", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("audit_logs", sa.Column("row_hash", sa.String(64), nullable=True))
    op.create_index("ix_audit_logs_category", "audit_logs", ["category"])
    op.create_index("ix_audit_logs_outcome", "audit_logs", ["outcome"])
    op.create_index("ix_audit_logs_correlation_id", "audit_logs", ["correlation_id"])
    # Existing rows get their outcome from the old status column
    op.execute("UPDATE audit_logs SET outcome = status::text WHERE outcome IS NULL")


def downgrade() -> None:
    raise NotImplementedError("Restore from a database dump to go back before this revision.")
