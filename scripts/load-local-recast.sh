#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

LOCAL_ENV="${RECAST_LOCAL_ENV:-$HOME/.config/recast/local-postgres.env}"
OUTERSPACES_ENV="${OUTERSPACES_ENV:-$HOME/.config/recast/outerspaces.env}"

if [[ ! -f "$LOCAL_ENV" ]]; then
  echo "Missing local Postgres env: $LOCAL_ENV" >&2
  exit 1
fi
if [[ ! -f "$OUTERSPACES_ENV" ]]; then
  echo "Missing Outerspaces env: $OUTERSPACES_ENV" >&2
  exit 1
fi

set -a
source "$LOCAL_ENV"
source "$OUTERSPACES_ENV"
set +a

LOCAL_PSQL=(psql -X -v ON_ERROR_STOP=1 -h "$RECAST_DB_HOST" -p "$RECAST_DB_PORT" -U "$RECAST_DB_USER" -d "$RECAST_DB_NAME")
REMOTE_PSQL=(psql -X -v ON_ERROR_STOP=1 -h "$OUTERSPACES_DB_HOST" -p "$OUTERSPACES_DB_PORT" -U "$OUTERSPACES_DB_USER" -d "$OUTERSPACES_DB_NAME")

export PGPASSWORD="$RECAST_DB_PASSWORD"
"${LOCAL_PSQL[@]}" -f "$REPO_ROOT/db/schema/001_local_recast.sql" >/dev/null

LOAD_RUN_ID="${LOAD_RUN_ID:-recast_$(date -u +%Y%m%dT%H%M%SZ)}"
TRANSFER_MECHANISM="gb100_direct_psql_copy"

mark_load_failed() {
  local exit_code="$?"
  export PGPASSWORD="$RECAST_DB_PASSWORD"
  "${LOCAL_PSQL[@]}" -v load_run_id="$LOAD_RUN_ID" -v exit_code="$exit_code" <<'SQL' >/dev/null || true
UPDATE meta.load_run
SET completed_at = now(),
    status = 'failed',
    notes = concat_ws(' ', notes, 'Loader exited before completion with code', :'exit_code', 'at', now()::text)
WHERE load_run_id = :'load_run_id'
  AND status = 'running';
SQL
  exit "$exit_code"
}
trap mark_load_failed ERR

"${LOCAL_PSQL[@]}" -v load_run_id="$LOAD_RUN_ID" -v transfer_mechanism="$TRANSFER_MECHANISM" <<'SQL'
INSERT INTO meta.load_run (load_run_id, source_system, tier_scope, transfer_mechanism, status, notes)
VALUES (:'load_run_id', 'outerspaces_supabase', 'tier0+tier1_public_foundation', :'transfer_mechanism', 'running', 'GB100 direct pull from Outerspaces Supabase; no Mac persistent staging.')
ON CONFLICT (load_run_id) DO UPDATE
SET started_at = now(), completed_at = NULL, status = 'running', transfer_mechanism = EXCLUDED.transfer_mechanism;

TRUNCATE
  source_outerspaces.market,
  source_outerspaces.asset_class_taxonomy,
  source_outerspaces.building_profile_with_coords_subset,
  source_outerspaces.parcel_subset,
  source_outerspaces.kingcounty_raw_parcel_subset,
  source_outerspaces.assessed_value_history_subset,
  source_outerspaces.permit_history_subset,
  source_outerspaces.availability_signal,
  source_outerspaces.seattle_building_energy_benchmarking_subset,
  source_outerspaces.jll_building_availability_raw_gated,
  source_outerspaces.jll_building_availability_match_gated,
  source_outerspaces.distress_seed_raw_gated,
  source_outerspaces.distress_seed_match_gated,
  recast.building,
  recast.building_value_trajectory,
  recast.building_permit_activity,
  recast.building_availability,
  recast.building_energy_signal,
  recast.building_signal_snapshot,
  recast.building_attention_candidate,
  recast.debt_instrument,
  recast.debt_event,
  recast.maturity_estimate,
  recast.debt_maturity_signal
RESTART IDENTITY;
SQL

copy_query() {
  local destination="$1"
  local columns="$2"
  local query="$3"
  local attempt
  for attempt in 1 2 3; do
    if (
      set -o pipefail
      export PGPASSWORD="$OUTERSPACES_DB_PASSWORD"
      "${REMOTE_PSQL[@]}" -c "\\copy ($query) TO STDOUT WITH CSV HEADER" |
        env PGPASSWORD="$RECAST_DB_PASSWORD" "${LOCAL_PSQL[@]}" -c "\\copy $destination ($columns) FROM STDIN WITH CSV HEADER"
    ); then
      return 0
    fi
    if [[ "$attempt" -lt 3 ]]; then
      sleep "$((attempt * 5))"
    fi
  done
  echo "COPY failed after 3 attempts for $destination" >&2
  return 1
}

