import { eq } from "drizzle-orm";
import { getDb, getRadarEnv } from "../../../db";
import { radarSnapshots } from "../../../db/schema";

const CITIES = new Set(["hialeah_fl", "surfside_fl"]);

export async function GET(request: Request) {
  try {
    const city = new URL(request.url).searchParams.get("city") || "hialeah_fl";
    if (!CITIES.has(city))
      return Response.json({ error: "unsupported city" }, { status: 400 });
    const [row] = await getDb()
      .select()
      .from(radarSnapshots)
      .where(eq(radarSnapshots.citySlug, city))
      .limit(1);
    if (!row) return Response.json({ status: "empty" }, { status: 404 });
    return Response.json({
      status: "ready",
      city: row.citySlug,
      generatedAt: row.generatedAt,
      receivedAt: row.receivedAt,
      properties: JSON.parse(row.payloadJson),
    });
  } catch (error) {
    return Response.json(
      {
        error: error instanceof Error ? error.message : "snapshot unavailable",
      },
      { status: 500 },
    );
  }
}

export async function PUT(request: Request) {
  const expected = getRadarEnv().INGEST_TOKEN;
  const supplied = request.headers
    .get("authorization")
    ?.replace(/^Bearer\s+/i, "");
  if (!expected)
    return Response.json(
      { error: "ingestion is not configured" },
      { status: 503 },
    );
  if (!supplied || supplied !== expected)
    return Response.json({ error: "unauthorized" }, { status: 401 });
  try {
    const body = (await request.json()) as {
      city?: string;
      generatedAt?: string;
      properties?: unknown[];
      source?: string;
    };
    if (
      !body.city ||
      !CITIES.has(body.city) ||
      !body.generatedAt ||
      !Array.isArray(body.properties)
    ) {
      return Response.json(
        { error: "city, generatedAt, and properties are required" },
        { status: 400 },
      );
    }
    if (body.properties.length > 500)
      return Response.json(
        { error: "snapshot exceeds 500 properties" },
        { status: 413 },
      );
    const payloadJson = JSON.stringify(body.properties);
    if (payloadJson.length > 2_500_000)
      return Response.json(
        { error: "snapshot exceeds 2.5 MB" },
        { status: 413 },
      );
    await getDb()
      .insert(radarSnapshots)
      .values({
        citySlug: body.city,
        payloadJson,
        generatedAt: body.generatedAt,
        source: body.source || "scheduled-refresh",
      })
      .onConflictDoUpdate({
        target: radarSnapshots.citySlug,
        set: {
          payloadJson,
          generatedAt: body.generatedAt,
          receivedAt: new Date().toISOString(),
          source: body.source || "scheduled-refresh",
        },
      });
    return Response.json({
      status: "accepted",
      city: body.city,
      propertyCount: body.properties.length,
    });
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "invalid snapshot" },
      { status: 400 },
    );
  }
}
