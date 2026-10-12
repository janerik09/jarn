"""Baseline: the schema the application was running on when migrations were introduced.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-10
"""
from pathlib import Path

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

BASELINE = Path(__file__).resolve().parent.parent / "baseline_schema.sql"


def upgrade() -> None:
    # Plain SQL from pg_dump: extensions, enums, tables, indexes, constraints
    op.get_bind().exec_driver_sql(BASELINE.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("The baseline cannot be reversed. Restore from a database dump instead.")
