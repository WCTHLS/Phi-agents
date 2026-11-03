FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src ./src
# Database migrations, so `docker compose exec api alembic upgrade head` can create the tables.
COPY alembic.ini ./
COPY migrations ./migrations

RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "pharma_ai.main:app", "--host", "0.0.0.0", "--port", "8000"]
