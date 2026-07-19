import { sql } from "drizzle-orm";
import { index, integer, primaryKey, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const radarSnapshots = sqliteTable("radar_snapshots", {
  citySlug: text("city_slug").primaryKey(),
  payloadJson: text("payload_json").notNull(),
  generatedAt: text("generated_at").notNull(),
  receivedAt: text("received_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  source: text("source").notNull().default("scheduled-refresh"),
});

export const propertyLeads = sqliteTable(
  "property_leads",
  {
    citySlug: text("city_slug").notNull(),
    folio: text("folio").notNull(),
    stage: text("stage").notNull().default("new"),
    assignee: text("assignee"),
    nextFollowUpDate: text("next_follow_up_date"),
    disposition: text("disposition"),
    notes: text("notes"),
    version: integer("version").notNull().default(1),
    updatedBy: text("updated_by").notNull(),
    updatedAt: text("updated_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    primaryKey({ columns: [table.citySlug, table.folio] }),
    index("idx_property_leads_city_followup").on(table.citySlug, table.nextFollowUpDate),
    index("idx_property_leads_city_stage").on(table.citySlug, table.stage),
    index("idx_property_leads_assignee").on(table.assignee),
  ],
);

export const leadChanges = sqliteTable(
  "lead_changes",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    citySlug: text("city_slug").notNull(),
    folio: text("folio").notNull(),
    beforeJson: text("before_json"),
    afterJson: text("after_json").notNull(),
    changedBy: text("changed_by").notNull(),
    changedAt: text("changed_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [index("idx_lead_changes_city_folio_changed").on(table.citySlug, table.folio, table.changedAt)],
);
