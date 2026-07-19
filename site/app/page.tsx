"use client";

import { useEffect, useMemo, useState } from "react";
import seedData from "../data/hialeah.example.json";

type Lead = {
  stage: string;
  assignee: string | null;
  nextFollowUpDate: string | null;
  disposition: string | null;
  notes: string | null;
  updatedAt?: string;
  updatedBy?: string;
};
type LeadChange = {
  id?: number;
  afterJson: string;
  changedBy: string;
  changedAt: string;
};
type Contact = {
  source_record_id?: string;
  contact_name: string;
  role?: string | null;
  email?: string | null;
  phone?: string | null;
  mailing_address?: string | null;
  verification_status?: string;
  confidence?: number;
  source_url?: string;
};
type Property = (typeof seedData)[number] & {
  lead?: Lead;
  contacts?: Contact[];
};
const num = (value: string | number | null | undefined) => Number(value || 0);
const date = (value: string | null | undefined) =>
  value ? String(value).slice(0, 10) : "—";
const CITY_OPTIONS = [
  { slug: "hialeah_fl", label: "Hialeah, FL" },
  { slug: "surfside_fl", label: "Surfside, FL" },
];

function Filters({
  query,
  setQuery,
  minimum,
  setMinimum,
  queried,
  setQueried,
  stage,
  setStage,
  dueOnly,
  setDueOnly,
  contactsOnly,
  setContactsOnly,
}: {
  query: string;
  setQuery: (v: string) => void;
  minimum: number;
  setMinimum: (v: number) => void;
  queried: boolean;
  setQueried: (v: boolean) => void;
  stage: string;
  setStage: (v: string) => void;
  dueOnly: boolean;
  setDueOnly: (v: boolean) => void;
  contactsOnly: boolean;
  setContactsOnly: (v: boolean) => void;
}) {
  return (
    <aside className="filters">
      <div className="filterHeading">
        <b>FILTERS</b>
        <button
          onClick={() => {
            setQuery("");
            setMinimum(0);
            setQueried(false);
            setStage("all");
            setDueOnly(false);
            setContactsOnly(false);
          }}
        >
          Reset all
        </button>
      </div>
      <label>
        Search address or owner
        <input
          aria-label="Search address or owner"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search…"
        />
      </label>
      <label>
        Units
        <div className="range">
          <input value="10" readOnly />
          <span>to</span>
          <input value="80" readOnly />
        </div>
      </label>
      <label>
        Minimum priority score
        <input
          type="number"
          value={minimum}
          onChange={(e) => setMinimum(num(e.target.value))}
        />
      </label>
      <label>
        Lead stage
        <select value={stage} onChange={(e) => setStage(e.target.value)}>
          <option value="all">All stages</option>
          <option>new</option>
          <option>researching</option>
          <option>qualified</option>
          <option>contacted</option>
          <option>negotiating</option>
          <option>won</option>
          <option>lost</option>
          <option>paused</option>
        </select>
      </label>
      <div className="checks">
        <b>Workflow filters</b>
        <label>
          <input
            type="checkbox"
            checked={dueOnly}
            onChange={(e) => setDueOnly(e.target.checked)}
          />{" "}
          Due or overdue follow-up
        </label>
        <label>
          <input
            type="checkbox"
            checked={contactsOnly}
            onChange={(e) => setContactsOnly(e.target.checked)}
          />{" "}
          Contacts available
        </label>
        <label>
          <input
            type="checkbox"
            checked={queried}
            onChange={(e) => setQueried(e.target.checked)}
          />{" "}
          Clerk queried
        </label>
      </div>
      <div className="about">
        <b>ABOUT SCORES</b>
        <p>
          Scores reflect verified public-record indicators. Higher scores
          identify greater evidence or opportunity—not a prediction of financial
          distress.
        </p>
      </div>
    </aside>
  );
}

