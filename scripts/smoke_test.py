#!/usr/bin/env python3
"""End-to-end smoke test of the RUNNING system: API and database together.

Plain language: a smoke test is a quick check that the main path works at all. This one plays a
scientist and an admin against the real API over real HTTP, then reads the audit log back, so it
proves that sign-in, project access, the research endpoint and the audit trail in the database all
work together. It needs only Python 3 (no packages) and makes no assumptions about Docker.

Before running it, start the system (see README, Option A): the database with its tables, and the API
on port 8000, with AUTH_MODE=dev in your .env (scripts/make_env.py does that).

Usage (from the repository root):
    python scripts/smoke_test.py                       # Windows: py scripts/smoke_test.py
    python scripts/smoke_test.py --base-url http://127.0.0.1:8001

Each run adds a handful of audit rows for a made-up person ("smoke-xxxxxx"). The audit trail is
append-only, so they stay: run this against a development database, never a real one.
Exit code 0 means every check passed.
"""

import argparse
import base64
import hashlib
import hmac
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ISSUER = "http://localhost:8080/realms/pharma"
QUESTION_MARKER = "ZX-9981"  # must never appear in the audit trail

Call = Callable[..., tuple[int, object]]
Mint = Callable[..., str]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_token(secret: str, issuer: str, *, sub: str, roles: list[str], projects: list[str]) -> str:
    """A development sign-in token (HS256), built with the standard library only."""
    now = int(time.time())
    claims = {
        "sub": sub,
        "iss": issuer,
        "iat": now,
        "exp": now + 600,
        "email": f"{sub}@pharma.local",
        "realm_access": {"roles": roles},
        "projects": projects,
    }
    head = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signature = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64url(signature)}"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def http_call(base_url: str) -> Call:
    """Real HTTP requests to the running API."""

    def call(
        method: str, path: str, token: str | None = None, body: object = None
    ) -> tuple[int, object]:
        request = urllib.request.Request(base_url.rstrip("/") + path, method=method)
        request.add_header("Accept", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        data = None
        if body is not None:
            request.add_header("Content-Type", "application/json")
            data = json.dumps(body).encode()
        try:
            with urllib.request.urlopen(request, data=data, timeout=20) as response:
                raw, status = response.read(), response.status
        except urllib.error.HTTPError as err:
            raw, status = err.read(), err.code
        try:
            return status, json.loads(raw or b"{}")
        except ValueError:
            return status, raw.decode("utf-8", "replace")

    return call


def run_smoke(call: Call, mint: Mint, report: Callable[[bool, str, str], None]) -> bool:
    """Run every check. ``call`` sends a request; ``mint`` makes a token; ``report`` prints a result."""
    results: list[bool] = []

    def check(ok: bool, name: str, detail: str = "") -> bool:
        report(ok, name, detail)
        results.append(ok)
        return ok

    person = f"smoke-{secrets.token_hex(3)}"
    scientist = mint(sub=person, roles=["scientist"], projects=["PRJ-001"])
    admin = mint(sub=f"{person}-admin", roles=["admin"], projects=["PRJ-001"])
    question = f"Does excipient {QUESTION_MARKER} in a confidential tablet raise a nitrite risk?"

    status, body = call("GET", "/health")
    check(status == 200 and body == {"status": "ok"}, "API is up (/health)", f"HTTP {status}")

    status, body = call("GET", "/ready")
    check(
        status == 200,
        "API is ready and the database answers (/ready)",
        f"HTTP {status} {body if status != 200 else ''}",
    )

    status, _ = call("GET", "/api/v1/projects")
    check(status == 401, "a request with no token is refused (401)", f"HTTP {status}")

    status, body = call("GET", "/api/v1/projects", scientist)
    ids = [p["project_id"] for p in body] if status == 200 and isinstance(body, list) else None
    check(
        ids == ["PRJ-001"],
        "a scientist sees only their own project",
        f"HTTP {status}, projects {ids}",
    )

    status, _ = call("GET", "/api/v1/projects/PRJ-001", scientist)
    check(status == 200, "a scientist can open their project", f"HTTP {status}")

    status, _ = call("GET", "/api/v1/projects/PRJ-002", scientist)
    check(status == 403, "a scientist is refused another project (403)", f"HTTP {status}")

    status, body = call("POST", "/api/v1/research/query", scientist, {"query": question})
    answered = status == 200 and isinstance(body, dict) and "answer" in body
    check(answered, "a research question is answered", f"HTTP {status}")

    status, _ = call(
        "POST",
        "/api/v1/audit/events",
        scientist,
        {"action": "REPORT_SECTION_APPROVED", "resource_type": "report"},
    )
    check(status == 405, "nobody can write a fake audit event (405)", f"HTTP {status}")

    status, _ = call("GET", "/api/v1/audit/events", scientist)
    check(status == 403, "a scientist cannot read the audit log (403)", f"HTTP {status}")

    status, page = call("GET", f"/api/v1/audit/events?actor={person}&page_size=50", admin)
    events = page["items"] if status == 200 and isinstance(page, dict) else []
    actions = sorted({e["action"] for e in events})
    expected = {"PROJECT_LISTED", "PROJECT_VIEWED", "ACCESS_DENIED", "RESEARCH_QUERY_EXECUTED"}
    mine = [e for e in events if e["actor_subject"] == person]
    check(
        status == 200 and expected <= set(actions),
        "an admin can read back what the scientist did",
        f"HTTP {status}, actions {actions}",
    )
    check(
        bool(mine) and len(mine) == len(events),
        "every event is attributed to the right person",
        f"{len(mine)} of {len(events)}",
    )

    denied = [e for e in events if e["action"] == "ACCESS_DENIED" and e["project_id"] == "PRJ-002"]
    check(
        bool(denied) and denied[0]["outcome"] == "DENIED",
        "the refused request was recorded as DENIED",
        f"{len(denied)} found",
    )

    query_events = [e for e in events if e["action"] == "RESEARCH_QUERY_EXECUTED"]
    fingerprint = hashlib.sha256(question.encode()).hexdigest()
    stored = json.dumps(events)
    check(
        bool(query_events)
        and query_events[0]["details"].get("query", {}).get("sha256") == fingerprint,
        "the question is stored only as a fingerprint",
        "fingerprint matches" if query_events else "no query event",
    )
    check(
        QUESTION_MARKER not in stored and "confidential" not in stored,
        "the question text appears nowhere in the audit log",
    )

    return all(results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="where the API listens")
    parser.add_argument("--env", type=Path, default=REPO_ROOT / ".env", help="your .env file")
    args = parser.parse_args(argv)

    env = load_env(args.env)
    if env.get("AUTH_MODE") != "dev" or not env.get("AUTH_DEV_SECRET"):
        print(
            "This test signs its own tokens, so it needs AUTH_MODE=dev and AUTH_DEV_SECRET in .env."
        )
        print("Run scripts/make_env.py to create one.")
        return 2
    issuer = env.get("OIDC_ISSUER_URL", DEFAULT_ISSUER)

    def mint(**kwargs: object) -> str:
        return make_token(env["AUTH_DEV_SECRET"], issuer, **kwargs)  # type: ignore[arg-type]

    def report(ok: bool, name: str, detail: str = "") -> None:
        print(
            f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else "")
        )

    print(f"Smoke test against {args.base_url}")
    try:
        passed = run_smoke(http_call(args.base_url), mint, report)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        print(f"\nCould not reach the API: {exc}\nIs it running? See README, Option A, step 5.")
        return 3
    print(
        "\nAll checks passed."
        if passed
        else "\nSome checks FAILED. See the lines marked FAIL above."
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
