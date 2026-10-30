"""Checks that keep secrets out of Git and Docker images, and keep the settings files consistent.

These read only committed files, so they run anywhere (CI, your machine, the dev container).
The real, local .env file is deliberately not read.
"""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.dev.yml")


def _lines(name: str) -> list[str]:
    path = REPO_ROOT / name
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]


def _env_example() -> dict[str, str]:
    values = {}
    for line in _lines(".env.example"):
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def test_env_file_is_ignored_by_git_but_the_example_is_not() -> None:
    lines = _lines(".gitignore")
    assert ".env" in lines
    assert "!.env.example" in lines


def test_env_file_is_kept_out_of_docker_images() -> None:
    lines = _lines(".dockerignore")
    assert ".env" in lines
    assert "!.env.example" in lines


def test_every_variable_used_by_compose_is_listed_in_env_example() -> None:
    used: set[str] = set()
    for name in COMPOSE_FILES:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        used |= set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", text))
    missing = sorted(used - set(_env_example()))
    assert missing == [], f"add these to .env.example: {missing}"


def test_env_example_contains_no_real_secrets() -> None:
    """Passwords in the committed example must be obvious placeholders."""
    for key, value in _env_example().items():
        if re.search(r"PASSWORD|SECRET|TOKEN|API_KEY", key) and value:
            assert "change_me" in value, f"{key} looks like a real secret in .env.example"
        # A password inside a connection string, e.g. postgresql://user:password@host/db
        match = re.search(r"://[^:/@\s]+:([^@\s]+)@", value)
        if match:
            assert "change_me" in match.group(1), f"{key} contains a real-looking password"


def test_dev_container_cannot_touch_the_development_database_with_tests() -> None:
    config = yaml.safe_load((REPO_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    dev = config["services"]["dev"]
    env = dev["environment"]

    # The tests erase their database, so it must be a different one from the working database.
    assert env["DATABASE_URL"].endswith("/${POSTGRES_DB:-pharma_ai}")
    assert env["TEST_DATABASE_URL"].endswith("/pharma_test")
    # Inside Docker the database is reached by its service name, not "localhost".
    assert "@db:5432/" in env["DATABASE_URL"]
    assert "@db:5432/" in env["TEST_DATABASE_URL"]


def test_dev_container_is_opt_in_and_publishes_no_ports() -> None:
    config = yaml.safe_load((REPO_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    dev = config["services"]["dev"]
    assert dev["profiles"] == ["dev"]  # `docker compose up` does not start it
    assert "ports" not in dev
    assert ".:/workspace" in dev["volumes"]
