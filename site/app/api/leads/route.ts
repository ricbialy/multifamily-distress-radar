import { and, asc, desc, eq } from "drizzle-orm";
import { getDb, getRadarEnv } from "../../../db";
import { leadChanges, propertyLeads } from "../../../db/schema";
import { isAuthResponse, publicError, requireAuthorizedUser } from "../auth";

const CITIES = new Set(["hialeah_fl", "surfside_fl"]);
const STAGES = new Set(["new", "researching", "qualified", "contacted", "negotiating", "won", "lost", "paused"]);
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export async function GET(request: Request) {
  const user = requireAuthorizedUser(request);
  if (isAuthResponse(user)) return user;

  const url = new URL(request.url);
  const city = url.searchParams.get("city");
  const folio = url.searchParams.get("folio");
  const limit = Math.min(Math.max(Number(url.searchParams.get("limit") || 100), 1), 200);
  if (!city || !CITIES.has(city)) return publicError("INVALID_CITY", "A supported city is required.", 400);

  try {
    if (folio) {
      if (!/^\d{8,20}$/.test(folio)) return publicError("INVALID_FOLIO", "A valid folio is required.", 400);
      const rows = await getDb().select().from(propertyLeads)
        .where(and(eq(propertyLeads.citySlug, city), eq(propertyLeads.folio, folio))).limit(1);
      const history = await getDb().select().from(leadChanges)
        .where(and(eq(leadChanges.citySlug, city), eq(leadChanges.folio, folio)))
        .orderBy(desc(leadChanges.changedAt)).limit(20);
      return Response.json({ leads: rows, history });
    }
    const rows = await getDb().select().from(propertyLeads)
      .where(eq(propertyLeads.citySlug, city))
      .orderBy(asc(propertyLeads.nextFollowUpDate)).limit(limit);
    return Response.json({ leads: rows });
  } catch (error) {
    console.error("lead.read.failed", { city, folio, user: user.email, error });
    return publicError("LEAD_READ_FAILED", "Lead data could not be loaded.", 500);
  }
}

export async function PUT(request: Request) {
  const user = requireAuthorizedUser(request);
  if (isAuthResponse(user)) return user;

  try {
    const body = await request.json() as {
      city?: string; folio?: string; stage?: string; assignee?: string; nextFollowUpDate?: string;
      disposition?: string; notes?: string; version?: number;
    };
    if (!body.city || !CITIES.has(body.city)) return publicError("INVALID_CITY", "A supported city is required.", 400);
    if (!body.folio || !/^\d{8,20}$/.test(body.folio)) return publicError("INVALID_FOLIO", "A valid folio is required.", 400);
    if (!body.stage || !STAGES.has(body.stage)) return publicError("INVALID_STAGE", "A valid lead stage is required.", 400);
    if ((body.notes?.length || 0) > 5000 || (body.assignee?.length || 0) > 120 || (body.disposition?.length || 0) > 240)
      return publicError("FIELD_TOO_LONG", "One or more workflow fields exceed allowed bounds.", 400);
    if (body.nextFollowUpDate && !DATE_RE.test(body.nextFollowUpDate))
      return publicError("INVALID_FOLLOW_UP_DATE", "Follow-up date must use YYYY-MM-DD.", 400);

    const [before] = await getDb().select().from(propertyLeads)
      .where(and(eq(propertyLeads.citySlug, body.city), eq(propertyLeads.folio, body.folio))).limit(1);
    if (before && body.version !== undefined && body.version !== before.version)
      return publicError("VERSION_CONFLICT", "This lead was changed by another user. Reload before saving.", 409);

    const now = new Date().toISOString();
    const nextVersion = (before?.version || 0) + 1;
    const values = {
      citySlug: body.city,
      folio: body.folio,
      stage: body.stage,
      assignee: body.assignee?.trim() || null,
      nextFollowUpDate: body.nextFollowUpDate || null,
      disposition: body.disposition?.trim() || null,
      notes: body.notes?.trim() || null,
      version: nextVersion,
      updatedBy: user.email,
      updatedAt: now,
    };
    const change = {
      citySlug: body.city,
      folio: body.folio,
      beforeJson: before ? JSON.stringify(before) : null,
      afterJson: JSON.stringify(values),
      changedBy: user.email,
      changedAt: now,
    };

    const binding = getRadarEnv().DB;
    if (!binding) throw new Error("D1 unavailable");
    await binding.batch([
      binding.prepare(`INSERT INTO property_leads
        (city_slug,folio,stage,assignee,next_follow_up_date,disposition,notes,version,updated_by,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(city_slug,folio) DO UPDATE SET
        stage=excluded.stage,assignee=excluded.assignee,next_follow_up_date=excluded.next_follow_up_date,
        disposition=excluded.disposition,notes=excluded.notes,version=excluded.version,
        updated_by=excluded.updated_by,updated_at=excluded.updated_at`)
        .bind(values.citySlug, values.folio, values.stage, values.assignee, values.nextFollowUpDate,
          values.disposition, values.notes, values.version, values.updatedBy, values.updatedAt),
      binding.prepare(`INSERT INTO lead_changes
        (city_slug,folio,before_json,after_json,changed_by,changed_at) VALUES (?,?,?,?,?,?)`)
        .bind(change.citySlug, change.folio, change.beforeJson, change.afterJson, change.changedBy, change.changedAt),
    ]);
    return Response.json({ status: "saved", lead: values, change });
  } catch (error) {
    console.error("lead.save.failed", { user: user.email, error });
    return publicError("LEAD_SAVE_FAILED", "The lead could not be saved.", 500);
  }
}
