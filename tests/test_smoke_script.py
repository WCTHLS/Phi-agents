"""Tests for scripts/smoke_test.py, the end-to-end check of a running system.

The script is run here against the in-process API (no network), so that if the API's behaviour
and the smoke test ever disagree, CI notices.
"""

import importlib.util
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from pharma_ai.core.config import settings
from pharma_ai.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "smoke_test", REPO_ROOT / "scripts" / "smoke_test.py"
)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

client = TestClient(app)


def _call(method: str, path: str, token: str | None = None, body: object = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.request(method, path, headers=headers, json=body)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text


def _mint(**kwargs):
    return smoke.make_token(settings.auth_dev_secret, settings.oidc_issuer_url, **kwargs)


def _run() -> tuple[bool, list[tuple[bool, str]]]:
    lines: list[tuple[bool, str]] = []
    ok = smoke.run_smoke(_call, _mint, lambda passed, name, detail="": lines.append((passed, name)))
    return ok, lines


def test_a_hand_built_token_is_a_valid_jwt_that_the_api_accepts() -> None:
    token = _mint(sub="t1", roles=["scientist"], projects=["PRJ-001"])
    claims = jwt.decode(
        token, settings.auth_dev_secret, algorithms=["HS256"], issuer=settings.oidc_issuer_url
    )
    assert claims["sub"] == "t1" and claims["realm_access"]["roles"] == ["scientist"]
    assert (
        client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )


def test_a_token_signed_with_the_wrong_key_is_refused() -> None:
    token = smoke.make_token(
        "wrong-key-wrong-key-wrong-key-wrong-key",
        settings.oidc_issuer_url,
        sub="t",
        roles=[],
        projects=[],
    )
    assert (
        client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )


def test_the_smoke_test_passes_against_the_api() -> None:
    ok, lines = _run()
    failed = [name for passed, name in lines if not passed]
    assert ok and not failed, failed
    assert len(lines) >= 13


def test_the_smoke_test_notices_if_the_question_text_were_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pharma_ai.api.routes import research

    monkeypatch.setattr(
        research,
        "fingerprint_text",
        lambda text: {"sha256": "x", "length": len(text), "text": text},
    )
    ok, lines = _run()
    assert not ok
    assert any(not passed and "fingerprint" in name for passed, name in lines)


def test_the_smoke_test_notices_if_a_client_could_write_audit_events() -> None:
    @app.post("/api/v1/audit/events")
    def forged() -> dict:
        return {}

    try:
        ok, lines = _run()
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if not (getattr(r, "endpoint", None) is forged)
        ]
    assert not ok
    assert any(not passed and "fake audit event" in name for passed, name in lines)


def test_reading_the_env_file_ignores_comments_and_blank_lines(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\n\nAUTH_MODE=dev\nAUTH_DEV_SECRET=abc=def\n", encoding="utf-8")
    assert smoke.load_env(env) == {"AUTH_MODE": "dev", "AUTH_DEV_SECRET": "abc=def"}
    assert smoke.load_env(tmp_path / "missing.env") == {}


def test_it_explains_itself_when_dev_sign_in_is_not_configured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = tmp_path / ".env"
    env.write_text("AUTH_MODE=oidc\n", encoding="utf-8")
    assert smoke.main(["--env", str(env)]) == 2
    assert "make_env.py" in capsys.readouterr().out
