import { eq } from "drizzle-orm";
import { getDb, getRadarEnv } from "../../../db";
import { radarSnapshots } from "../../../db/schema";
import { isAuthResponse, publicError, requireAuthorizedUser } from "../auth";

const CITIES = new Set(["hialeah_fl", "surfside_fl"]);
const MAX_PROPERTIES = 500;
const MAX_PAYLOAD_CHARS = 2_500_000;

function isValidProperty(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return typeof row.folio === "string" && /^\d{8,20}$/.test(row.folio) &&
    (row.address === undefined || row.address === null || typeof row.address === "string");
}

export async function GET(request: Request) {
  const user = requireAuthorizedUser(request);
  if (isAuthResponse(user)) return user;
  const city = new URL(request.url).searchParams.get("city") || "hialeah_fl";
  if (!CITIES.has(city)) return publicError("INVALID_CITY", "A supported city is required.", 400);

  try {
    const [row] = await getDb().select().from(radarSnapshots)
      .where(eq(radarSnapshots.citySlug, city)).limit(1);
    if (!row) return publicError("SNAPSHOT_NOT_FOUND", "No snapshot is available for this city.", 404);
    return Response.json({
      status: "ready",
      city: row.citySlug,
      generatedAt: row.generatedAt,
      receivedAt: row.receivedAt,
      properties: JSON.parse(row.payloadJson),
    });
  } catch (error) {
    console.error("snapshot.read.failed", { city, user: user.email, error });
    return publicError("SNAPSHOT_READ_FAILED", "The snapshot could not be loaded.", 500);
  }
}

export async function PUT(request: Request) {
  const expected = getRadarEnv().INGEST_TOKEN;
  const supplied = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  if (!expected) return publicError("INGEST_NOT_CONFIGURED", "Snapshot ingestion is not configured.", 503);
  if (!supplied || supplied !== expected) return publicError("UNAUTHORIZED", "Valid ingestion credentials are required.", 401);

  try {
    const body = (await request.json()) as {
      city?: string;
      generatedAt?: string;
      properties?: unknown[];
      source?: string;
      schemaVersion?: string;
    };
    if (!body.city || !CITIES.has(body.city)) return publicError("INVALID_CITY", "A supported city is required.", 400);
    if (body.schemaVersion !== "1.0") return publicError("UNSUPPORTED_SCHEMA", "Snapshot schemaVersion 1.0 is required.", 400);
    if (!body.generatedAt || Number.isNaN(Date.parse(body.generatedAt)))
      return publicError("INVALID_GENERATED_AT", "generatedAt must be a valid timestamp.", 400);
    if (!Array.isArray(body.properties) || !body.properties.every(isValidProperty))
      return publicError("INVALID_PROPERTIES", "Snapshot properties failed validation.", 400);
    if (body.properties.length > MAX_PROPERTIES)
      return publicError("SNAPSHOT_TOO_LARGE", `Snapshot exceeds ${MAX_PROPERTIES} properties.`, 413);

    const payloadJson = JSON.stringify(body.properties);
    if (payloadJson.length > MAX_PAYLOAD_CHARS)
      return publicError("SNAPSHOT_TOO_LARGE", "Snapshot exceeds the payload limit.", 413);

    const [current] = await getDb().select({ generatedAt: radarSnapshots.generatedAt })
      .from(radarSnapshots).where(eq(radarSnapshots.citySlug, body.city)).limit(1);
    if (current && Date.parse(body.generatedAt) <= Date.parse(current.generatedAt))
      return publicError("STALE_SNAPSHOT", "The submitted snapshot is not newer than the current snapshot.", 409);

    await getDb().insert(radarSnapshots).values({
      citySlug: body.city,
      payloadJson,
      generatedAt: body.generatedAt,
      source: body.source?.slice(0, 120) || "scheduled-refresh",
    }).onConflictDoUpdate({
      target: radarSnapshots.citySlug,
      set: {
        payloadJson,
        generatedAt: body.generatedAt,
        receivedAt: new Date().toISOString(),
        source: body.source?.slice(0, 120) || "scheduled-refresh",
      },
    });
    return Response.json({ status: "accepted", city: body.city, propertyCount: body.properties.length });
  } catch (error) {
    console.error("snapshot.ingest.failed", { error });
    return publicError("SNAPSHOT_INGEST_FAILED", "The snapshot could not be ingested.", 500);
  }
}
