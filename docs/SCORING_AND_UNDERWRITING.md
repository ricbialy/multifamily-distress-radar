# Scoring and underwriting

## Score dimensions

Scores are retained independently on a 0–100 scale:

- Owner motivation: explicit delinquency, lis pendens, recorded liens, long
  ownership, absentee ownership, and inactive entity signals.
- Economics: current/stabilized NOI relative to evidence-backed value or list
  price in the fixture baseline.
- MLS market pressure: DOM/CDOM, motivated-language, expired/withdrawn status,
  and material snapshot changes.
- Property risk: liens, code escalation, and unsafe-structure evidence.
- Data confidence: mean confidence of known evidence.
- Data completeness: known evidence divided by expected evidence.
- Data freshness: fresh evidence divided by all evidence.

Unknown fields do not create positive “clean” signals. High property risk can
produce `reject_high_risk` or a human/municipal review action even when owner
motivation is high.

## Two-to-four units

The model requires legal-unit verification, current and market monthly rent,
vacancy, taxes after sale, insurance, maintenance, utilities, management, and
sale/rent comparable values.

```text
stabilized gross = market monthly rent × 12 × (1 − vacancy)
management = stabilized gross × management rate
stabilized NOI = stabilized gross − taxes − insurance − maintenance
                 − utilities − management
```

Base comparable value is the mean of the supplied sale- and rent-supported
values. Price per unit and, when area exists, price per square foot are shown.

## Five or more units

The model keeps current NOI, reconstructed NOI, and stabilized NOI separate.

```text
effective income = gross potential rent × (1 − market vacancy)
expenses = taxes after sale + insurance + management + utilities
           + maintenance + other operating expenses
stabilized NOI = effective income − expenses
value at cap rate = stabilized NOI ÷ cap rate
```

High/base/low value use the low/base/high market cap-rate assumptions in
inverse order.

## Preliminary offer

Maximum preliminary offer is stabilized value less:

- required return/margin;
- repairs;
- capital expenditures;
- violation/permit contingency;
- closing costs;
- financing costs;
- insurance/flood contingency;
- data-uncertainty reserve.

The fixture baseline sets base offer to 95% and conservative offer to 90% of
that maximum. These are screening values, not bids. Without a supported
stabilized value the status is `insufficient_data` and all offer values are
`null`.
