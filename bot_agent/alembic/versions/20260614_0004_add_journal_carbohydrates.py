"""Add carbohydrate grams to journal entries.

Revision ID: 20260614_0004
Revises: 20260614_0003
Create Date: 2026-06-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260614_0004"
down_revision: str | None = "20260614_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "journal_entries",
        sa.Column("carbohydrates_grams", sa.Numeric(9, 2), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_journal_entries_carbohydrates_nonnegative"),
        "journal_entries",
        "carbohydrates_grams IS NULL OR carbohydrates_grams >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_journal_entries_carbohydrates_nonnegative"),
        "journal_entries",
        type_="check",
    )
    op.drop_column("journal_entries", "carbohydrates_grams")
