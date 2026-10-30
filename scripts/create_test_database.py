#!/usr/bin/env python3
"""Create the empty database used by the database tests (name taken from TEST_DATABASE_URL).

The database tests erase and rebuild the tables of the database they run against, so they must
never point at your real development database. This script makes a separate one next to it.

Usage (inside the dev container the environment is already set up):
    docker compose run --rm dev python scripts/create_test_database.py
or on your machine, with TEST_DATABASE_URL set:
    python scripts/create_test_database.py

Safe to run more than once: an existing database is left alone.
"""

import os
import re
import sys

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def main() -> int:
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        print("TEST_DATABASE_URL is not set.", file=sys.stderr)
        return 2
    url = make_url(raw)
    name = url.database or ""
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        print(f"Refusing unusual database name: {name!r}", file=sys.stderr)
        return 2

    # Connect to the server's built-in "postgres" database to create the new one.
    with psycopg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        dbname="postgres",
        autocommit=True,
    ) as conn:
        exists = conn.execute("select 1 from pg_database where datname = %s", (name,)).fetchone()
        if exists:
            print(f"Database {name!r} already exists.")
        else:
            conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
            print(f"Created database {name!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
