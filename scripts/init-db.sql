-- Runs once when the database container is first created.
-- Only the extensions live here. Tables are created by the Alembic migrations:
--     alembic upgrade head
--     python scripts/seed_dev_data.py
-- (If this file also created tables, the first migration would fail with "already exists".)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