function Inspector({
  property,
  onLeadSaved,
}: {
  property?: Property;
  onLeadSaved: (folio: string, lead: Lead) => void;
}) {
  const emptyLead = {
    stage: "new",
    assignee: "",
    nextFollowUpDate: "",
    disposition: "",
    notes: "",
  };
  const [lead, setLead] = useState(emptyLead);
  const [saveState, setSaveState] = useState("");
  const [history, setHistory] = useState<LeadChange[]>([]);
  useEffect(() => {
    setLead({
      stage: property?.lead?.stage || "new",
      assignee: property?.lead?.assignee || "",
      nextFollowUpDate: property?.lead?.nextFollowUpDate || "",
      disposition: property?.lead?.disposition || "",
      notes: property?.lead?.notes || "",
    });
    setSaveState("");
    if (property?.folio)
      fetch(`/api/leads?folio=${encodeURIComponent(property.folio)}`)
        .then(async (response) => {
          if (!response.ok) return;
          const result = (await response.json()) as { history?: LeadChange[] };
          setHistory(result.history || []);
        })
        .catch(() => setHistory([]));
    else setHistory([]);
  }, [property?.folio, property?.lead]);
  if (!property) return null;
  const tax = property as Property & {
    delinquent_tax_year_count?: string | number;
    delinquent_tax_amount?: string | number;
    oldest_delinquent_tax_year?: string | number | null;
  };
  return (
    <aside className="inspector">
      <h2>{property.address}</h2>
      <div className="scoreRow">
        <div>
          <strong>{property.code_enforcement_score}</strong>
          <span>Code</span>
        </div>
        <div>
          <strong>{property.financial_distress_score}</strong>
          <span>Financial</span>
        </div>
        <div>
          <strong>{property.opportunity_score}</strong>
          <span>Opportunity</span>
        </div>
      </div>
      <dl>
        <dt>Folio</dt>
        <dd>{property.folio}</dd>
        <dt>Owner</dt>
        <dd>{property.owner_name}</dd>
        <dt>Units / Year built</dt>
        <dd>
          {property.unit_count} / {property.year_built}
        </dd>
        <dt>Mailing</dt>
        <dd>
          {property.mailing_address_1}, {property.mailing_city}{" "}
          {property.mailing_state}
        </dd>
      </dl>
      <section>
        <h3>
          VERIFIED OPEN CASES <i>{property.cases.length}</i>
        </h3>
        {property.cases.slice(0, 5).map((c) => (
          <div className="evidence" key={c.source_record_id}>
            <span>{c.case_number}</span>
            <span>{date(c.opened_date)}</span>
            <a href={c.source_url} target="_blank">
              {c.status || "Source"} ↗
            </a>
          </div>
        ))}
      </section>
      <section>
        <h3>
          OFFICIAL-RECORD INSTRUMENTS <i>{property.instruments.length}</i>
        </h3>
        {property.instruments.slice(0, 6).map((record) => (
          <div className="evidence" key={record.instrument_id}>
            <b>{record.document_type}</b>
            <span>{date(record.recorded_date)}</span>
            <span>{record.instrument_id}</span>
          </div>
        ))}
        {!property.instruments.length && (
          <p className="empty">No Clerk instruments returned for this folio.</p>
        )}
      </section>
      <section>
        <h3>DELINQUENT PROPERTY TAX</h3>
        {num(tax.delinquent_tax_year_count) ? (
          <div className="taxEvidence">
            <strong>
              $
              {num(tax.delinquent_tax_amount).toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </strong>
            <span>
              {tax.delinquent_tax_year_count} unpaid tax year(s) · oldest{" "}
              {tax.oldest_delinquent_tax_year}
            </span>
          </div>
        ) : (
          <p className="empty">
            No authorized delinquent-tax record has been imported for this
            property.
          </p>
        )}
      </section>
      <section className="contacts">
        <h3>
          VERIFIED CONTACT EVIDENCE <i>{property.contacts?.length || 0}</i>
        </h3>
        {property.contacts?.slice(0, 5).map((contact, index) => (
          <div
            className="contactCard"
            key={contact.source_record_id || `${contact.contact_name}-${index}`}
          >
            <div>
              <b>{contact.contact_name}</b>
              <span>
                {contact.role || "Contact"} ·{" "}
                {contact.verification_status || "unverified"}
                {contact.confidence !== undefined
                  ? ` · ${Math.round(contact.confidence * 100)}% confidence`
                  : ""}
              </span>
            </div>
            <div className="contactLinks">
              {contact.email && (
                <a href={`mailto:${contact.email}`}>{contact.email}</a>
              )}
              {contact.phone && (
                <a href={`tel:${contact.phone}`}>{contact.phone}</a>
              )}
              {contact.source_url && (
                <a href={contact.source_url} target="_blank">
                  Source ↗
                </a>
              )}
            </div>
            {contact.mailing_address && (
              <small>{contact.mailing_address}</small>
            )}
          </div>
        ))}
        {!property.contacts?.length && (
          <p className="empty">
            No authorized contact record has been imported for this property.
          </p>
        )}
      </section>
      <section className="crm">
        <div className="crmHeading">
          <h3>ACQUISITION WORKFLOW</h3>
          <span className={`stage stage-${lead.stage}`}>{lead.stage}</span>
        </div>
        <div className="crmGrid">
          <label>
            Stage
            <select
              value={lead.stage}
              onChange={(e) => setLead({ ...lead, stage: e.target.value })}
            >
              <option>new</option>
              <option>researching</option>
              <option>qualified</option>
              <option>contacted</option>
              <option>negotiating</option>
              <option>won</option>
              <option>lost</option>
              <option>paused</option>
            </select>
          </label>
          <label>
            Assignee
            <input
              value={lead.assignee}
              onChange={(e) => setLead({ ...lead, assignee: e.target.value })}
              placeholder="Name"
            />
          </label>
          <label>
            Follow-up
            <input
              type="date"
              value={lead.nextFollowUpDate}
              onChange={(e) =>
                setLead({ ...lead, nextFollowUpDate: e.target.value })
              }
            />
          </label>
          <label>
            Disposition
            <input
              value={lead.disposition}
              onChange={(e) =>
                setLead({ ...lead, disposition: e.target.value })
              }
              placeholder="Optional"
            />
          </label>
        </div>
        <label>
          Notes
          <textarea
            value={lead.notes}
            onChange={(e) => setLead({ ...lead, notes: e.target.value })}
            placeholder="Research, outreach, and next action"
          />
        </label>
        <div className="crmActions">
          <span>{saveState}</span>
          <button
            onClick={async () => {
              setSaveState("Saving…");
              const response = await fetch("/api/leads", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ folio: property.folio, ...lead }),
              });
              if (!response.ok) {
                setSaveState("Save failed");
                return;
              }
              const result = (await response.json()) as {
                lead: Lead;
                change: LeadChange;
              };
              onLeadSaved(property.folio, result.lead);
              setHistory((current) => [result.change, ...current]);
              setSaveState("Saved");
            }}
          >
            Save workflow
          </button>
        </div>
        {history.length > 0 && (
          <div className="history">
            <b>RECENT ACTIVITY</b>
            {history.slice(0, 5).map((change, index) => {
              const saved = JSON.parse(change.afterJson) as Lead;
              return (
                <div key={change.id || `${change.changedAt}-${index}`}>
                  <span>
                    {date(change.changedAt)} · {saved.stage}
                  </span>
                  <small>{change.changedBy}</small>
                </div>
              );
            })}
          </div>
        )}
      </section>
      <section className="filing">
        <b>
          {num(property.financial_distress_score)
            ? "Verified financial filing"
            : "✓ No verified active financial filing"}
        </b>
        <p>
          {num(property.financial_distress_score)
            ? "Review the recorded instrument evidence."
            : "No unmatched explicit lien or lis-pendens code located in collected records."}
        </p>
      </section>
      <section>
        <h3>SOURCES</h3>
        <a href={property.property_source_url} target="_blank">
          Miami-Dade Property Appraiser ↗
        </a>
      </section>
    </aside>
  );
}

