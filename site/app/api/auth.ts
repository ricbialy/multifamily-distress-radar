import { getRadarEnv } from "../../db";

export type AuthorizedUser = {
  email: string;
  role: "admin";
};

function csvSet(value?: string): Set<string> {
  return new Set(
    (value || "")
      .split(",")
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean),
  );
}

export function requireAuthorizedUser(request: Request): AuthorizedUser | Response {
  const email = request.headers.get("oai-authenticated-user-email")?.trim().toLowerCase();
  if (!email) return Response.json({ error: { code: "UNAUTHORIZED", message: "Authentication required." } }, { status: 401 });

  const env = getRadarEnv();
  const allowedEmails = csvSet(env.ALLOWED_EMAILS);
  const allowedDomains = csvSet(env.ALLOWED_EMAIL_DOMAINS);
  const domain = email.split("@")[1] || "";

  if (!allowedEmails.size && !allowedDomains.size) {
    return Response.json(
      { error: { code: "AUTH_NOT_CONFIGURED", message: "Application access is not configured." } },
      { status: 503 },
    );
  }
  if (!allowedEmails.has(email) && !allowedDomains.has(domain)) {
    return Response.json({ error: { code: "FORBIDDEN", message: "You do not have access to this application." } }, { status: 403 });
  }
  return { email, role: "admin" };
}

export function isAuthResponse(value: AuthorizedUser | Response): value is Response {
  return value instanceof Response;
}

export function publicError(code: string, message: string, status: number): Response {
  return Response.json({ error: { code, message } }, { status });
}
