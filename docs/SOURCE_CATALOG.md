# Source catalog

| Source | Geography | Fields | Access | State | Rate / freshness | Restrictions and gaps |
|---|---|---|---|---|---|---|
| Matrix CSV | Authorized South Florida MLS search scope | MLS number, address, folio, class, status, price, DOM/CDOM, units, NOI, rents, expenses, remarks | User-authorized CSV export | Fixture/manual live import | No requests; expected daily | No MLS login stored. Export columns vary and schema changes fail visibly. |
| Matrix local inbox | Same as attachment | CSV attachment payload | Local `.csv` or saved `.eml` directory | Live local interface | Filesystem scan; expected daily | Gmail/IMAP connector is not implemented. Credentials must never be embedded. |
| Miami-Dade Property Point View | Miami-Dade County | Folio, address, owner, units, building and sale facts | Public ArcGIS service | Live existing collector | Configured polite paging; weekly source | Municipal/class filters and source schema can change. |
| Tyler EnerGov | Configured Hialeah and Surfside jurisdictions | Code case, status, dates, folio, address, description, violations | Public portal API | Live existing collector | Low request rate; refresh by runbook | Status meaning is jurisdiction-specific. No CAPTCHA or private endpoint bypass. |
| Miami-Dade Clerk Official Records | Miami-Dade County | Instruments, document type/date, parties, lien/lis-pendens lifecycle | Official paid API | Disabled without credential | Cache default 30 days; purchased API units | Requires developer access and `MIAMI_DADE_CLERK_AUTH_KEY`; not a title search. |
| Miami-Dade delinquent-tax CSV | Miami-Dade County | Folio, tax year, amount due, payment status | Authorized manual export | Manual | Per supplied export | Human-verification page is not automated. Blank status is unknown. |
| Authorized off-market CSV | User-defined South Florida research scope | Taxes, filings, liens, tenure, absentee, entity status, code escalation, NOI/value inputs | User-provided CSV | Fixture/manual | Per supplied export | The importer does not independently verify the export. Blank fields remain unknown. |
| Sunbiz | Florida | Entity registration and status | Official site/export or authorized service | Manual/not implemented | Unknown until adapter selected | No CAPTCHA or human-verification bypass. Beneficial ownership is not inferred. |
| Permit/unsafe-structure searches | Municipality-specific | Permit/case status | Official export/API or human task | Manual except EnerGov case data | Jurisdiction-specific | No reliable common adapter yet; missing results are unknown. |
| PropertyRadar | Vendor coverage | Property, owner, equity and event fields per subscription | Paid API | Disabled/interface only | Vendor plan dependent | Requires `PROPERTYRADAR_API_KEY`; live method not implemented. |
| ATTOM | Vendor coverage | Property, valuation, transaction and mortgage fields per subscription | Paid API | Disabled/interface only | Vendor plan dependent | Requires `ATTOM_API_KEY`; live method not implemented. |
| Melissa | Vendor coverage | Address/entity/contact verification per subscription | Paid API | Disabled/interface only | Vendor plan dependent | Requires `MELISSA_API_KEY`; live method not implemented. |
| RentCast | Vendor coverage | Rent/value estimates and comparables per subscription | Paid API | Disabled/interface only | Vendor plan dependent | Requires `RENTCAST_API_KEY`; live method not implemented. |

“Live” means the adapter exists and has previously been exercised against its
authorized public endpoint. It does not guarantee current source availability.
Health and collection timestamps must be checked on every run.
