"""Record which version of the Terms and Privacy Policy a user accepted, and when.

Revision ID: 0003_terms
Revises: 0002_decks_runs
Create Date: 2026-11-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_terms"
down_revision = "0002_decks_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("terms_version", sa.String(16), nullable=True))
        batch.add_column(sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("terms_accepted_at")
        batch.drop_column("terms_version")