record_manifest() {
  local tier="$1" source="$2" destination="$3" filter="$4" expected="$5" actual="$6" privacy="${7:-public_or_derived}"
  export PGPASSWORD="$RECAST_DB_PASSWORD"
  "${LOCAL_PSQL[@]}" -v load_run_id="$LOAD_RUN_ID" -v tier="$tier" -v source="$source" -v destination="$destination" -v filter="$filter" -v expected="$expected" -v actual="$actual" -v privacy="$privacy" <<'SQL' >/dev/null
INSERT INTO meta.source_export_manifest (
  load_run_id, tier, source_relation, destination_relation, source_filter,
  expected_row_count, actual_row_count, privacy_posture, validation_status
) VALUES (
  :'load_run_id', :'tier', :'source', :'destination', :'filter',
  :'expected'::integer, :'actual'::integer, :'privacy', CASE WHEN :'expected'::integer = :'actual'::integer THEN 'passed' ELSE 'mismatch' END
);
INSERT INTO meta.source_relation_snapshot (
  load_run_id, tier, source_relation, destination_relation, expected_row_count, actual_row_count, notes
) VALUES (
  :'load_run_id', :'tier', :'source', :'destination', :'expected'::integer, :'actual'::integer, 'Recorded immediately after COPY load.'
);
SQL
}

count_local() {
  local relation="$1" where_clause="${2:-true}"
  export PGPASSWORD="$RECAST_DB_PASSWORD"
  "${LOCAL_PSQL[@]}" -tAc "SELECT count(*) FROM $relation WHERE $where_clause"
}

count_tier() {
  local relation="$1" tier="$2"
  case "$tier" in
    tier0|tier1|support) ;;
    *) echo "Unsupported tier for count_tier: $tier" >&2; exit 1 ;;
  esac
  export PGPASSWORD="$RECAST_DB_PASSWORD"
  "${LOCAL_PSQL[@]}" -tAc "SELECT count(*) FROM $relation WHERE load_tier = '$tier'"
}

TIER0_IDS="(building_id, source_parcel_id) IN (VALUES ('king_county_wa:0653000250:profile','0653000250'),('king_county_wa:1975700380:profile','1975700380'),('king_county_wa:0660000650:profile','0660000650'),('king_county_wa:4088803750:profile','4088803750'))"
TIER0_PARCELS="source_parcel_id IN ('0653000250','1975700380','0660000650','4088803750')"
TIER0_RAW_PARCELS="parcel_id IN ('0653000250','1975700380','0660000650','4088803750')"
TIER0_ENERGY="tax_parcel_identification_number IN ('0653000250','1975700380','0660000650','4088803750')"

TIER1_CTE="WITH base AS (
  SELECT *
  FROM warehouse.building_profile_with_coords
  WHERE city = 'Seattle'
    AND state = 'WA'
    AND parcel_centroid_lat BETWEEN 47.58 AND 47.66
    AND parcel_centroid_lon BETWEEN -122.37 AND -122.30
    AND parcel_centroid_lat IS NOT NULL
    AND parcel_centroid_lon IS NOT NULL
), office_base AS (
  SELECT *
  FROM base
  WHERE (lower(coalesce(asset_class, '')) = 'office' OR lower(coalesce(source_use_description, '')) LIKE '%office%')
    AND coalesce(gross_sf, 0) >= 25000
), tier1_buildings AS (
  SELECT DISTINCT b.building_id, b.source_parcel_id
  FROM office_base b
  LEFT JOIN warehouse.availability_signal a ON a.building_id = b.building_id
  WHERE coalesce(b.peak_to_current_compression_pct, 0) <= -25
     OR a.building_id IS NOT NULL
     OR coalesce(b.permit_count, 0) = 0
     OR b.latest_permit_date < DATE '2019-01-01'
)"

BP_COLS="load_tier,market_id,market_name,city,state,source_jurisdiction,source_parcel_id,building_id,address,asset_class,source_use_code,source_use_description,gross_sf,net_sf,stories,year_built,building_age,effective_year,owner_proxy,latest_sale_date,latest_sale_price,sale_instrument,sale_reason,property_class,current_assessment_year,current_assessed_value,peak_assessment_year,peak_assessed_value,peak_to_current_compression_pct,peak_to_current_compression_value,purchase_basis_compression_pct,permit_count,latest_permit_date,permit_value_since_2019,supported_signals,missing_signal_flags,artifact_risk_flags,signal_confidence,parcel_centroid_lat,parcel_centroid_lon,updated_at,load_run_id"
BP_SELECT="market_id,market_name,city,state,source_jurisdiction,source_parcel_id,building_id,address,asset_class,source_use_code,source_use_description,gross_sf,net_sf,stories,year_built,building_age,effective_year,owner_proxy,latest_sale_date,latest_sale_price,sale_instrument,sale_reason,property_class,current_assessment_year,current_assessed_value,peak_assessment_year,peak_assessed_value,peak_to_current_compression_pct,peak_to_current_compression_value,purchase_basis_compression_pct,permit_count,latest_permit_date,permit_value_since_2019,supported_signals,missing_signal_flags,artifact_risk_flags,signal_confidence,parcel_centroid_lat,parcel_centroid_lon,updated_at"

PARCEL_COLS="load_tier,market_id,source_jurisdiction,source_parcel_id,normalized_parcel_id,major,minor,address,city,state,zip,latitude,longitude,lot_sqft,zoning,present_use_raw,source_table,source_updated_at,load_run_id"
PARCEL_SELECT="market_id,source_jurisdiction,source_parcel_id,normalized_parcel_id,major,minor,address,city,state,zip,latitude,longitude,lot_sqft,zoning,present_use_raw,source_table,source_updated_at"

