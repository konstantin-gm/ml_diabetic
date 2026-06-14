"""Create foods and food aliases tables.

Revision ID: 20260613_0001
Revises:
Create Date: 2026-06-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260613_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "foods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("canonical_name", sa.String(length=200), nullable=False),
        sa.Column("ru_name", sa.String(length=200), nullable=False),
        sa.Column("en_name", sa.String(length=200), nullable=True),
        sa.Column("carbs_per_100g", sa.Numeric(6, 2), nullable=False),
        sa.Column("protein_per_100g", sa.Numeric(6, 2), nullable=True),
        sa.Column("fat_per_100g", sa.Numeric(6, 2), nullable=True),
        sa.Column("kcal_per_100g", sa.Numeric(7, 2), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("carbs_per_100g >= 0", name=op.f("ck_foods_carbs_nonnegative")),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name=op.f("ck_foods_confidence")),
        sa.PrimaryKeyConstraint("id", name="pk_foods"),
        sa.UniqueConstraint("canonical_name", name="uq_foods_canonical_name"),
    )
    op.create_index("ix_foods_ru_name", "foods", ["ru_name"])

    op.create_table(
        "food_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("food_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(
            ["food_id"],
            ["foods.id"],
            name="fk_food_aliases_food_id_foods",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_food_aliases"),
        sa.UniqueConstraint("alias", name="uq_food_aliases_alias"),
    )
    op.create_index("ix_food_aliases_food_id", "food_aliases", ["food_id"])


def downgrade() -> None:
    op.drop_index("ix_food_aliases_food_id", table_name="food_aliases")
    op.drop_table("food_aliases")
    op.drop_index("ix_foods_ru_name", table_name="foods")
    op.drop_table("foods")
