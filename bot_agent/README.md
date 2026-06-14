# Diabetes Food Bot

Stage 1 Telegram assistant that answers Russian-language questions about food
carbohydrates. It checks PostgreSQL first, uses OpenAI web search for missing
foods, and stores the normalized result for later requests.
Users can also provide carbohydrate values from a product label. The bot
normalizes them to 100 grams and stores or updates the local food record.

Examples:

```text
В моём хлебе 42 г углеводов на 100 г
В 30 г батончика 18 г углеводов
```

Telegram commands:

```text
/foods       Show all saved foods in chat
/export_csv  Download the complete food database as CSV
```

Access is restricted to users stored in PostgreSQL. Set your Telegram numeric
ID as the initial administrator before the first start:

```env
TELEGRAM_ADMIN_IDS=123456789
```

An unauthorized user receives their numeric ID from the bot. The administrator
can then grant access in Telegram:

```text
/add_user 987654321 Иван
/users
```

Each authorized user has a private journal:

```text
/log сахар 6.4 ммоль/л, короткий инсулин 3 ед., гречка, прогулка 30 минут
/journal
/journal 50
/import 2026
```

Journal entries may contain a timestamp, duration, short and long insulin,
food, physical activity, and blood glucose in mmol/L. Insulin values are stored
as reported; the bot does not calculate or recommend doses.

Send a Hematonix `.xls/.xlsx` monitor export or a MelStudio `.txt` diary as a
Telegram document. For diary dates without a year, add `/import 2026` as the
document caption; otherwise the current year is used. Re-importing identical
records does not create duplicates.

The bot only calculates carbohydrates. It does not recommend insulin doses or
provide medical advice.

## Run with Docker Compose

```bash
cp .env.example .env
# Fill TELEGRAM_BOT_TOKEN and OPENAI_API_KEY in .env
docker compose up --build
```

The bot container applies Alembic migrations before starting long polling.
PostgreSQL is exposed on `localhost:5434` by default to avoid conflicts with a
locally installed PostgreSQL. Override it with `POSTGRES_PORT` when needed.

## Local development

Python 3.12+ and a running PostgreSQL instance are required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
alembic upgrade head
python -m app.main
```

Run checks:

```bash
ruff check .
mypy app
pytest
```
