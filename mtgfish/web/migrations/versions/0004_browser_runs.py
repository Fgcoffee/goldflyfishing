"""Runs played in the browser by the TypeScript engine, sent back and stored.

Revision ID: 0004_browser_runs
Revises: 0003_terms
Create Date: 2026-11-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_browser_runs"
down_revision = "0003_terms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "user_id", sa.String(32),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_seed", sa.BigInteger(), nullable=False),
        sa.Column("game_start", sa.Integer(), nullable=False),
        sa.Column("game_end", sa.Integer(), nullable=False),
        sa.Column("games", sa.Integer(), nullable=False),
        sa.Column("crashed", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("hero", sa.Integer(), nullable=False),
        sa.Column("max_rounds", sa.Integer(), nullable=False),
        sa.Column("engine_build", sa.String(80), nullable=False),
        sa.Column("ir_build", sa.String(80), nullable=False),
        sa.Column("snapshot", sa.String(80), nullable=False),
        sa.Column("decks", sa.Text(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("spot_check", sa.String(16), nullable=False),
        sa.Column("spot_check_detail", sa.Text(), nullable=True),
    )
    op.create_index("ix_browser_runs_user_id", "browser_runs", ["user_id"])
    op.create_index("ix_browser_runs_created", "browser_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_browser_runs_created", table_name="browser_runs")
    op.drop_index("ix_browser_runs_user_id", table_name="browser_runs")
    op.drop_table("browser_runs")
