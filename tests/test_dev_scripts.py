"""Tests for the onboarding scripts: make_env.py (create .env) and dev_token.py (local sign-in token)."""

import importlib.util
import re
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from pharma_ai.core.config import Settings, settings
from pharma_ai.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_env = _load("make_env")
dev_token = _load("dev_token")


def _parse(env_text: str) -> dict[str, str]:
    values = {}
    for line in env_text.splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


EXAMPLE = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


# --- make_env.py -----------------------------------------------------------------------------


def test_generated_env_has_no_placeholder_passwords() -> None:
    text = make_env.build_env(EXAMPLE)
    assert "change_me" not in text


def test_the_database_password_matches_everywhere_it_is_used() -> None:
    values = _parse(make_env.build_env(EXAMPLE))
    password = values["POSTGRES_PASSWORD"]
    assert len(password) >= 24
    assert f":{password}@" in values["DATABASE_URL"]
    assert f":{password}@" in values["TEST_DATABASE_URL"]  # switched on by the script


def test_other_secrets_are_random_and_different_from_each_other() -> None:
    values = _parse(make_env.build_env(EXAMPLE))
    secrets_ = {
        values["POSTGRES_PASSWORD"],
        values["KEYCLOAK_ADMIN_PASSWORD"],
        values["AUTH_DEV_SECRET"],
    }
    assert len(secrets_) == 3
    assert len(values["AUTH_DEV_SECRET"]) >= 40


def test_two_runs_give_different_secrets() -> None:
    first, second = (_parse(make_env.build_env(EXAMPLE)) for _ in range(2))
    assert first["POSTGRES_PASSWORD"] != second["POSTGRES_PASSWORD"]
    assert first["AUTH_DEV_SECRET"] != second["AUTH_DEV_SECRET"]


def test_secrets_are_safe_inside_a_database_url() -> None:
    for _ in range(20):
        assert re.fullmatch(
            r"[A-Za-z0-9_-]+", _parse(make_env.build_env(EXAMPLE))["POSTGRES_PASSWORD"]
        )


def test_every_setting_in_the_example_is_still_present() -> None:
    assert set(_parse(EXAMPLE)) <= set(_parse(make_env.build_env(EXAMPLE)))


def test_the_dev_tools_container_is_switched_on() -> None:
    values = _parse(make_env.build_env(EXAMPLE))
    assert values["COMPOSE_PATH_SEPARATOR"] == ","
    assert values["COMPOSE_FILE"] == "docker-compose.yml,docker-compose.dev.yml"


def test_auth_mode_follows_the_option() -> None:
    assert _parse(make_env.build_env(EXAMPLE, auth_mode="dev"))["AUTH_MODE"] == "dev"
    assert _parse(make_env.build_env(EXAMPLE, auth_mode="oidc"))["AUTH_MODE"] == "oidc"
    with pytest.raises(ValueError):
        make_env.build_env(EXAMPLE, auth_mode="open")


def test_generated_env_is_a_valid_configuration_for_the_app() -> None:
    values = _parse(make_env.build_env(EXAMPLE))
    cfg = Settings(
        _env_file=None,
        **{k.lower(): v for k, v in values.items() if k.lower() in Settings.model_fields},
    )
    assert cfg.auth_mode == "dev" and cfg.auth_dev_secret
    assert cfg.audit_backend == "database"


def test_every_compose_variable_gets_a_value() -> None:
    used = set()
    for name in ("docker-compose.yml", "docker-compose.dev.yml"):
        used |= set(
            re.findall(r"\$\{([A-Z][A-Z0-9_]*)", (REPO_ROOT / name).read_text(encoding="utf-8"))
        )
    assert used <= set(_parse(make_env.build_env(EXAMPLE)))


def test_an_existing_env_file_is_not_overwritten(tmp_path: Path) -> None:
    out = tmp_path / ".env"
    out.write_text("KEEP=me\n", encoding="utf-8")
    code = make_env.main(["--example", str(REPO_ROOT / ".env.example"), "--output", str(out)])
    assert code == 1
    assert out.read_text(encoding="utf-8") == "KEEP=me\n"


def test_force_replaces_an_existing_env_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / ".env"
    out.write_text("KEEP=me\n", encoding="utf-8")
    code = make_env.main(
        ["--example", str(REPO_ROOT / ".env.example"), "--output", str(out), "--force"]
    )
    assert code == 0
    assert "POSTGRES_PASSWORD=" in out.read_text(encoding="utf-8")
    password = _parse(out.read_text(encoding="utf-8"))["POSTGRES_PASSWORD"]
    assert password not in capsys.readouterr().out  # secrets are never printed


# --- dev_token.py ----------------------------------------------------------------------------


def _cfg(**overrides: object) -> Settings:
    base = {
        "auth_mode": "dev",
        "environment": "local",
        "auth_dev_secret": "x" * 48,
        "oidc_issuer_url": "http://localhost:8080/realms/pharma",
    }
    return Settings(_env_file=None, **{**base, **overrides})


def _mint(cfg: Settings, **kwargs: object) -> str:
    args = {
        "sub": "tester",
        "email": None,
        "roles": ["scientist"],
        "projects": ["PRJ-001"],
        "minutes": 5,
    }
    return dev_token.mint_dev_token(cfg, **{**args, **kwargs})


def test_token_carries_who_what_and_for_how_long() -> None:
    cfg = _cfg()
    claims = jwt.decode(
        _mint(cfg, roles=["admin", "reviewer"]),
        cfg.auth_dev_secret,
        algorithms=["HS256"],
        issuer=cfg.oidc_issuer_url,
    )
    assert claims["sub"] == "tester"
    assert claims["email"] == "tester@pharma.local"
    assert claims["realm_access"]["roles"] == ["admin", "reviewer"]
    assert claims["projects"] == ["PRJ-001"]
    assert 4 * 60 <= claims["exp"] - claims["iat"] <= 5 * 60


def test_the_api_accepts_a_token_from_the_script() -> None:
    token = _mint(settings, sub="script-user", roles=["scientist"], projects=["PRJ-001"])
    response = TestClient(app).get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert [p["project_id"] for p in response.json()] == ["PRJ-001"]


def test_an_admin_token_can_read_the_audit_log() -> None:
    token = _mint(settings, roles=["admin"])
    response = TestClient(app).get(
        "/api/v1/audit/events", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"environment": "production"}, "local or test only"),
        ({"auth_mode": "oidc"}, "not 'dev'"),
        ({"auth_dev_secret": None}, "AUTH_DEV_SECRET"),
    ],
)
def test_it_refuses_to_make_tokens_in_unsafe_settings(overrides: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _mint(_cfg(**overrides))


def test_it_refuses_a_non_positive_lifetime() -> None:
    with pytest.raises(ValueError):
        _mint(_cfg(), minutes=0)
