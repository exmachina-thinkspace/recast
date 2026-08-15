# Recast Data Docs

| Document | Purpose |
| --- | --- |
| [local-recast-postgres-plan.md](local-recast-postgres-plan.md) | Architecture plan for a local GB100 PostgreSQL database named `recast` |
| [local-recast-data-manifest.md](local-recast-data-manifest.md) | Exact Tier 0 + Tier 1 data movement manifest and validation plan |

## Implementation Artifacts

| Artifact | Purpose |
| --- | --- |
| [`../../db/schema/001_local_recast.sql`](../../db/schema/001_local_recast.sql) | Local `recast` database schemas, source tables, derived tables, and lineage tables |
| [`../../scripts/load-local-recast.sh`](../../scripts/load-local-recast.sh) | GB100 direct loader from Outerspaces/Supabase into local PostgreSQL |
| [`../../scripts/validate-local-recast.sh`](../../scripts/validate-local-recast.sh) | Source row-count validation and hero-building sanity checks |

Current GB100 status: Tier 0 + Tier 1 public foundation loaded and validated in local PostgreSQL `recast`. Private/review-gated datasets are intentionally still excluded.