RAW_PARCEL_COLS="load_tier,parcel_id,major,minor,prop_name,prop_type,current_zoning,hbu_as_if_vacant,hbu_as_improved,present_use,sq_ft_lot,water_system,sewer_system,access,topography,street_surface,inadequate_parking,unbuildable,traffic_noise,contamination,historic_site,current_use_designation,latitude,longitude,imported_at,load_run_id"
RAW_PARCEL_SELECT="parcel_id,major,minor,prop_name,prop_type,current_zoning,hbu_as_if_vacant,hbu_as_improved,present_use,sq_ft_lot,water_system,sewer_system,access,topography,street_surface,inadequate_parking,unbuildable,traffic_noise,contamination,historic_site,current_use_designation,latitude,longitude,imported_at"

VALUE_COLS="load_tier,market_id,source_parcel_id,assessment_year,land_value,improvement_value,total_assessed_value,value_reason,source_table,source_updated_at,load_run_id"
VALUE_SELECT="market_id,source_parcel_id,assessment_year,land_value,improvement_value,total_assessed_value,value_reason,source_table,source_updated_at"

PERMIT_COLS="load_tier,market_id,source_parcel_id,permit_number,permit_type,permit_status,issue_date,permit_value,percent_complete,description,source_table,source_updated_at,load_run_id"
PERMIT_SELECT="market_id,source_parcel_id,permit_number,permit_type,permit_status,issue_date,permit_value,percent_complete,description,source_table,source_updated_at"

AVAIL_COLS="load_tier,availability_signal_id,building_id,market_id,source_parcel_id,address,asset_class,gross_sf,owner_proxy,availability_sf,direct_available_sf,sublease_available_sf,availability_pct,source,source_type,source_url,collection_method,last_observed,confidence,coverage_status,match_confidence,notes,current_assessed_value,peak_assessed_value,peak_to_current_compression_pct,updated_at,load_run_id"
AVAIL_SELECT="availability_signal_id,building_id,market_id,source_parcel_id,address,asset_class,gross_sf,owner_proxy,availability_sf,direct_available_sf,sublease_available_sf,availability_pct,source,source_type,source_url,collection_method,last_observed,confidence,coverage_status,match_confidence,notes,current_assessed_value,peak_assessed_value,peak_to_current_compression_pct,updated_at"

ENERGY_COLS="load_tier,ose_building_id,data_year,building_name,building_type,tax_parcel_identification_number,address,latitude,longitude,year_built,number_of_floors,property_gfa_total,energy_star_score,site_eui_wn_kbtu_sf,site_eui_kbtu_sf,site_energy_use_kbtu,source_eui_kbtu_sf,electricity_kwh,natural_gas_therms,compliance_status,compliance_issue,total_ghg_emissions,ghg_emissions_intensity,jurisdiction,loaded_at,load_run_id"
ENERGY_SELECT="ose_building_id,data_year,building_name,building_type,tax_parcel_identification_number,address,latitude,longitude,year_built,number_of_floors,property_gfa_total,energy_star_score,site_eui_wn_kbtu_sf,site_eui_kbtu_sf,site_energy_use_kbtu,source_eui_kbtu_sf,electricity_kwh,natural_gas_therms,compliance_status,compliance_issue,total_ghg_emissions,ghg_emissions_intensity,jurisdiction,loaded_at"

JLL_RAW_COLS="load_tier,jll_raw_id,source_report,source_file_name,source_page,source_row_number,extracted_at,building_name,property_address,city,state,zip,submarket,property_type,rentable_building_area_sf,percent_leased,available_min_sf,available_total_sf,max_contiguous_sf,available_share_of_rba,asking_rent,rent_posture,occupancy_timing,owner_name,extraction_confidence,review_status,reviewer_notes,created_at,updated_at,load_run_id"
JLL_RAW_SELECT="jll_raw_id,source_report,source_file_name,source_page,source_row_number,extracted_at,building_name,property_address,city,state,zip,submarket,property_type,rentable_building_area_sf,percent_leased,available_min_sf,available_total_sf,max_contiguous_sf,available_share_of_rba,asking_rent,rent_posture,occupancy_timing,owner_name,extraction_confidence,review_status,reviewer_notes,created_at,updated_at"

JLL_MATCH_COLS="load_tier,jll_match_id,jll_raw_id,source_parcel_id,building_id,matched_address,matched_building_name,parcel_centroid_lat,parcel_centroid_lon,match_status,match_method,match_confidence,match_score,candidate_rank,candidate_count,review_status,reviewer_notes,created_at,updated_at,load_run_id"
JLL_MATCH_SELECT="jll_match_id,jll_raw_id,source_parcel_id,building_id,matched_address,matched_building_name,parcel_centroid_lat,parcel_centroid_lon,match_status,match_method,match_confidence,match_score,candidate_rank,candidate_count,review_status,reviewer_notes,created_at,updated_at"

DISTRESS_RAW_COLS="load_tier,seed_id,source_key,tier,cohort_initial,building_name,address,city,state,distress_types,claim_summary,vacancy_pct_claimed,availability_sf_claimed,value_decline_pct_claimed,debt_amount_claimed,evidence_status,review_status,source_note,source_urls,entered_by,entered_at,updated_at,load_run_id"
DISTRESS_RAW_SELECT="seed_id,source_key,tier,cohort_initial,building_name,address,city,state,distress_types,claim_summary,vacancy_pct_claimed,availability_sf_claimed,value_decline_pct_claimed,debt_amount_claimed,evidence_status,review_status,source_note,source_urls,entered_by,entered_at,updated_at"

