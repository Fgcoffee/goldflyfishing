"""Reports: a card read wrongly, a game step that went wrong, or anything else.

Revision ID: 0006_reports
Revises: 0005_browser_run_label
Create Date: 2026-11-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_reports"
down_revision = "0005_browser_run_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "user_id", sa.String(32),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("problem", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page", sa.String(200), nullable=False),
        sa.Column("oracle_id", sa.String(64), nullable=False),
        sa.Column("card_name", sa.String(200), nullable=False),
        sa.Column("card_hash", sa.String(64), nullable=False),
        sa.Column("ir_build", sa.String(80), nullable=False),
        sa.Column("engine_build", sa.String(80), nullable=False),
        sa.Column("reading", sa.Text(), nullable=False),
        sa.Column("run_kind", sa.String(20), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("game_index", sa.BigInteger(), nullable=True),
        sa.Column("seed", sa.BigInteger(), nullable=True),
        sa.Column("log_index", sa.BigInteger(), nullable=True),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("ix_reports_user_id", "reports", ["user_id"])
    op.create_index("ix_reports_status", "reports", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_reports_status", table_name="reports")
    op.drop_index("ix_reports_user_id", table_name="reports")
    op.drop_table("reports")
