#!/usr/bin/env bash
set -euo pipefail

LOCAL_ENV="${RECAST_LOCAL_ENV:-$HOME/.config/recast/local-postgres.env}"
if [[ ! -f "$LOCAL_ENV" ]]; then
  echo "Missing local Postgres env: $LOCAL_ENV" >&2
  exit 1
fi

set -a
source "$LOCAL_ENV"
set +a

PSQL=(psql -X -v ON_ERROR_STOP=1 -h "$RECAST_DB_HOST" -p "$RECAST_DB_PORT" -U "$RECAST_DB_USER" -d "$RECAST_DB_NAME")
export PGPASSWORD="$RECAST_DB_PASSWORD"

"${PSQL[@]}" <<'SQL'
\pset pager off

WITH latest_run AS (
  SELECT load_run_id
  FROM meta.load_run
  WHERE status = 'loaded'
  ORDER BY completed_at DESC NULLS LAST, started_at DESC
  LIMIT 1
), expected(tier, relation_name, expected_count) AS (
  VALUES
    ('tier0','source_outerspaces.building_profile_with_coords_subset',4),
    ('tier1','source_outerspaces.building_profile_with_coords_subset',67),
    ('tier0','source_outerspaces.parcel_subset',4),
    ('tier1','source_outerspaces.parcel_subset',67),
    ('tier0','source_outerspaces.kingcounty_raw_parcel_subset',4),
    ('tier1','source_outerspaces.kingcounty_raw_parcel_subset',67),
    ('tier0','source_outerspaces.assessed_value_history_subset',198),
    ('tier1','source_outerspaces.assessed_value_history_subset',3687),
    ('tier0','source_outerspaces.permit_history_subset',60),
    ('tier1','source_outerspaces.permit_history_subset',1383),
    ('tier0','source_outerspaces.availability_signal',2),
    ('tier1','source_outerspaces.availability_signal',49),
    ('tier0','source_outerspaces.seattle_building_energy_benchmarking_subset',20),
    ('tier1','source_outerspaces.seattle_building_energy_benchmarking_subset',319),
    ('tier0','source_outerspaces.jll_building_availability_match_gated',1),
    ('tier1','source_outerspaces.jll_building_availability_match_gated',20),
    ('tier0','source_outerspaces.jll_building_availability_raw_gated',1),
    ('tier1','source_outerspaces.jll_building_availability_raw_gated',20),
    ('tier0','source_outerspaces.distress_seed_match_gated',1),
    ('tier1','source_outerspaces.distress_seed_match_gated',5),
    ('tier0','source_outerspaces.distress_seed_raw_gated',1),
    ('tier1','source_outerspaces.distress_seed_raw_gated',5)
), actual AS (
  SELECT 'tier0' AS tier, 'source_outerspaces.building_profile_with_coords_subset' AS relation_name, count(*)::int AS actual_count FROM source_outerspaces.building_profile_with_coords_subset WHERE load_tier='tier0'
  UNION ALL SELECT 'tier1','source_outerspaces.building_profile_with_coords_subset', count(*)::int FROM source_outerspaces.building_profile_with_coords_subset WHERE load_tier='tier1'
  UNION ALL SELECT 'tier0','source_outerspaces.parcel_subset', count(*)::int FROM source_outerspaces.parcel_subset WHERE load_tier='tier0'
  UNION ALL SELECT 'tier1','source_outerspaces.parcel_subset', count(*)::int FROM source_outerspaces.parcel_subset WHERE load_tier='tier1'
  UNION ALL SELECT 'tier0','source_outerspaces.kingcounty_raw_parcel_subset', count(*)::int FROM source_outerspaces.kingcounty_raw_parcel_subset WHERE load_tier='tier0'
  UNION ALL SELECT 'tier1','source_outerspaces.kingcounty_raw_parcel_subset', count(*)::int FROM source_outerspaces.kingcounty_raw_parcel_subset WHERE load_tier='tier1'
  UNION ALL SELECT 'tier0','source_outerspaces.assessed_value_history_subset', count(*)::int FROM source_outerspaces.assessed_value_history_subset WHERE load_tier='tier0'
  UNION ALL SELECT 'tier1','source_outerspaces.assessed_value_history_subset', count(*)::int FROM source_outerspaces.assessed_value_history_subset WHERE load_tier='tier1'
  UNION ALL SELECT 'tier0','source_outerspaces.permit_history_subset', count(*)::int FROM source_outerspaces.permit_history_subset WHERE load_tier='tier0'
  UNION ALL SELECT 'tier1','source_outerspaces.permit_history_subset', count(*)::int FROM source_outerspaces.permit_history_subset WHERE load_tier='tier1'
  UNION ALL SELECT 'tier0','source_outerspaces.availability_signal', count(*)::int FROM source_outerspaces.availability_signal WHERE load_tier='tier0'
  UNION ALL SELECT 'tier1','source_outerspaces.availability_signal', count(*)::int FROM source_outerspaces.availability_signal WHERE load_tier='tier1'
  UNION ALL SELECT 'tier0','source_outerspaces.seattle_building_energy_benchmarking_subset', count(*)::int FROM source_outerspaces.seattle_building_energy_benchmarking_subset WHERE load_tier='tier0'
  UNION ALL SELECT 'tier1','source_outerspaces.seattle_building_energy_benchmarking_subset', count(*)::int FROM source_outerspaces.seattle_building_energy_benchmarking_subset WHERE load_tier='tier1'
  UNION ALL SELECT 'tier0','source_outerspaces.jll_building_availability_match_gated', count(*)::int FROM source_outerspaces.jll_building_availability_match_gated WHERE load_tier='tier0'
  UNION ALL SELECT 'tier1','source_outerspaces.jll_building_availability_match_gated', count(*)::int FROM source_outerspaces.jll_building_availability_match_gated WHERE load_tier='tier1'
  UNION ALL SELECT 'tier0','source_outerspaces.jll_building_availability_raw_gated', count(*)::int FROM source_outerspaces.jll_building_availability_raw_gated WHERE load_tier='tier0'
  UNION ALL SELECT 'tier1','source_outerspaces.jll_building_availability_raw_gated', count(*)::int FROM source_outerspaces.jll_building_availability_raw_gated WHERE load_tier='tier1'
  UNION ALL SELECT 'tier0','source_outerspaces.distress_seed_match_gated', count(*)::int FROM source_outerspaces.distress_seed_match_gated WHERE load_tier='tier0'
  UNION ALL SELECT 'tier1','source_outerspaces.distress_seed_match_gated', count(*)::int FROM source_outerspaces.distress_seed_match_gated WHERE load_tier='tier1'
  UNION ALL SELECT 'tier0','source_outerspaces.distress_seed_raw_gated', count(*)::int FROM source_outerspaces.distress_seed_raw_gated WHERE load_tier='tier0'
  UNION ALL SELECT 'tier1','source_outerspaces.distress_seed_raw_gated', count(*)::int FROM source_outerspaces.distress_seed_raw_gated WHERE load_tier='tier1'
), checks AS (
  SELECT e.tier, e.relation_name, e.expected_count, a.actual_count,
    CASE WHEN e.expected_count = a.actual_count THEN 'pass' ELSE 'fail' END AS status
  FROM expected e
  JOIN actual a USING (tier, relation_name)
), recorded AS (
  INSERT INTO meta.row_count_check (
    load_run_id, tier, relation_name, expected_count, actual_count, status
  )
  SELECT latest_run.load_run_id, checks.tier, checks.relation_name,
    checks.expected_count, checks.actual_count, checks.status
  FROM checks
  CROSS JOIN latest_run
  RETURNING 1
)
SELECT * FROM checks ORDER BY relation_name, tier;

SELECT building_id, source_parcel_id, address, latitude, longitude
FROM recast.building
WHERE building_id IN (
  'king_county_wa:0653000250:profile',
  'king_county_wa:1975700380:profile',
  'king_county_wa:0660000650:profile',
  'king_county_wa:4088803750:profile'
)
ORDER BY address;

SELECT 'recast_building' AS relation, count(*) FROM recast.building
UNION ALL SELECT 'recast_signal_snapshot', count(*) FROM recast.building_signal_snapshot
UNION ALL SELECT 'recast_attention_candidate', count(*) FROM recast.building_attention_candidate
UNION ALL SELECT 'recast_value_trajectory', count(*) FROM recast.building_value_trajectory
UNION ALL SELECT 'recast_permit_activity', count(*) FROM recast.building_permit_activity
UNION ALL SELECT 'recast_availability', count(*) FROM recast.building_availability
UNION ALL SELECT 'recast_energy_signal', count(*) FROM recast.building_energy_signal
UNION ALL SELECT 'recast_debt_maturity_signal', count(*) FROM recast.debt_maturity_signal
ORDER BY relation;

SELECT pg_size_pretty(pg_database_size(current_database())) AS local_recast_database_size;
SQL
