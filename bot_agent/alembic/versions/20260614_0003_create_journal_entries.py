"""Create per-user journal entries table.

Revision ID: 20260614_0003
Revises: 20260613_0002
Create Date: 2026-06-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260614_0003"
down_revision: str | None = "20260613_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("short_insulin_units", sa.Numeric(7, 2), nullable=True),
        sa.Column("long_insulin_units", sa.Numeric(7, 2), nullable=True),
        sa.Column("food", sa.Text(), nullable=True),
        sa.Column("physical_activity", sa.Text(), nullable=True),
        sa.Column("blood_glucose_mmol_l", sa.Numeric(6, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes > 0",
            name=op.f("ck_journal_entries_duration_positive"),
        ),
        sa.CheckConstraint(
            "short_insulin_units IS NULL OR short_insulin_units >= 0",
            name=op.f("ck_journal_entries_short_insulin_nonnegative"),
        ),
        sa.CheckConstraint(
            "long_insulin_units IS NULL OR long_insulin_units >= 0",
            name=op.f("ck_journal_entries_long_insulin_nonnegative"),
        ),
        sa.CheckConstraint(
            "blood_glucose_mmol_l IS NULL OR blood_glucose_mmol_l > 0",
            name=op.f("ck_journal_entries_blood_glucose_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.telegram_user_id"],
            name="fk_journal_entries_telegram_user_id_telegram_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_journal_entries"),
    )
    op.create_index(
        "ix_journal_entries_telegram_user_id",
        "journal_entries",
        ["telegram_user_id"],
    )
    op.create_index("ix_journal_entries_occurred_at", "journal_entries", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_journal_entries_occurred_at", table_name="journal_entries")
    op.drop_index("ix_journal_entries_telegram_user_id", table_name="journal_entries")
    op.drop_table("journal_entries")