DISTRESS_MATCH_COLS="load_tier,seed_id,source_key,match_status,match_method,match_confidence,market_id,building_id,source_parcel_id,matched_address,matched_building_name,parcel_centroid_lat,parcel_centroid_lon,review_notes,reviewed_by,reviewed_at,updated_at,load_run_id"
DISTRESS_MATCH_SELECT="seed_id,source_key,match_status,match_method,match_confidence,market_id,building_id,source_parcel_id,matched_address,matched_building_name,parcel_centroid_lat,parcel_centroid_lon,review_notes,reviewed_by,reviewed_at,updated_at"

copy_query source_outerspaces.market "market_id,market_name,state,country,source_jurisdiction,primary_source_system,active,created_at,updated_at,load_run_id" "SELECT market_id,market_name,state,country,source_jurisdiction,primary_source_system,active,created_at,updated_at,'$LOAD_RUN_ID' FROM warehouse.market"
record_manifest support warehouse.market source_outerspaces.market "all rows" 3 "$(count_local source_outerspaces.market)"

copy_query source_outerspaces.asset_class_taxonomy "taxonomy_id,source_system,source_jurisdiction,source_use_code,source_use_description,asset_class,market_asset_flag,non_market_flag,artifact_risk_flag,notes,reviewed,reviewed_by,reviewed_at,created_at,updated_at,load_run_id" "SELECT taxonomy_id,source_system,source_jurisdiction,source_use_code,source_use_description,asset_class,market_asset_flag,non_market_flag,artifact_risk_flag,notes,reviewed,reviewed_by,reviewed_at,created_at,updated_at,'$LOAD_RUN_ID' FROM warehouse.asset_class_taxonomy"
record_manifest support warehouse.asset_class_taxonomy source_outerspaces.asset_class_taxonomy "all rows" 255 "$(count_local source_outerspaces.asset_class_taxonomy)"

copy_query source_outerspaces.building_profile_with_coords_subset "$BP_COLS" "SELECT 'tier0',$BP_SELECT,'$LOAD_RUN_ID' FROM warehouse.building_profile_with_coords WHERE $TIER0_IDS"
record_manifest tier0 warehouse.building_profile_with_coords source_outerspaces.building_profile_with_coords_subset "exact four Tier 0 building_id/source_parcel_id pairs" 4 "$(count_tier source_outerspaces.building_profile_with_coords_subset tier0)"

copy_query source_outerspaces.building_profile_with_coords_subset "$BP_COLS" "$TIER1_CTE SELECT 'tier1',$BP_SELECT,'$LOAD_RUN_ID' FROM warehouse.building_profile_with_coords b JOIN tier1_buildings t USING (building_id, source_parcel_id)"
record_manifest tier1 warehouse.building_profile_with_coords source_outerspaces.building_profile_with_coords_subset "manifest Tier 1 downtown/SLU office opportunity filter" 67 "$(count_tier source_outerspaces.building_profile_with_coords_subset tier1)"

copy_query source_outerspaces.parcel_subset "$PARCEL_COLS" "SELECT 'tier0',$PARCEL_SELECT,'$LOAD_RUN_ID' FROM warehouse.parcel WHERE $TIER0_PARCELS"
record_manifest tier0 warehouse.parcel source_outerspaces.parcel_subset "Tier 0 parcels" 4 "$(count_tier source_outerspaces.parcel_subset tier0)"

copy_query source_outerspaces.parcel_subset "$PARCEL_COLS" "$TIER1_CTE SELECT 'tier1',$PARCEL_SELECT,'$LOAD_RUN_ID' FROM warehouse.parcel p JOIN tier1_buildings t USING (source_parcel_id)"
record_manifest tier1 warehouse.parcel source_outerspaces.parcel_subset "Tier 1 parcels" 67 "$(count_tier source_outerspaces.parcel_subset tier1)"

copy_query source_outerspaces.kingcounty_raw_parcel_subset "$RAW_PARCEL_COLS" "SELECT 'tier0',$RAW_PARCEL_SELECT,'$LOAD_RUN_ID' FROM public.kingcounty_raw_parcel WHERE $TIER0_RAW_PARCELS"
record_manifest tier0 public.kingcounty_raw_parcel source_outerspaces.kingcounty_raw_parcel_subset "Tier 0 parcels" 4 "$(count_tier source_outerspaces.kingcounty_raw_parcel_subset tier0)"

copy_query source_outerspaces.kingcounty_raw_parcel_subset "$RAW_PARCEL_COLS" "$TIER1_CTE SELECT 'tier1',$RAW_PARCEL_SELECT,'$LOAD_RUN_ID' FROM public.kingcounty_raw_parcel r JOIN tier1_buildings t ON r.parcel_id=t.source_parcel_id"
record_manifest tier1 public.kingcounty_raw_parcel source_outerspaces.kingcounty_raw_parcel_subset "Tier 1 parcels" 67 "$(count_tier source_outerspaces.kingcounty_raw_parcel_subset tier1)"

copy_query source_outerspaces.assessed_value_history_subset "$VALUE_COLS" "SELECT 'tier0',$VALUE_SELECT,'$LOAD_RUN_ID' FROM warehouse.assessed_value_history WHERE $TIER0_PARCELS"
record_manifest tier0 warehouse.assessed_value_history source_outerspaces.assessed_value_history_subset "Tier 0 parcels" 198 "$(count_tier source_outerspaces.assessed_value_history_subset tier0)"

