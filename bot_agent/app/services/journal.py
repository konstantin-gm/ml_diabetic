from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo
from decimal import ROUND_HALF_UP, Decimal

from app.agent.schemas import JournalEntryRecord
from app.services.food_export import TELEGRAM_MESSAGE_LIMIT

DEFAULT_STATISTICS_DAYS = 7
MAX_STATISTICS_DAYS = 3650


@dataclass(frozen=True)
class NumericStatistics:
    count: int
    average: Decimal
    median: Decimal
    minimum: Decimal
    maximum: Decimal


@dataclass(frozen=True)
class JournalStatistics:
    entries_count: int
    carbohydrates: NumericStatistics | None
    short_insulin: NumericStatistics | None


def parse_journal_limit(args: str | None, default: int = 20) -> int:
    if not args or not args.strip():
        return default
    try:
        limit = int(args.strip())
    except ValueError as error:
        raise ValueError("Использование: /journal [количество от 1 до 100]") from error
    if not 1 <= limit <= 100:
        raise ValueError("Количество записей должно быть от 1 до 100")
    return limit


def parse_statistics_days(args: str | None, default: int = DEFAULT_STATISTICS_DAYS) -> int:
    if not args or not args.strip():
        return default
    try:
        days = int(args.strip())
    except ValueError as error:
        raise ValueError(
            f"Использование: /stats [количество дней от 1 до {MAX_STATISTICS_DAYS}]"
        ) from error
    if not 1 <= days <= MAX_STATISTICS_DAYS:
        raise ValueError(f"Количество дней должно быть от 1 до {MAX_STATISTICS_DAYS}")
    return days


def statistics_period_bounds(
    now: datetime,
    days: int,
    timezone: tzinfo,
) -> tuple[datetime, datetime]:
    if not 1 <= days <= MAX_STATISTICS_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_STATISTICS_DAYS}")
    local_today = now.astimezone(timezone).date()
    first_date = local_today - timedelta(days=days)
    return (
        datetime.combine(first_date, time.min, tzinfo=timezone),
        datetime.combine(local_today, time.min, tzinfo=timezone),
    )


def calculate_journal_statistics(entries: Sequence[JournalEntryRecord]) -> JournalStatistics:
    carbohydrates = [
        entry.carbohydrates_grams
        for entry in entries
        if entry.carbohydrates_grams is not None
    ]
    short_insulin = [
        entry.short_insulin_units
        for entry in entries
        if entry.short_insulin_units is not None
    ]
    return JournalStatistics(
        entries_count=len(entries),
        carbohydrates=_numeric_statistics(carbohydrates),
        short_insulin=_numeric_statistics(short_insulin),
    )


def format_journal_statistics(statistics: JournalStatistics, days: int) -> str:
    header = f"Статистика журнала за последние {days} дн. Записей: {statistics.entries_count}."
    return "\n\n".join(
        [
            header,
            _format_numeric_statistics("Углеводы", "г", statistics.carbohydrates),
            _format_numeric_statistics(
                "Короткий инсулин",
                "ед.",
                statistics.short_insulin,
            ),
        ]
    )


def format_journal_messages(
    entries: Sequence[JournalEntryRecord],
    display_timezone: tzinfo,
    max_length: int = TELEGRAM_MESSAGE_LIMIT,
) -> list[str]:
    if not entries:
        return ["Ваш журнал пока пуст."]

    header = f"Последние записи журнала: {len(entries)}\n"
    blocks = [_format_entry(entry, display_timezone) for entry in entries]
    return _chunk_blocks(header, blocks, max_length)


def _format_entry(entry: JournalEntryRecord, display_timezone: tzinfo) -> str:
    timestamp = entry.occurred_at.astimezone(display_timezone).strftime("%d.%m.%Y %H:%M")
    values: list[str] = []
    if entry.blood_glucose_mmol_l is not None:
        values.append(f"сахар {_decimal(entry.blood_glucose_mmol_l)} ммоль/л")
    if entry.short_insulin_units is not None:
        values.append(f"короткий инсулин {_decimal(entry.short_insulin_units)} ед.")
    if entry.long_insulin_units is not None:
        values.append(f"длинный инсулин {_decimal(entry.long_insulin_units)} ед.")
    if entry.carbohydrates_grams is not None:
        values.append(f"углеводы {_decimal(entry.carbohydrates_grams)} г")
    if entry.food:
        values.append(f"еда: {entry.food}")
    if entry.physical_activity:
        values.append(f"активность: {entry.physical_activity}")
    if entry.duration_minutes is not None:
        values.append(f"продолжительность {entry.duration_minutes} мин.")
    return f"{timestamp}\n" + "; ".join(values)


def _chunk_blocks(header: str, blocks: Sequence[str], max_length: int) -> list[str]:
    if max_length <= len(header):
        raise ValueError("max_length is too small for the header")

    messages: list[str] = []
    current = header
    for block in blocks:
        addition = f"\n{block}"
        if len(current) + len(addition) > max_length:
            messages.append(current)
            current = block
        else:
            current += addition
    messages.append(current)
    return messages


def _decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _numeric_statistics(values: Sequence[Decimal]) -> NumericStatistics | None:
    if not values:
        return None
    return NumericStatistics(
        count=len(values),
        average=(sum(values, Decimal(0)) / len(values)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        ),
        median=_median(values).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        minimum=min(values),
        maximum=max(values),
    )


def _median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _format_numeric_statistics(
    title: str,
    unit: str,
    statistics: NumericStatistics | None,
) -> str:
    if statistics is None:
        return f"{title}: нет данных."
    return (
        f"{title} ({statistics.count} знач.):\n"
        f"среднее {_decimal(statistics.average)} {unit}; "
        f"медиана {_decimal(statistics.median)} {unit}; "
        f"мин. {_decimal(statistics.minimum)} {unit}; "
        f"макс. {_decimal(statistics.maximum)} {unit}."
    )
