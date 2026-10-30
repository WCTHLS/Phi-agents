#!/usr/bin/env python3
"""Print a signed sign-in token for local development, to try the API without Keycloak.

Usage (from the repository root, inside the dev container so the settings are loaded):
    docker compose run --rm dev python scripts/dev_token.py --role admin
    docker compose run --rm dev python scripts/dev_token.py --role scientist --project PRJ-001
or on your own machine, with the project installed (pip install -e ".[dev]") and a .env file:
    python scripts/dev_token.py --role admin

Then, in the API docs (http://localhost:8000/docs), click "Authorize" and paste the token, or send
it as a header:  Authorization: Bearer <token>

Safety: this works only when AUTH_MODE=dev, ENVIRONMENT is "local" or "test" and AUTH_DEV_SECRET
is set (the same rules the API itself applies). It cannot make a token for a real deployment.
"""

import argparse
import sys
import time
from pathlib import Path

import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pharma_ai.core.config import Settings, settings
from pharma_ai.infrastructure.security.auth import DEV_ALLOWED_ENVIRONMENTS


def mint_dev_token(
    cfg: Settings,
    *,
    sub: str,
    email: str | None,
    roles: list[str],
    projects: list[str],
    minutes: int = 60,
) -> str:
    """A token the API accepts in dev mode. Raises ValueError if dev tokens are not allowed."""
    if cfg.auth_mode != "dev":
        raise ValueError(
            "AUTH_MODE is not 'dev'. Set AUTH_MODE=dev in .env (see scripts/make_env.py)."
        )
    if cfg.environment not in DEV_ALLOWED_ENVIRONMENTS:
        raise ValueError(
            f"Refusing: ENVIRONMENT is {cfg.environment!r}; dev tokens are for local or test only."
        )
    if not cfg.auth_dev_secret:
        raise ValueError("AUTH_DEV_SECRET is not set. Run scripts/make_env.py or set it in .env.")
    if minutes < 1:
        raise ValueError("minutes must be at least 1")
    now = int(time.time())
    claims = {
        "sub": sub,
        "iss": cfg.oidc_issuer_url,
        "iat": now,
        "exp": now + minutes * 60,
        "email": email or f"{sub}@pharma.local",
        "realm_access": {"roles": roles},
        "projects": projects,
    }
    return jwt.encode(claims, cfg.auth_dev_secret, algorithm="HS256")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sub", default="dev-user", help="stable user ID (default: dev-user)")
    parser.add_argument("--email", default=None, help="default: <sub>@pharma.local")
    parser.add_argument(
        "--role",
        action="append",
        dest="roles",
        help="scientist, reviewer, qa or admin (repeatable; default: scientist)",
    )
    parser.add_argument(
        "--project",
        action="append",
        dest="projects",
        help="project the user belongs to (repeatable; default: PRJ-001)",
    )
    parser.add_argument(
        "--minutes", type=int, default=60, help="how long the token is valid (default: 60)"
    )
    args = parser.parse_args(argv)
    try:
        token = mint_dev_token(
            settings,
            sub=args.sub,
            email=args.email,
            roles=args.roles or ["scientist"],
            projects=args.projects or ["PRJ-001"],
            minutes=args.minutes,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
