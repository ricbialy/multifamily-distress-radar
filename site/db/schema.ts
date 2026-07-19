import { sql } from "drizzle-orm";
import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const radarSnapshots = sqliteTable("radar_snapshots", {
  citySlug: text("city_slug").primaryKey(),
  payloadJson: text("payload_json").notNull(),
  generatedAt: text("generated_at").notNull(),
  receivedAt: text("received_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  source: text("source").notNull().default("scheduled-refresh"),
});

export const propertyLeads = sqliteTable("property_leads", {
  folio: text("folio").primaryKey(),
  stage: text("stage").notNull().default("new"),
  assignee: text("assignee"),
  nextFollowUpDate: text("next_follow_up_date"),
  disposition: text("disposition"),
  notes: text("notes"),
  updatedBy: text("updated_by").notNull(),
  updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const leadChanges = sqliteTable("lead_changes", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  folio: text("folio").notNull(),
  beforeJson: text("before_json"),
  afterJson: text("after_json").notNull(),
  changedBy: text("changed_by").notNull(),
  changedAt: text("changed_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});