copy_query source_outerspaces.assessed_value_history_subset "$VALUE_COLS" "$TIER1_CTE SELECT 'tier1',$VALUE_SELECT,'$LOAD_RUN_ID' FROM warehouse.assessed_value_history v JOIN tier1_buildings t USING (source_parcel_id)"
record_manifest tier1 warehouse.assessed_value_history source_outerspaces.assessed_value_history_subset "Tier 1 parcels" 3687 "$(count_tier source_outerspaces.assessed_value_history_subset tier1)"

copy_query source_outerspaces.permit_history_subset "$PERMIT_COLS" "SELECT 'tier0',$PERMIT_SELECT,'$LOAD_RUN_ID' FROM warehouse.permit_history WHERE $TIER0_PARCELS"
record_manifest tier0 warehouse.permit_history source_outerspaces.permit_history_subset "Tier 0 parcels" 60 "$(count_tier source_outerspaces.permit_history_subset tier0)"

copy_query source_outerspaces.permit_history_subset "$PERMIT_COLS" "$TIER1_CTE SELECT 'tier1',$PERMIT_SELECT,'$LOAD_RUN_ID' FROM warehouse.permit_history p JOIN tier1_buildings t USING (source_parcel_id)"
record_manifest tier1 warehouse.permit_history source_outerspaces.permit_history_subset "Tier 1 parcels" 1383 "$(count_tier source_outerspaces.permit_history_subset tier1)"

copy_query source_outerspaces.availability_signal "$AVAIL_COLS" "SELECT 'tier0',$AVAIL_SELECT,'$LOAD_RUN_ID' FROM warehouse.availability_signal WHERE building_id IN ('king_county_wa:0653000250:profile','king_county_wa:1975700380:profile','king_county_wa:0660000650:profile','king_county_wa:4088803750:profile')"
record_manifest tier0 warehouse.availability_signal source_outerspaces.availability_signal "Tier 0 buildings" 2 "$(count_tier source_outerspaces.availability_signal tier0)"

copy_query source_outerspaces.availability_signal "$AVAIL_COLS" "$TIER1_CTE SELECT 'tier1',$AVAIL_SELECT,'$LOAD_RUN_ID' FROM (SELECT a.* FROM warehouse.availability_signal a JOIN tier1_buildings t USING (building_id)) a"
record_manifest tier1 warehouse.availability_signal source_outerspaces.availability_signal "Tier 1 buildings" 49 "$(count_tier source_outerspaces.availability_signal tier1)"

copy_query source_outerspaces.seattle_building_energy_benchmarking_subset "$ENERGY_COLS" "SELECT 'tier0',$ENERGY_SELECT,'$LOAD_RUN_ID' FROM raw.seattle_building_energy_benchmarking WHERE $TIER0_ENERGY"
record_manifest tier0 raw.seattle_building_energy_benchmarking source_outerspaces.seattle_building_energy_benchmarking_subset "Tier 0 parcels" 20 "$(count_tier source_outerspaces.seattle_building_energy_benchmarking_subset tier0)"

copy_query source_outerspaces.seattle_building_energy_benchmarking_subset "$ENERGY_COLS" "$TIER1_CTE SELECT 'tier1',$ENERGY_SELECT,'$LOAD_RUN_ID' FROM (SELECT e.* FROM raw.seattle_building_energy_benchmarking e JOIN tier1_buildings t ON e.tax_parcel_identification_number=t.source_parcel_id) e"
record_manifest tier1 raw.seattle_building_energy_benchmarking source_outerspaces.seattle_building_energy_benchmarking_subset "Tier 1 parcels" 319 "$(count_tier source_outerspaces.seattle_building_energy_benchmarking_subset tier1)"

copy_query source_outerspaces.jll_building_availability_match_gated "$JLL_MATCH_COLS" "SELECT 'tier0',$JLL_MATCH_SELECT,'$LOAD_RUN_ID' FROM private.jll_building_availability_match WHERE building_id IN ('king_county_wa:0653000250:profile','king_county_wa:1975700380:profile','king_county_wa:0660000650:profile','king_county_wa:4088803750:profile')"
record_manifest tier0 private.jll_building_availability_match source_outerspaces.jll_building_availability_match_gated "Tier 0 buildings, review-gated" 1 "$(count_tier source_outerspaces.jll_building_availability_match_gated tier0)" "private_review_gated"

copy_query source_outerspaces.jll_building_availability_match_gated "$JLL_MATCH_COLS" "$TIER1_CTE SELECT 'tier1',$JLL_MATCH_SELECT,'$LOAD_RUN_ID' FROM (SELECT m.* FROM private.jll_building_availability_match m JOIN tier1_buildings t USING (building_id)) m"
record_manifest tier1 private.jll_building_availability_match source_outerspaces.jll_building_availability_match_gated "Tier 1 buildings, review-gated" 20 "$(count_tier source_outerspaces.jll_building_availability_match_gated tier1)" "private_review_gated"

