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


def test_init_db_script_enables_extensions_and_leaves_tables_to_migrations() -> None:
    """scripts/init-db.sql only enables extensions; Alembic migrations create the tables (S1-07)."""
    sql_path = REPO_ROOT / "scripts" / "init-db.sql"
    assert sql_path.exists()

    sql_text = sql_path.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector;" in sql_text
    # If this script also created tables, the first migration would fail on a fresh database.
    assert "CREATE TABLE" not in sql_text.upper()
    assert (REPO_ROOT / "alembic.ini").exists()
    assert (REPO_ROOT / "migrations" / "versions" / "0001_schema_v1.py").exists()


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


def test_ui_container_and_frontend_dockerfile_exist() -> None:
    """Verify frontend Dockerfile, nginx.conf, and docker-compose ui service (S2-04)."""
    dockerfile_path = REPO_ROOT / "frontend" / "Dockerfile"
    assert dockerfile_path.exists()
    dockerfile_text = dockerfile_path.read_text(encoding="utf-8")
    assert "FROM node:20-alpine AS builder" in dockerfile_text
    assert "FROM nginx:1.27-alpine" in dockerfile_text

    nginx_path = REPO_ROOT / "frontend" / "nginx.conf"
    assert nginx_path.exists()
    nginx_text = nginx_path.read_text(encoding="utf-8")
    assert "try_files $uri $uri/ /index.html" in nginx_text
    assert "/health" in nginx_text

    assert (REPO_ROOT / "frontend" / ".dockerignore").exists()

    compose_path = REPO_ROOT / "docker-compose.yml"
    config = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert "ui" in config.get("services", {})


def test_ci_workflow_includes_tika_service_and_container_images_job() -> None:
    """Verify CI workflow defines Tika service in tests and container-images build job (S2-04)."""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists()

    ci_config = yaml.safe_load(ci_path.read_text(encoding="utf-8"))
    jobs = ci_config.get("jobs", {})

    # 1. Tika service container in integration tests
    test_services = jobs.get("test", {}).get("services", {})
    assert "tika" in test_services
    assert test_services["tika"]["image"] == "apache/tika:latest"

    # 2. Container image build job
    assert "container-images" in jobs
    container_job = jobs["container-images"]
    assert "pharma-ai-api" in str(container_job)
    assert "pharma-ai-ui" in str(container_job)
