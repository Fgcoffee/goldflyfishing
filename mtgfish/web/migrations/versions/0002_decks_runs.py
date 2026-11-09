"""Saved decks (with versions) and simulation history.

Revision ID: 0002_decks_runs
Revises: 0001_accounts
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_decks_runs"
down_revision = "0001_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "user_id", sa.String(32),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("commander", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decks_user_id", "decks", ["user_id"])
    op.create_table(
        "deck_versions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "deck_id", sa.String(32),
            sa.ForeignKey("decks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("decklist", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("deck_id", "number", name="uq_deck_versions_number"),
    )
    op.create_index("ix_deck_versions_deck_id", "deck_versions", ["deck_id"])
    op.create_index("ix_deck_versions_text_hash", "deck_versions", ["text_hash"])
    op.create_table(
        "runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "user_id", sa.String(32),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "deck_version_id", sa.String(32),
            sa.ForeignKey("deck_versions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("config", sa.Text(), nullable=False),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("games", sa.Integer(), nullable=False),
        sa.Column("run_seed", sa.Integer(), nullable=False),
        sa.Column("card_db_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_runs_user_id", "runs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_user_id", "runs")
    op.drop_table("runs")
    op.drop_index("ix_deck_versions_text_hash", "deck_versions")
    op.drop_index("ix_deck_versions_deck_id", "deck_versions")
    op.drop_table("deck_versions")
    op.drop_index("ix_decks_user_id", "decks")
    op.drop_table("decks")
