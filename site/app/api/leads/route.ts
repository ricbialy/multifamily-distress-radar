import { asc, desc, eq } from "drizzle-orm";
import { getDb } from "../../../db";
import { leadChanges, propertyLeads } from "../../../db/schema";

const STAGES = new Set(["new", "researching", "qualified", "contacted", "negotiating", "won", "lost", "paused"]);

function authenticatedEmail(request: Request) {
  return request.headers.get("oai-authenticated-user-email");
}

export async function GET(request: Request) {
  if (!authenticatedEmail(request)) return Response.json({ error: "unauthorized" }, { status: 401 });
  const folio = new URL(request.url).searchParams.get("folio");
  const query = getDb().select().from(propertyLeads);
  if (folio) {
    const rows = await query.where(eq(propertyLeads.folio, folio)).limit(1);
    const history = await getDb().select().from(leadChanges).where(eq(leadChanges.folio, folio)).orderBy(desc(leadChanges.changedAt)).limit(20);
    return Response.json({ leads: rows, history });
  }
  const rows = await query.orderBy(asc(propertyLeads.nextFollowUpDate));
  return Response.json({ leads: rows });
}

export async function PUT(request: Request) {
  const email = authenticatedEmail(request);
  if (!email) return Response.json({ error: "unauthorized" }, { status: 401 });
  try {
    const body = await request.json() as {
      folio?: string; stage?: string; assignee?: string; nextFollowUpDate?: string;
      disposition?: string; notes?: string;
    };
    if (!body.folio || !body.stage || !STAGES.has(body.stage)) {
      return Response.json({ error: "valid folio and stage are required" }, { status: 400 });
    }
    if (!/^\d{8,20}$/.test(body.folio) || (body.notes?.length || 0) > 5000 || (body.assignee?.length || 0) > 120 || (body.disposition?.length || 0) > 240) {
      return Response.json({ error: "workflow fields exceed allowed bounds" }, { status: 400 });
    }
    const values = {
      folio: body.folio, stage: body.stage,
      assignee: body.assignee?.trim() || null,
      nextFollowUpDate: body.nextFollowUpDate || null,
      disposition: body.disposition?.trim() || null,
      notes: body.notes?.trim() || null,
      updatedBy: email, updatedAt: new Date().toISOString(),
    };
    const [before] = await getDb().select().from(propertyLeads).where(eq(propertyLeads.folio, body.folio)).limit(1);
    await getDb().insert(propertyLeads).values(values).onConflictDoUpdate({
      target: propertyLeads.folio,
      set: values,
    });
    const change = { folio: body.folio, beforeJson: before ? JSON.stringify(before) : null, afterJson: JSON.stringify(values), changedBy: email, changedAt: values.updatedAt };
    await getDb().insert(leadChanges).values(change);
    return Response.json({ status: "saved", lead: values, change });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "invalid lead" }, { status: 400 });
  }
}
