"""A browser run's deck name and win rate, for History.

Revision ID: 0005_browser_run_label
Revises: 0004_browser_runs
Create Date: 2026-11-15

Both are filled when a run is uploaded. Runs stored before this have them
null, and the listing fills them in from the stored decks and games.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_browser_run_label"
down_revision = "0004_browser_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("browser_runs") as batch:
        batch.add_column(sa.Column("label", sa.String(200), nullable=True))
        batch.add_column(sa.Column("win_rate", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("browser_runs") as batch:
        batch.drop_column("win_rate")
        batch.drop_column("label")
