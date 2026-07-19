PRAGMA foreign_keys=OFF;

ALTER TABLE property_leads RENAME TO property_leads_legacy;
ALTER TABLE lead_changes RENAME TO lead_changes_legacy;

CREATE TABLE property_leads (
  city_slug text NOT NULL,
  folio text NOT NULL,
  stage text NOT NULL DEFAULT 'new',
  assignee text,
  next_follow_up_date text,
  disposition text,
  notes text,
  version integer NOT NULL DEFAULT 1,
  updated_by text NOT NULL,
  updated_at text NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(city_slug, folio)
);

CREATE TABLE lead_changes (
  id integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  city_slug text NOT NULL,
  folio text NOT NULL,
  before_json text,
  after_json text NOT NULL,
  changed_by text NOT NULL,
  changed_at text NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO property_leads
  (city_slug,folio,stage,assignee,next_follow_up_date,disposition,notes,version,updated_by,updated_at)
SELECT
  'hialeah_fl',folio,stage,assignee,next_follow_up_date,disposition,notes,1,updated_by,updated_at
FROM property_leads_legacy;

INSERT INTO lead_changes
  (id,city_slug,folio,before_json,after_json,changed_by,changed_at)
SELECT
  id,'hialeah_fl',folio,before_json,after_json,changed_by,changed_at
FROM lead_changes_legacy;

DROP TABLE property_leads_legacy;
DROP TABLE lead_changes_legacy;

CREATE INDEX idx_property_leads_city_followup
  ON property_leads(city_slug,next_follow_up_date);
CREATE INDEX idx_property_leads_city_stage
  ON property_leads(city_slug,stage);
CREATE INDEX idx_property_leads_assignee
  ON property_leads(assignee);
CREATE INDEX idx_lead_changes_city_folio_changed
  ON lead_changes(city_slug,folio,changed_at DESC);

PRAGMA foreign_keys=ON;
