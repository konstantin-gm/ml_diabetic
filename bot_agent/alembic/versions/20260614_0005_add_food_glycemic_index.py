"""Add glycemic index to foods.

Revision ID: 20260614_0005
Revises: 20260614_0004
Create Date: 2026-06-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260614_0005"
down_revision: str | None = "20260614_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "foods",
        sa.Column("glycemic_index", sa.Numeric(5, 2), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_foods_glycemic_index_range"),
        "foods",
        "glycemic_index IS NULL OR (glycemic_index >= 0 AND glycemic_index <= 100)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_foods_glycemic_index_range"),
        "foods",
        type_="check",
    )
    op.drop_column("foods", "glycemic_index")
