import io
from datetime import datetime
from decimal import Decimal
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Base, TelegramUser
from app.database.repositories import JournalRepository
from app.services.journal_import import (
    JournalImportError,
    parse_import_year,
    parse_journal_file,
)


def test_parses_melstudio_diary() -> None:
    payload = (
        "Дата\tВремя\tДлинный инсулин/Короткий инсулин\tХЕ\tПримечания\t\n"
        "21 мая\t13:09\t-/9\t6\tОбед4\t\n"
        "22 мая\t08:00\t-/-\t-\tВелосипед 30 минут.\t\n"
    ).encode()

    parsed = parse_journal_file(payload, "diary.txt", 2026, ZoneInfo("Europe/Moscow"))

    assert parsed.source == "MelStudio"
    assert len(parsed.entries) == 2
    assert parsed.entries[0].occurred_at == datetime(
        2026, 5, 21, 13, 9, tzinfo=ZoneInfo("Europe/Moscow")
    )
    assert parsed.entries[0].short_insulin_units == Decimal("9")
    assert parsed.entries[0].food == "Обед4; 6 ХЕ"
    assert parsed.entries[1].physical_activity == "Велосипед 30 минут."
    assert parsed.entries[1].duration_minutes == 30


def test_parses_hematonix_ooxml_with_xls_extension() -> None:
    payload = _hematonix_workbook()

    parsed = parse_journal_file(payload, "monitor.xls", 2025, ZoneInfo("Europe/Moscow"))

    assert parsed.source == "Hematonix"
    assert len(parsed.entries) == 2
    assert parsed.entries[0].occurred_at == datetime(
        2026, 5, 21, 9, 45, tzinfo=ZoneInfo("Europe/Moscow")
    )
    assert parsed.entries[0].blood_glucose_mmol_l == Decimal("6.9")
    assert parsed.entries[1].blood_glucose_mmol_l == Decimal("7")


async def test_bulk_import_skips_duplicates(tmp_path) -> None:  # type: ignore[no-untyped-def]
    timezone = ZoneInfo("Europe/Moscow")
    parsed = parse_journal_file(_hematonix_workbook(), "monitor.xls", 2026, timezone)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'import.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(TelegramUser(telegram_user_id=1001, is_admin=False, is_active=True))
        await session.flush()
        first = await JournalRepository(session).add_many(1001, parsed.entries, timezone)
        await session.commit()

    async with sessions() as session:
        second = await JournalRepository(session).add_many(1001, parsed.entries, timezone)
        await session.commit()
        entries = await JournalRepository(session).list_recent(1001)

    assert first == (2, 0)
    assert second == (0, 2)
    assert len(entries) == 2
    await engine.dispose()


def test_import_year_validation() -> None:
    assert parse_import_year(None, 2026) == 2026
    assert parse_import_year("/import 2025", 2026) == 2025
    with pytest.raises(JournalImportError, match="Укажите год"):
        parse_import_year("/import май", 2026)


def _hematonix_workbook() -> bytes:
    shared_strings = [
        "Время",
        "Результат мониторинга, ммоль/л",
        "21.05.2026 09:45",
        "6,9",
        "21.05.2026 09:50",
        "7",
    ]
    strings_xml = "".join(f"<si><t>{value}</t></si>" for value in shared_strings)
    sheet_xml = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
    <row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>
    <row r="3"><c r="A3" t="s"><v>4</v></c><c r="B3" t="s"><v>5</v></c></row>
  </sheetData>
</worksheet>"""
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as workbook:
        workbook.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"{strings_xml}</sst>",
        )
        workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return output.getvalue()
