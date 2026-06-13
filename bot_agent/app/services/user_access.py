from __future__ import annotations

from collections.abc import Sequence

from app.agent.schemas import TelegramUserRecord
from app.services.food_export import TELEGRAM_MESSAGE_LIMIT


def parse_add_user_args(args: str | None) -> tuple[int, str | None]:
    if not args or not args.strip():
        raise ValueError("Укажите Telegram ID: /add_user 123456789 Имя")

    parts = args.strip().split(maxsplit=1)
    try:
        telegram_user_id = int(parts[0])
    except ValueError as error:
        raise ValueError("Telegram ID должен быть положительным целым числом") from error
    if telegram_user_id <= 0:
        raise ValueError("Telegram ID должен быть положительным целым числом")

    full_name = parts[1].strip() if len(parts) > 1 else None
    return telegram_user_id, full_name or None


def format_user_messages(
    users: Sequence[TelegramUserRecord], max_length: int = TELEGRAM_MESSAGE_LIMIT
) -> list[str]:
    if not users:
        return ["Белый список пока пуст."]

    header = f"Пользователи в белом списке: {len(users)}\n"
    lines = []
    for user in users:
        name = user.full_name or "без имени"
        username = f" @{user.username}" if user.username else ""
        role = "администратор" if user.is_admin else "пользователь"
        status = "активен" if user.is_active else "отключён"
        lines.append(f"{user.telegram_user_id} — {name}{username} ({role}, {status})")
    return _chunk_lines(header, lines, max_length)


def _chunk_lines(header: str, lines: Sequence[str], max_length: int) -> list[str]:
    if max_length <= len(header):
        raise ValueError("max_length is too small for the header")

    messages: list[str] = []
    current = header
    for line in lines:
        addition = f"\n{line}"
        if len(current) + len(addition) > max_length:
            messages.append(current)
            current = line
        else:
            current += addition
    messages.append(current)
    return messages
