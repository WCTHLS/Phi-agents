/**
 * IdP-Agnostic OIDC Authorization Code + PKCE Helper (Keycloak / Microsoft Entra ID)
 */

export type UserRole = "scientist" | "reviewer" | "admin";

export interface OidcSession {
  userKey: string;
  sub: string;
  name: string;
  email: string;
  role: UserRole;
  projectIds: string[];
  accessToken: string;
  idpProvider: "keycloak" | "entra_id";
  expiresAt: number;
}

export interface OidcConfig {
  issuerUrl: string;
  clientId: string;
  redirectUri: string;
}

export const defaultOidcConfig: OidcConfig = {
  issuerUrl: import.meta.env.VITE_OIDC_ISSUER_URL ?? "http://localhost:8080/realms/pharma",
  clientId: import.meta.env.VITE_OIDC_CLIENT_ID ?? "pharma-ui",
  redirectUri: typeof window !== "undefined" ? window.location.origin : "http://localhost:5173",
};

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) {
    binary += String.fromCharCode(b);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function generatePkcePair(): Promise<{ codeVerifier: string; codeChallenge: string }> {
  const randomBytes = new Uint8Array(32);
  crypto.getRandomValues(randomBytes);
  const codeVerifier = base64UrlEncode(randomBytes);
  const encoded = new TextEncoder().encode(codeVerifier);
  const digest = await crypto.subtle.digest("SHA-256", encoded);
  const codeChallenge = base64UrlEncode(new Uint8Array(digest));
  return { codeVerifier, codeChallenge };
}

export async function buildOidcAuthorizeUrl(config: OidcConfig = defaultOidcConfig): Promise<string> {
  const { codeVerifier, codeChallenge } = await generatePkcePair();
  sessionStorage.setItem("oidc_code_verifier", codeVerifier);
  const params = new URLSearchParams({
    client_id: config.clientId,
    response_type: "code",
    scope: "openid profile email",
    redirect_uri: config.redirectUri,
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
  });
  return `${config.issuerUrl}/protocol/openid-connect/auth?${params.toString()}`;
}

/**
 * The development users. The API holds the same table (src/pharma_ai/infrastructure/security/
 * dev_users.py) and decides each person's role and projects from it; what is listed here is only
 * what the sign-in screen shows. A test checks that the two lists agree. Each person belongs to
 * some projects and not others on purpose, so that a refusal can be seen.
 */
export interface DevUser {
  key: string;
  name: string;
  email: string;
  role: UserRole;
  projects: string[];
  blurb: string;
}

export const DEV_USERS: DevUser[] = [
  {
    key: "thorne",
    name: "Dr. Aris Thorne",
    email: "a.thorne@pharma.local",
    role: "scientist",
    projects: ["PRJ-001"],
    blurb: "Scientist, PRJ-001 only",
  },
  {
    key: "nair",
    name: "Dr. Priya Nair",
    email: "p.nair@pharma.local",
    role: "scientist",
    projects: ["PRJ-002"],
    blurb: "Scientist, PRJ-002 only",
  },
  {
    key: "webb",
    name: "Dr. Marcus Webb",
    email: "m.webb@pharma.local",
    role: "scientist",
    projects: ["PRJ-001", "PRJ-002"],
    blurb: "Scientist, both projects",
  },
  {
    key: "lind",
    name: "Dr. Nora Lind",
    email: "n.lind@pharma.local",
    role: "scientist",
    projects: [],
    blurb: "Scientist, no project (refused everywhere)",
  },
  {
    key: "vance",
    name: "Dr. Elena Vance",
    email: "e.vance@pharma.local",
    role: "reviewer",
    projects: ["PRJ-001"],
    blurb: "Reviewer, PRJ-001 only",
  },
  {
    key: "menon",
    name: "Dr. Ravi Menon",
    email: "r.menon@pharma.local",
    role: "reviewer",
    projects: ["PRJ-002"],
    blurb: "Reviewer, PRJ-002 only",
  },
  {
    key: "admin",
    name: "Platform Administrator",
    email: "admin@pharma.local",
    role: "admin",
    projects: ["PRJ-001", "PRJ-002"],
    blurb: "Admin: uploads PDFs, reads the audit log",
  },
];

/**
 * Every project any development user belongs to. The project list shows the ones the signed-in
 * person is not in as "no access", so that choosing one shows the API refusing it. Names are not
 * listed here: the API only tells a person the names of their own projects.
 */
export const DEV_PROJECT_IDS: string[] = [...new Set(DEV_USERS.flatMap((u) => u.projects))].sort();

export function createDevSession(userKey: string = DEV_USERS[0].key): OidcSession {
  const user = DEV_USERS.find((u) => u.key === userKey) ?? DEV_USERS[0];
  return {
    userKey: user.key,
    sub: `dev-${user.key}`,
    name: user.name,
    email: user.email,
    role: user.role,
    projectIds: user.projects,
    accessToken: `dev-jwt-${user.key}`,
    idpProvider: "keycloak",
    expiresAt: Date.now() + 3600 * 1000,
  };
}


export async function requestDevToken(userKey: string): Promise<string | null> {
  try {
    const apiBase = import.meta.env.VITE_API_BASE_URL ?? "";
    // Only the user's name is sent. The API decides the role and projects from its own table.
    const res = await fetch(`${apiBase}/api/v1/auth/dev-token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: userKey }),
    });
    if (res.ok) {
      const data = await res.json();
      return (data.access_token as string) || null;
    }
  } catch {
    // Offline or dev token endpoint unavailable
  }
  return null;
}
