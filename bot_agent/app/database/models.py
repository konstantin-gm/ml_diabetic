from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Food(Base):
    __tablename__ = "foods"
    __table_args__ = (
        CheckConstraint("carbs_per_100g >= 0", name="carbs_nonnegative"),
        CheckConstraint(
            "glycemic_index IS NULL OR (glycemic_index >= 0 AND glycemic_index <= 100)",
            name="glycemic_index_range",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(200), unique=True)
    ru_name: Mapped[str] = mapped_column(String(200), index=True)
    en_name: Mapped[str | None] = mapped_column(String(200))

    carbs_per_100g: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    protein_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    fat_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    kcal_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    glycemic_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    source: Mapped[str] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    aliases: Mapped[list[FoodAlias]] = relationship(
        back_populates="food", cascade="all, delete-orphan", lazy="selectin"
    )


class FoodAlias(Base):
    __tablename__ = "food_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    food_id: Mapped[int] = mapped_column(ForeignKey("foods.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(200), unique=True)

    food: Mapped[Food] = relationship(back_populates="aliases")


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true", index=True)
    added_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes > 0",
            name="duration_positive",
        ),
        CheckConstraint(
            "short_insulin_units IS NULL OR short_insulin_units >= 0",
            name="short_insulin_nonnegative",
        ),
        CheckConstraint(
            "long_insulin_units IS NULL OR long_insulin_units >= 0",
            name="long_insulin_nonnegative",
        ),
        CheckConstraint(
            "blood_glucose_mmol_l IS NULL OR blood_glucose_mmol_l > 0",
            name="blood_glucose_positive",
        ),
        CheckConstraint(
            "carbohydrates_grams IS NULL OR carbohydrates_grams >= 0",
            name="carbohydrates_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.telegram_user_id", ondelete="CASCADE"),
        index=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_minutes: Mapped[int | None]
    short_insulin_units: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    long_insulin_units: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    food: Mapped[str | None] = mapped_column(Text)
    carbohydrates_grams: Mapped[Decimal | None] = mapped_column(Numeric(9, 2))
    physical_activity: Mapped[str | None] = mapped_column(Text)
    blood_glucose_mmol_l: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
