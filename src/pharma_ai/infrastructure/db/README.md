# Database infrastructure

PostgreSQL + pgvector. Schema v1 is defined by the Alembic migration in `migrations/versions/` and described in [docs/architecture/erd.md](../../../../docs/architecture/erd.md).

## Set up a local database

```powershell
docker compose up -d db          # creates the database and enables the extensions only
alembic upgrade head             # creates the tables
python scripts/seed_dev_data.py  # roles, two sample projects, and the approved-source register
```

Use `alembic downgrade base` to remove the tables again. If the database volume was created before this change (it then contains the old tables), recreate it with `docker compose down -v`.

## Code

- `models.py`: the tables as SQLAlchemy models (a test checks they match the migration).
- `repositories.py`: project-scoped reads; every query that returns project content takes the caller's allowed project IDs.
- `seed.py`: loads `data/sources/corpus_manifest.csv` into the `sources` table.

## Tests that use a real database

They skip unless `TEST_DATABASE_URL` points at an empty throwaway database (see the top of `tests/test_db_integration.py`).
