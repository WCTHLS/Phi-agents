"""Named development users, so that project access and roles can be tested (decision log D23).

Plain language: until real sign-in (Keycloak) is connected, the API signs a development token for
whoever asks. These are the people it can sign one for. The *server* decides each person's role and
projects from this table, so a browser that asks for ``thorne`` cannot also ask for more projects.
Each person belongs to some projects and not others on purpose: that is what makes it possible to
see a refusal. The names are made up. Nothing here is used unless AUTH_MODE=dev and ENVIRONMENT is
local or test (the dev-token endpoint refuses otherwise).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DevUser:
    key: str  # what the sign-in screen sends
    sub: str  # the stable id that appears in audit events and stored runs
    name: str
    email: str
    role: str
    projects: tuple[str, ...]


def _user(key: str, name: str, role: str, *projects: str) -> DevUser:
    initial, _, surname = name.removeprefix("Dr. ").partition(" ")
    email = f"{initial[0].lower()}.{surname.lower()}@pharma.local"
    return DevUser(key, f"dev-{key}", name, email, role, tuple(projects))


DEV_USERS: dict[str, DevUser] = {
    user.key: user
    for user in (
        _user("thorne", "Dr. Aris Thorne", "scientist", "PRJ-001"),
        _user("nair", "Dr. Priya Nair", "scientist", "PRJ-002"),
        _user("webb", "Dr. Marcus Webb", "scientist", "PRJ-001", "PRJ-002"),
        _user("lind", "Dr. Nora Lind", "scientist"),  # in no project: refused everywhere
        _user("vance", "Dr. Elena Vance", "reviewer", "PRJ-001"),
        _user("menon", "Dr. Ravi Menon", "reviewer", "PRJ-002"),
        DevUser(
            "admin",
            "dev-admin",
            "Platform Administrator",
            "admin@pharma.local",
            "admin",
            ("PRJ-001", "PRJ-002"),
        ),
    )
}