copy_query source_outerspaces.jll_building_availability_raw_gated "$JLL_RAW_COLS" "SELECT 'tier0',$JLL_RAW_SELECT,'$LOAD_RUN_ID' FROM private.jll_building_availability_raw WHERE jll_raw_id IN (SELECT jll_raw_id FROM private.jll_building_availability_match WHERE building_id IN ('king_county_wa:0653000250:profile','king_county_wa:1975700380:profile','king_county_wa:0660000650:profile','king_county_wa:4088803750:profile'))"
record_manifest tier0 private.jll_building_availability_raw source_outerspaces.jll_building_availability_raw_gated "Tier 0 matched rows, review-gated; extraction_text/raw_payload excluded" 1 "$(count_tier source_outerspaces.jll_building_availability_raw_gated tier0)" "private_review_gated"

copy_query source_outerspaces.jll_building_availability_raw_gated "$JLL_RAW_COLS" "$TIER1_CTE SELECT 'tier1',$JLL_RAW_SELECT,'$LOAD_RUN_ID' FROM (SELECT r.* FROM private.jll_building_availability_raw r JOIN private.jll_building_availability_match m USING (jll_raw_id) JOIN tier1_buildings t USING (building_id)) r"
record_manifest tier1 private.jll_building_availability_raw source_outerspaces.jll_building_availability_raw_gated "Tier 1 matched rows, review-gated; extraction_text/raw_payload excluded" 20 "$(count_tier source_outerspaces.jll_building_availability_raw_gated tier1)" "private_review_gated"

copy_query source_outerspaces.distress_seed_match_gated "$DISTRESS_MATCH_COLS" "SELECT 'tier0',$DISTRESS_MATCH_SELECT,'$LOAD_RUN_ID' FROM private.build_vitals_distress_seed_match WHERE building_id IN ('king_county_wa:0653000250:profile','king_county_wa:1975700380:profile','king_county_wa:0660000650:profile','king_county_wa:4088803750:profile')"
record_manifest tier0 private.build_vitals_distress_seed_match source_outerspaces.distress_seed_match_gated "Tier 0 buildings, review-gated" 1 "$(count_tier source_outerspaces.distress_seed_match_gated tier0)" "private_review_gated"

copy_query source_outerspaces.distress_seed_match_gated "$DISTRESS_MATCH_COLS" "$TIER1_CTE SELECT 'tier1',$DISTRESS_MATCH_SELECT,'$LOAD_RUN_ID' FROM (SELECT m.* FROM private.build_vitals_distress_seed_match m JOIN tier1_buildings t USING (building_id)) m"
record_manifest tier1 private.build_vitals_distress_seed_match source_outerspaces.distress_seed_match_gated "Tier 1 buildings, review-gated" 5 "$(count_tier source_outerspaces.distress_seed_match_gated tier1)" "private_review_gated"

copy_query source_outerspaces.distress_seed_raw_gated "$DISTRESS_RAW_COLS" "SELECT 'tier0',$DISTRESS_RAW_SELECT,'$LOAD_RUN_ID' FROM private.build_vitals_distress_seed_raw WHERE seed_id IN (SELECT seed_id FROM private.build_vitals_distress_seed_match WHERE building_id IN ('king_county_wa:0653000250:profile','king_county_wa:1975700380:profile','king_county_wa:0660000650:profile','king_county_wa:4088803750:profile'))"
record_manifest tier0 private.build_vitals_distress_seed_raw source_outerspaces.distress_seed_raw_gated "Tier 0 matched seed rows, review-gated" 1 "$(count_tier source_outerspaces.distress_seed_raw_gated tier0)" "private_review_gated"

copy_query source_outerspaces.distress_seed_raw_gated "$DISTRESS_RAW_COLS" "$TIER1_CTE SELECT 'tier1',$DISTRESS_RAW_SELECT,'$LOAD_RUN_ID' FROM (SELECT r.* FROM private.build_vitals_distress_seed_raw r JOIN private.build_vitals_distress_seed_match m USING (seed_id) JOIN tier1_buildings t USING (building_id)) r"
record_manifest tier1 private.build_vitals_distress_seed_raw source_outerspaces.distress_seed_raw_gated "Tier 1 matched seed rows, review-gated" 5 "$(count_tier source_outerspaces.distress_seed_raw_gated tier1)" "private_review_gated"

export PGPASSWORD="$RECAST_DB_PASSWORD"
"${LOCAL_PSQL[@]}" -v load_run_id="$LOAD_RUN_ID" <<'SQL'
INSERT INTO recast.building (
  building_id, source_parcel_id, address, city, state, asset_class, source_use_description,
  gross_sf, stories, year_built, owner_proxy, zoning, latitude, longitude, source_load_run_id
)
SELECT DISTINCT ON (b.building_id)
  b.building_id, b.source_parcel_id, b.address, b.city, b.state, b.asset_class, b.source_use_description,
  b.gross_sf, b.stories, b.year_built, b.owner_proxy, p.zoning,
  COALESCE(b.parcel_centroid_lat, p.latitude), COALESCE(b.parcel_centroid_lon, p.longitude),
  :'load_run_id'
FROM source_outerspaces.building_profile_with_coords_subset b
LEFT JOIN source_outerspaces.parcel_subset p USING (source_parcel_id)
ORDER BY b.building_id, CASE b.load_tier WHEN 'tier0' THEN 0 ELSE 1 END;