export default function Home() {
  const [data, setData] = useState<Property[]>(seedData);
  const [generatedAt, setGeneratedAt] = useState("2026-07-17");
  const [city, setCity] = useState("hialeah_fl");
  const [snapshotState, setSnapshotState] = useState<
    "loading" | "ready" | "empty"
  >("loading");
  const [query, setQuery] = useState("");
  const [minimum, setMinimum] = useState(0);
  const [queried, setQueried] = useState(false);
  const [selected, setSelected] = useState(data[0]?.folio);
  const [stage, setStage] = useState("all");
  const [dueOnly, setDueOnly] = useState(false);
  const [contactsOnly, setContactsOnly] = useState(false);
  useEffect(() => {
    setSnapshotState("loading");
    setData(city === "hialeah_fl" ? seedData : []);
    setSelected(undefined);
    fetch(`/api/snapshot?city=${encodeURIComponent(city)}`)
      .then(async (response) => {
        if (!response.ok) {
          setData([]);
          setSnapshotState("empty");
          return;
        }
        const snapshot = (await response.json()) as {
          generatedAt?: string;
          properties?: Property[];
        };
        if (snapshot.properties?.length) {
          setData(snapshot.properties);
          setGeneratedAt(snapshot.generatedAt || generatedAt);
          setSelected(snapshot.properties[0]?.folio);
          setSnapshotState("ready");
        } else {
          setData([]);
          setSnapshotState("empty");
        }
      })
      .catch(() => {
        setData([]);
        setSnapshotState("empty");
      });
  }, [city]);
  useEffect(() => {
    fetch("/api/leads")
      .then(async (response) => {
        if (!response.ok) return;
        const result = (await response.json()) as {
          leads?: Array<Lead & { folio: string }>;
        };
        if (result.leads?.length)
          setData((current) =>
            current.map((property) => ({
              ...property,
              lead:
                result.leads?.find((lead) => lead.folio === property.folio) ||
                property.lead,
            })),
          );
      })
      .catch(() => undefined);
  }, [city]);
  const today = new Date().toISOString().slice(0, 10);
  const rows = useMemo(
    () =>
      data.filter(
        (p) =>
          `${p.address} ${p.owner_name}`
            .toLowerCase()
            .includes(query.toLowerCase()) &&
          num(p.priority_score) >= minimum &&
          (!queried || p.clerk_queried) &&
          (stage === "all" || (p.lead?.stage || "new") === stage) &&
          (!dueOnly ||
            Boolean(
              p.lead?.nextFollowUpDate && p.lead.nextFollowUpDate <= today,
            )) &&
          (!contactsOnly || Boolean(p.contacts?.length)),
      ),
    [data, query, minimum, queried, stage, dueOnly, contactsOnly, today],
  );
  const current = data.find((p) => p.folio === selected) || rows[0];
  const download = () => {
    const blob = new Blob([JSON.stringify(data)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${city}-distress-radar.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <>
      <header>
        <h1>MULTIFAMILY DISTRESS RADAR</h1>
        <select
          aria-label="City"
          value={city}
          onChange={(event) => setCity(event.target.value)}
        >
          {CITY_OPTIONS.map((option) => (
            <option key={option.slug} value={option.slug}>
              {option.label}
            </option>
          ))}
        </select>
        <span>Public records snapshot · {date(generatedAt)}</span>
        <button className="export" onClick={download}>
          Export ↓
        </button>
      </header>
      <main>
        <Filters
          {...{
            query,
            setQuery,
            minimum,
            setMinimum,
            queried,
            setQueried,
            stage,
            setStage,
            dueOnly,
            setDueOnly,
            contactsOnly,
            setContactsOnly,
          }}
        />
        <div className="table">
          <div className="tableTop">
            <b>{rows.length} properties</b>
            <span>Priority = code + financial + opportunity</span>
          </div>
          <div className="tableRow tableHeader">
            <span>Rank</span>
            <span>Address / workflow</span>
            <span>Units</span>
            <span>Priority</span>
            <span>Code</span>
            <span>Financial</span>
            <span>Opportunity</span>
            <span>Open cases</span>
            <span>Latest record</span>
          </div>
          {snapshotState === "loading" && (
            <div className="snapshotMessage">
              <b>Loading city snapshot…</b>
            </div>
          )}
          {snapshotState === "empty" && (
            <div className="snapshotMessage">
              <b>
                Awaiting the first{" "}
                {CITY_OPTIONS.find((option) => option.slug === city)?.label}{" "}
                refresh
              </b>
              <span>
                The collector can publish this city independently without
                changing Hialeah data.
              </span>
            </div>
          )}
          {rows.map((p, index) => (
            <button
              className={`tableRow ${p.folio === current?.folio ? "active" : ""}`}
              key={p.folio}
              onClick={() => setSelected(p.folio)}
            >
              <span>{index + 1}</span>
              <b>
                {p.address}
                <small className="rowWorkflow">
                  {p.lead?.stage || "new"}
                  {p.lead?.assignee ? ` · ${p.lead.assignee}` : ""}
                  {p.lead?.nextFollowUpDate && p.lead.nextFollowUpDate <= today
                    ? " · DUE"
                    : ""}
                </small>
              </b>
              <span>{p.unit_count}</span>
              <strong>{p.priority_score}</strong>
              <span>{p.code_enforcement_score}</span>
              <span>{p.financial_distress_score}</span>
              <span>{p.opportunity_score}</span>
              <span>{p.open_case_count}</span>
              <span>{date(p.latest_official_record_date)}</span>
            </button>
          ))}
        </div>
        <Inspector
          property={current}
          onLeadSaved={(folio, savedLead) =>
            setData((currentData) =>
              currentData.map((property) =>
                property.folio === folio
                  ? { ...property, lead: savedLead }
                  : property,
              ),
            )
          }
        />
      </main>
    </>
  );
}
