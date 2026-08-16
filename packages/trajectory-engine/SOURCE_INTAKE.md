# Evidence Intake and Review Map

The trajectory engine calculates from reviewed evidence; it does not collect or
verify evidence itself. This intake map is the manual or future adapter boundary
for building an actual property file without weakening Recast's evidence rules.

## Source hierarchy

Use the strongest available source for each field. A search result, news story,
broker note, or model-generated summary can create a review task but should not
silently become a `KNOWN` fact.

| Pipeline input | Preferred evidence | Public cross-check | Do not substitute |
| --- | --- | --- | --- |
| Current use, area, parcel | Assessor record, certificate of occupancy, owner records | Seattle permits/zoning and King County parcel records | Map category or generic web snippet |
| Acquisition date/price | Recorded deed and assessor sale record | King County Recorder search | Current value or current loan balance |
| Current market value | Current appraisal or reviewed valuation | Assessed value and comparable transactions | Acquisition price |
| Current loan balance | Current lender/payoff statement | None reliably establishes it | Original principal or recorded deed amount |
| Rate, debt service, maturity | Executed loan documents plus current lender statement | Recorded instrument/court filing when it explicitly states the term | Sale/refi year or market convention |
| Lease area, rent, expiry, options | Current certified rent roll and executed lease/abstract | Public filings only when the exact lease term is disclosed | Website tenant list |
| Renewal range | Reviewed leasing underwriting plus tenant contact evidence | Tenant filings, sublease listings, hiring and office-use signals | A model's unsupported percentage |
| Tenant entity/status | Executed lease entity and guarantor | Washington corporation search and SEC filings | Brand name alone |
| Bankruptcy/legal distress | Court docket/document tied to the exact entity | PACER | Rumor, social media, or merely poor earnings |
| Layoff event | Tenant disclosure or official WARN notice | Washington WARN records | General industry layoffs |
| AI space-demand exposure | Tenant function/occupation mix and documented space strategy | O*NET and BLS industry/occupation context | Predicted headcount loss |
| Reuse fit | Site measurements, plans, code/zoning review, demand study, cost plan | Seattle SDCI and adopted planning materials | A generic “best use” suggestion |

## Official public-source starting points

- [King County Recorder records search](https://kingcounty.gov/en/dept/executive-services/certificates-permits-licenses/records-licensing/recorders-office/records-search): recorded deeds, deeds of trust, assignments, releases, and related property records. A recorded amount or date does not prove the current balance or maturity unless the document explicitly says so.
- [SEC EDGAR filing search](https://www.sec.gov/search-filings): primary-source public-company filings. Match the tenant or guarantor legal entity before attaching a filing to a lease.
- [PACER](https://pacer.uscourts.gov/): federal court dockets and bankruptcy evidence. “Receiver appointed” or “bankruptcy filed” requires the supporting docket/document and exact entity match.
- [Washington WARN notices](https://esd.wa.gov/employer-requirements/layoffs-and-employee-notifications): qualifying layoff/closure notices. A notice is an event, not a complete employment forecast.
- [Washington corporation search](https://ccfs.sos.wa.gov/): legal-entity status and names. Active registration is not evidence of financial health.
- [New York Fed reference rates](https://www.newyorkfed.org/markets/reference-rates): SOFR reference data for an explicitly floating-rate loan. The loan spread, floors, caps, and reset terms still require loan documents.
- [O*NET](https://www.onetonline.org/help/onet/) and the [BLS industry-occupation matrix](https://www.bls.gov/emp/tables/industry-occupation-matrix-industry.htm): occupation and industry context for a reviewed AI-exposure flag. Neither source predicts a named tenant's layoffs or space decision.
- [Seattle SDCI](https://www.seattle.gov/sdci): permits, land-use, zoning, and code review starting point. A preliminary screen is not an entitlement or compliance conclusion.

## Review gates

Before a source can populate a usable fact:

1. Resolve the parcel, building, tenant legal entity, borrower, and guarantor.
2. Save a stable `source_ref` and the date reviewed.
3. Record what the source actually proves, not what it merely suggests.
4. Add limitations when scope, freshness, or entity matching is imperfect.
5. Use `INFERRED` for a reasoned estimate and retain the underlying evidence.
6. Use `UNKNOWN` or `INSUFFICIENT_EVIDENCE` when the source does not establish the field.
7. Obtain human approval before replacing a fact used in an owner- or lender-facing scenario.

## Tenant renewal workflow

Renewal is never a single scraped fact. Build the low/high range from reviewed
evidence such as lease options and notice dates, tenant interviews, utilization,
local hiring, public filings, sublease activity, relocation announcements,
business health, and the importance of the location. Document the rationale in
the source record. AI exposure may be one diligence factor, but the engine does
not automatically reduce renewal because a tenant is considered exposed.

## Alternative-use workflow

Only add a candidate after identifying why it may fit the building and its
surroundings. Each candidate needs four separate facts:

- physical fit: floorplate, depth, structure, envelope, MEP, access, parking;
- regulatory fit: zoning, occupancy, code, entitlement, environmental issues;
- market fit: named demand, achievable use and rents, competing supply;
- financial fit: concept scope, capex, downtime, operating economics, capital.

Any `fail` screens the candidate out. Any unknown produces
`INSUFFICIENT_EVIDENCE`. Passing the screen means only that the candidate may
advance to due diligence.

## Future adapter boundary

A later data-collection layer can produce this package's input JSON, but it
should remain separate from both the BHI calculator and the scenario engine.
Adapters should store raw source snapshots, entity matches, review status, and
field-level evidence labels. The engine should continue accepting only the
reviewed contract so the same input always produces the same output.
