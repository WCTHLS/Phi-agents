"""Tests for S1-06 containerized development environment configuration."""

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_compose_file_defines_required_services() -> None:
    """Verify docker-compose.yml defines db, tika, keycloak, api, and nginx with healthchecks."""
    compose_path = REPO_ROOT / "docker-compose.yml"
    assert compose_path.exists()

    config = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = config.get("services", {})

    expected_services = {"db", "tika", "keycloak", "api", "nginx"}
    assert expected_services.issubset(set(services.keys()))

    for svc_name in expected_services:
        assert "healthcheck" in services[svc_name], f"Missing healthcheck in service: {svc_name}"


def test_init_db_seed_script_exists_and_creates_core_tables() -> None:
    """Verify scripts/init-db.sql defines vector extension, core tables, and seed records."""
    sql_path = REPO_ROOT / "scripts" / "init-db.sql"
    assert sql_path.exists()

    sql_text = sql_path.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector;" in sql_text
    assert "CREATE TABLE IF NOT EXISTS projects" in sql_text
    assert "CREATE TABLE IF NOT EXISTS approved_sources" in sql_text
    assert "CREATE TABLE IF NOT EXISTS audit_events" in sql_text
    assert "PRJ-001" in sql_text


def test_keycloak_dev_realm_config_is_valid_json() -> None:
    """Verify infra/keycloak/pharma-realm.json is valid and defines scientist/reviewer/admin roles."""
    realm_path = REPO_ROOT / "infra" / "keycloak" / "pharma-realm.json"
    assert realm_path.exists()

    realm_data = json.loads(realm_path.read_text(encoding="utf-8"))
    assert realm_data["realm"] == "pharma"
    role_names = {r["name"] for r in realm_data["roles"]["realm"]}
    assert {"scientist", "reviewer", "admin"}.issubset(role_names)


def test_nginx_and_makefile_exist() -> None:
    """Verify Nginx reverse proxy config, Dockerfile, Makefile, and .env.example exist."""
    assert (REPO_ROOT / "infra" / "nginx" / "nginx.conf").exists()
    assert (REPO_ROOT / "Dockerfile").exists()
    assert (REPO_ROOT / "Makefile").exists()
    assert (REPO_ROOT / ".env.example").exists()