INSERT INTO recast.building_value_trajectory (
  building_id, source_parcel_id, first_assessment_year, latest_assessment_year,
  first_assessed_value, latest_assessed_value, peak_assessed_value, peak_to_latest_compression_pct,
  source_load_run_id
)
WITH vals AS (
  SELECT DISTINCT ON (b.building_id, v.source_parcel_id, v.assessment_year)
    b.building_id, v.source_parcel_id, v.assessment_year, v.total_assessed_value
  FROM source_outerspaces.assessed_value_history_subset v
  JOIN recast.building b USING (source_parcel_id)
  ORDER BY b.building_id, v.source_parcel_id, v.assessment_year,
    CASE v.load_tier WHEN 'tier0' THEN 0 ELSE 1 END
), agg AS (
  SELECT building_id, source_parcel_id,
    min(assessment_year) AS first_year,
    max(assessment_year) AS latest_year,
    max(total_assessed_value) AS peak_value
  FROM vals GROUP BY building_id, source_parcel_id
)
SELECT a.building_id, a.source_parcel_id, a.first_year, a.latest_year,
  vf.total_assessed_value, vl.total_assessed_value, a.peak_value,
  CASE WHEN a.peak_value > 0 THEN ((vl.total_assessed_value - a.peak_value) / a.peak_value) ELSE NULL END,
  :'load_run_id'
FROM agg a
LEFT JOIN vals vf ON vf.building_id=a.building_id AND vf.assessment_year=a.first_year
LEFT JOIN vals vl ON vl.building_id=a.building_id AND vl.assessment_year=a.latest_year;

INSERT INTO recast.building_permit_activity (
  building_id, source_parcel_id, permit_rows, latest_permit_date, permit_value_since_2019, source_load_run_id
)
SELECT b.building_id, b.source_parcel_id, count(p.*)::integer, max(p.issue_date),
  sum(CASE WHEN p.issue_date >= DATE '2019-01-01' THEN coalesce(p.permit_value,0) ELSE 0 END),
  :'load_run_id'
FROM recast.building b
LEFT JOIN (
  SELECT DISTINCT ON (source_parcel_id, permit_number, permit_type, issue_date)
    *
  FROM source_outerspaces.permit_history_subset
  ORDER BY source_parcel_id, permit_number, permit_type, issue_date,
    CASE load_tier WHEN 'tier0' THEN 0 ELSE 1 END
) p USING (source_parcel_id)
GROUP BY b.building_id, b.source_parcel_id;

INSERT INTO recast.building_availability (
  building_id, source_parcel_id, availability_sf, availability_pct, confidence, last_observed, source_load_run_id
)
SELECT DISTINCT ON (b.building_id)
  b.building_id, b.source_parcel_id, a.availability_sf, a.availability_pct, a.confidence, a.last_observed, :'load_run_id'
FROM recast.building b
LEFT JOIN source_outerspaces.availability_signal a USING (building_id)
ORDER BY b.building_id, a.last_observed DESC NULLS LAST;

INSERT INTO recast.building_energy_signal (
  building_id, source_parcel_id, energy_rows, latest_data_year, latest_energy_star_score,
  latest_site_eui_kbtu_sf, latest_compliance_status, source_load_run_id
)
WITH ranked AS (
  SELECT b.building_id, b.source_parcel_id, e.*,
    row_number() OVER (PARTITION BY b.building_id ORDER BY e.data_year DESC NULLS LAST) AS rn,
    count(e.*) OVER (PARTITION BY b.building_id) AS energy_rows
  FROM recast.building b
  LEFT JOIN (
    SELECT DISTINCT ON (tax_parcel_identification_number, ose_building_id, data_year)
      *
    FROM source_outerspaces.seattle_building_energy_benchmarking_subset
    ORDER BY tax_parcel_identification_number, ose_building_id, data_year,
      CASE load_tier WHEN 'tier0' THEN 0 ELSE 1 END
  ) e ON e.tax_parcel_identification_number = b.source_parcel_id
)
SELECT building_id, source_parcel_id, coalesce(energy_rows,0)::integer, data_year, energy_star_score,
  site_eui_kbtu_sf, compliance_status, :'load_run_id'
FROM ranked
WHERE rn = 1 OR rn IS NULL;

INSERT INTO recast.building_signal_snapshot (
  building_id, source_parcel_id, value_compression_pct, availability_pct, permit_rows, energy_rows,
  has_review_gated_private_signal, missing_signal_flags, source_load_run_id
)
SELECT b.building_id, b.source_parcel_id,
  bp.peak_to_current_compression_pct,
  a.availability_pct,
  p.permit_rows,
  e.energy_rows,
  false,
  bp.missing_signal_flags,
  :'load_run_id'
FROM recast.building b
LEFT JOIN (
  SELECT DISTINCT ON (building_id) *
  FROM source_outerspaces.building_profile_with_coords_subset
  ORDER BY building_id, CASE load_tier WHEN 'tier0' THEN 0 ELSE 1 END
) bp USING (building_id)
LEFT JOIN recast.building_availability a USING (building_id)
LEFT JOIN recast.building_permit_activity p USING (building_id)
LEFT JOIN recast.building_energy_signal e USING (building_id);

