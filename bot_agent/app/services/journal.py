from __future__ import annotations

from collections.abc import Sequence
from datetime import tzinfo
from decimal import Decimal

from app.agent.schemas import JournalEntryRecord
from app.services.food_export import TELEGRAM_MESSAGE_LIMIT


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
