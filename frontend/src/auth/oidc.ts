/**
 * IdP-Agnostic OIDC Authorization Code + PKCE Helper (Keycloak / Microsoft Entra ID)
 */

export type UserRole = "scientist" | "reviewer" | "admin";

export interface OidcSession {
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

export function createDevSession(role: UserRole = "scientist"): OidcSession {
  const roleProfiles: Record<UserRole, { name: string; email: string; projects: string[] }> = {
    scientist: {
      name: "Dr. Aris Thorne (Formulation Scientist)",
      email: "a.thorne@pharma.local",
      projects: ["PRJ-001", "PRJ-002"],
    },
    reviewer: {
      name: "Dr. Elena Vance (QA & Regulatory Reviewer)",
      email: "e.vance@pharma.local",
      projects: ["PRJ-001", "PRJ-002"],
    },
    admin: {
      name: "Platform & Compliance Administrator",
      email: "admin@pharma.local",
      projects: ["PRJ-001", "PRJ-002"],
    },
  };
  const profile = roleProfiles[role];
  return {
    sub: `oidc-${role}-001`,
    name: profile.name,
    email: profile.email,
    role,
    projectIds: profile.projects,
    accessToken: `dev-jwt-${role}-PRJ-001`,
    idpProvider: "keycloak",
    expiresAt: Date.now() + 3600 * 1000,
  };
}