INSERT INTO recast.building_attention_candidate (
  building_id, source_parcel_id, load_tier, value_compression_flag, availability_flag,
  permit_silence_flag, stale_permit_flag, attention_reason, source_load_run_id
)
SELECT DISTINCT ON (bp.building_id)
  bp.building_id, bp.source_parcel_id, bp.load_tier,
  coalesce(bp.peak_to_current_compression_pct,0) <= -25,
  a.building_id IS NOT NULL,
  coalesce(bp.permit_count,0) = 0,
  bp.latest_permit_date < DATE '2019-01-01',
  array_remove(ARRAY[
    CASE WHEN coalesce(bp.peak_to_current_compression_pct,0) <= -25 THEN 'value_compression' END,
    CASE WHEN a.building_id IS NOT NULL THEN 'availability_signal' END,
    CASE WHEN coalesce(bp.permit_count,0) = 0 THEN 'permit_silence' END,
    CASE WHEN bp.latest_permit_date < DATE '2019-01-01' THEN 'stale_permit_activity' END
  ], NULL),
  :'load_run_id'
FROM source_outerspaces.building_profile_with_coords_subset bp
LEFT JOIN source_outerspaces.availability_signal a USING (building_id)
ORDER BY bp.building_id, CASE bp.load_tier WHEN 'tier0' THEN 0 ELSE 1 END;

INSERT INTO recast.debt_maturity_signal (
  building_id, source_parcel_id, owner_entity, latest_sale_date, latest_sale_price,
  assessed_value_peak, assessed_value_current, value_decline_amount, value_decline_percent,
  available_sf, availability_pct, jll_percent_leased, jll_review_status,
  distress_types, distress_claim_summary, debt_amount_claimed, legal_distress_status,
  debt_maturity_state, evidence_label, evidence_tier, review_gated_claim_present,
  source_refs, next_verification_step, source_load_run_id
)
WITH bp AS (
  SELECT DISTINCT ON (building_id) *
  FROM source_outerspaces.building_profile_with_coords_subset
  ORDER BY building_id, CASE load_tier WHEN 'tier0' THEN 0 ELSE 1 END
), jll AS (
  SELECT DISTINCT ON (m.building_id)
    m.building_id, m.source_parcel_id, m.jll_raw_id, m.jll_match_id,
    r.available_total_sf, r.available_share_of_rba, r.percent_leased,
    r.review_status, m.match_status, m.match_confidence
  FROM source_outerspaces.jll_building_availability_match_gated m
  JOIN source_outerspaces.jll_building_availability_raw_gated r USING (jll_raw_id, load_tier)
  ORDER BY m.building_id, CASE m.load_tier WHEN 'tier0' THEN 0 ELSE 1 END,
    r.available_total_sf DESC NULLS LAST
), distress AS (
  SELECT DISTINCT ON (m.building_id)
    m.building_id, m.source_parcel_id, m.seed_id,
    r.distress_types, r.claim_summary, r.debt_amount_claimed,
    r.evidence_status, r.review_status, r.source_urls
  FROM source_outerspaces.distress_seed_match_gated m
  JOIN source_outerspaces.distress_seed_raw_gated r USING (seed_id, load_tier)
  ORDER BY m.building_id, CASE m.load_tier WHEN 'tier0' THEN 0 ELSE 1 END
)
SELECT b.building_id, b.source_parcel_id, b.owner_proxy, b.latest_sale_date, b.latest_sale_price,
  b.peak_assessed_value, b.current_assessed_value, b.peak_to_current_compression_value,
  b.peak_to_current_compression_pct,
  COALESCE(j.available_total_sf, a.availability_sf),
  COALESCE(j.available_share_of_rba, a.availability_pct),
  j.percent_leased,
  j.review_status,
  d.distress_types,
  d.claim_summary,
  d.debt_amount_claimed,
  CASE WHEN d.seed_id IS NOT NULL THEN 'SOURCE_REVIEW_REQUIRED' ELSE NULL END,
  'INSUFFICIENT_DEBT_EVIDENCE',
  'INSUFFICIENT_EVIDENCE',
  CASE
    WHEN d.seed_id IS NOT NULL OR j.jll_raw_id IS NOT NULL THEN 'REVIEW_GATED_SOURCE_PRESENT'
    ELSE 'NO_DEBT_SOURCE_REVIEWED'
  END,
  (d.seed_id IS NOT NULL OR j.jll_raw_id IS NOT NULL),
  array_remove(ARRAY[
    CASE WHEN j.jll_raw_id IS NOT NULL THEN 'jll_raw:' || j.jll_raw_id::text END,
    CASE WHEN j.jll_match_id IS NOT NULL THEN 'jll_match:' || j.jll_match_id::text END,
    CASE WHEN d.seed_id IS NOT NULL THEN 'distress_seed:' || d.seed_id::text END
  ], NULL),
  CASE
    WHEN d.seed_id IS NOT NULL THEN 'Review distress seed source URLs and verify against recorder/court/licensed debt sources before scoring.'
    WHEN j.jll_raw_id IS NOT NULL THEN 'Use JLL availability as prioritization; run recorder/court debt maturity workflow.'
    ELSE 'Run recorder/court debt maturity workflow if this building becomes a debt-priority candidate.'
  END,
  :'load_run_id'
FROM bp b
LEFT JOIN jll j USING (building_id)
LEFT JOIN distress d USING (building_id)
LEFT JOIN recast.building_availability a USING (building_id);

UPDATE recast.building_signal_snapshot s
SET has_review_gated_private_signal = d.review_gated_claim_present
FROM recast.debt_maturity_signal d
WHERE s.building_id = d.building_id;

UPDATE meta.load_run
SET completed_at = now(), status = 'loaded'
WHERE load_run_id = :'load_run_id';
SQL

echo "$LOAD_RUN_ID"
