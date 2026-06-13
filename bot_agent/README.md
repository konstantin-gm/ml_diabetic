# Diabetes Food Bot

Stage 1 Telegram assistant that answers Russian-language questions about food
carbohydrates. It checks PostgreSQL first, uses OpenAI web search for missing
foods, and stores the normalized result for later requests.

The bot only calculates carbohydrates. It does not recommend insulin doses or
provide medical advice.

## Run with Docker Compose

```bash
cp .env.example .env
# Fill TELEGRAM_BOT_TOKEN and OPENAI_API_KEY in .env
docker compose up --build
```

The bot container applies Alembic migrations before starting long polling.

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

