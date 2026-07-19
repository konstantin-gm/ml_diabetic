FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY desktop_client ./desktop_client
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir .

CMD ["sh", "-c", "alembic upgrade head && python -m app.main"]
