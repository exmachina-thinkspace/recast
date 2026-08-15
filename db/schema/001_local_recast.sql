-- Local Recast PostgreSQL schema.
-- Target database: recast

CREATE SCHEMA IF NOT EXISTS source_outerspaces;
CREATE SCHEMA IF NOT EXISTS recast;
CREATE SCHEMA IF NOT EXISTS vss;
CREATE SCHEMA IF NOT EXISTS capital;
CREATE SCHEMA IF NOT EXISTS meta;

CREATE TABLE IF NOT EXISTS meta.load_run (
  load_run_id text PRIMARY KEY,
  source_system text NOT NULL,
  tier_scope text NOT NULL,
  transfer_mechanism text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  status text NOT NULL DEFAULT 'running',
  notes text
);

CREATE TABLE IF NOT EXISTS meta.source_export_manifest (
  manifest_id bigserial PRIMARY KEY,
  load_run_id text REFERENCES meta.load_run(load_run_id),
  tier text NOT NULL,
  source_relation text NOT NULL,
  destination_relation text NOT NULL,
  source_filter text NOT NULL,
  expected_row_count integer,
  actual_row_count integer,
  source_system text NOT NULL DEFAULT 'outerspaces_supabase',
  privacy_posture text NOT NULL DEFAULT 'public_or_derived',
  validation_status text NOT NULL DEFAULT 'pending',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meta.source_relation_snapshot (
  snapshot_id bigserial PRIMARY KEY,
  load_run_id text REFERENCES meta.load_run(load_run_id),
  source_relation text NOT NULL,
  destination_relation text NOT NULL,
  tier text NOT NULL,
  expected_row_count integer,
  actual_row_count integer,
  captured_at timestamptz NOT NULL DEFAULT now(),
  notes text
);

CREATE TABLE IF NOT EXISTS meta.row_count_check (
  check_id bigserial PRIMARY KEY,
  load_run_id text REFERENCES meta.load_run(load_run_id),
  tier text NOT NULL,
  relation_name text NOT NULL,
  expected_count integer,
  actual_count integer,
  status text NOT NULL,
  checked_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meta.validation_issue (
  issue_id bigserial PRIMARY KEY,
  load_run_id text REFERENCES meta.load_run(load_run_id),
  severity text NOT NULL,
  scope text NOT NULL,
  message text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz
);

CREATE TABLE IF NOT EXISTS source_outerspaces.market (
  market_id text,
  market_name text,
  state text,
  country text,
  source_jurisdiction text,
  primary_source_system text,
  active boolean,
  created_at timestamptz,
  updated_at timestamptz,
  load_run_id text
);

CREATE TABLE IF NOT EXISTS source_outerspaces.asset_class_taxonomy (
  taxonomy_id bigint,
  source_system text,
  source_jurisdiction text,
  source_use_code text,
  source_use_description text,
  asset_class text,
  market_asset_flag boolean,
  non_market_flag boolean,
  artifact_risk_flag boolean,
  notes text,
  reviewed boolean,
  reviewed_by text,
  reviewed_at timestamptz,
  created_at timestamptz,
  updated_at timestamptz,
  load_run_id text
);

CREATE TABLE IF NOT EXISTS source_outerspaces.building_profile_with_coords_subset (
  load_tier text NOT NULL,
  market_id text,
  market_name text,
  city text,
  state text,
  source_jurisdiction text,
  source_parcel_id text,
  building_id text,
  address text,
  asset_class text,
  source_use_code text,
  source_use_description text,
  gross_sf numeric,
  net_sf numeric,
  stories numeric,
  year_built integer,
  building_age integer,
  effective_year integer,
  owner_proxy text,
  latest_sale_date date,
  latest_sale_price numeric,
  sale_instrument text,
  sale_reason text,
  property_class text,
  current_assessment_year integer,
  current_assessed_value numeric,
  peak_assessment_year integer,
  peak_assessed_value numeric,
  peak_to_current_compression_pct numeric,
  peak_to_current_compression_value numeric,
  purchase_basis_compression_pct numeric,
  permit_count bigint,
  latest_permit_date date,
  permit_value_since_2019 numeric,
  supported_signals text[],
  missing_signal_flags text[],
  artifact_risk_flags text[],
  signal_confidence text,
  parcel_centroid_lat numeric,
  parcel_centroid_lon numeric,
  updated_at timestamptz,
  load_run_id text
);

CREATE TABLE IF NOT EXISTS source_outerspaces.parcel_subset (
  load_tier text NOT NULL,
  market_id text,
  source_jurisdiction text,
  source_parcel_id text,
  normalized_parcel_id text,
  major text,
  minor text,
  address text,
  city text,
  state text,
  zip text,
  latitude numeric,
  longitude numeric,
  lot_sqft numeric,
  zoning text,
  present_use_raw text,
  source_table text,
  source_updated_at timestamptz,
  load_run_id text
);

CREATE TABLE IF NOT EXISTS source_outerspaces.kingcounty_raw_parcel_subset (
  load_tier text NOT NULL,
  parcel_id text,
  major text,
  minor text,
  prop_name text,
  prop_type text,
  current_zoning text,
  hbu_as_if_vacant text,
  hbu_as_improved text,
  present_use text,
  sq_ft_lot bigint,
  water_system text,
  sewer_system text,
  access text,
  topography text,
  street_surface text,
  inadequate_parking text,
  unbuildable text,
  traffic_noise text,
  contamination text,
  historic_site text,
  current_use_designation text,
  latitude numeric,
  longitude numeric,
  imported_at timestamptz,
  load_run_id text
);

CREATE TABLE IF NOT EXISTS source_outerspaces.assessed_value_history_subset (
  load_tier text NOT NULL,
  market_id text,
  source_parcel_id text,
  assessment_year integer,
  land_value numeric,
  improvement_value numeric,
  total_assessed_value numeric,
  value_reason text,
  source_table text,
  source_updated_at timestamptz,
  load_run_id text
);

CREATE TABLE IF NOT EXISTS source_outerspaces.permit_history_subset (
  load_tier text NOT NULL,
  market_id text,
  source_parcel_id text,
  permit_number text,
  permit_type text,
  permit_status text,
  issue_date date,
  permit_value numeric,
  percent_complete numeric,
  description text,
  source_table text,
  source_updated_at timestamptz,
  load_run_id text
);

CREATE TABLE IF NOT EXISTS source_outerspaces.availability_signal (
  load_tier text NOT NULL,
  availability_signal_id text,
  building_id text,
  market_id text,
  source_parcel_id text,
  address text,
  asset_class text,
  gross_sf numeric,
  owner_proxy text,
  availability_sf numeric,
  direct_available_sf numeric,
  sublease_available_sf numeric,
  availability_pct numeric,
  source text,
  source_type text,
  source_url text,
  collection_method text,
  last_observed date,
  confidence text,
  coverage_status text,
  match_confidence text,
  notes text,
  current_assessed_value numeric,
  peak_assessed_value numeric,
  peak_to_current_compression_pct numeric,
  updated_at timestamptz,
  load_run_id text
);

CREATE TABLE IF NOT EXISTS source_outerspaces.seattle_building_energy_benchmarking_subset (
  load_tier text NOT NULL,
  ose_building_id text,
  data_year text,
  building_name text,
  building_type text,
  tax_parcel_identification_number text,
  address text,
  latitude text,
  longitude text,
  year_built text,
  number_of_floors text,
  property_gfa_total text,
  energy_star_score text,
  site_eui_wn_kbtu_sf text,
  site_eui_kbtu_sf text,
  site_energy_use_kbtu text,
  source_eui_kbtu_sf text,
  electricity_kwh text,
  natural_gas_therms text,
  compliance_status text,
  compliance_issue text,
  total_ghg_emissions text,
  ghg_emissions_intensity text,
  jurisdiction text,
  loaded_at timestamptz,
  load_run_id text
);

CREATE TABLE IF NOT EXISTS recast.building (
  building_id text PRIMARY KEY,
  source_parcel_id text,
  address text,
  city text,
  state text,
  asset_class text,
  source_use_description text,
  gross_sf numeric,
  stories numeric,
  year_built integer,
  owner_proxy text,
  zoning text,
  latitude numeric,
  longitude numeric,
  source_load_run_id text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recast.building_value_trajectory (
  building_id text,
  source_parcel_id text,
  first_assessment_year integer,
  latest_assessment_year integer,
  first_assessed_value numeric,
  latest_assessed_value numeric,
  peak_assessed_value numeric,
  peak_to_latest_compression_pct numeric,
  source_load_run_id text,
  PRIMARY KEY (building_id)
);

CREATE TABLE IF NOT EXISTS recast.building_permit_activity (
  building_id text PRIMARY KEY,
  source_parcel_id text,
  permit_rows integer,
  latest_permit_date date,
  permit_value_since_2019 numeric,
  source_load_run_id text
);

CREATE TABLE IF NOT EXISTS recast.building_availability (
  building_id text PRIMARY KEY,
  source_parcel_id text,
  availability_sf numeric,
  availability_pct numeric,
  confidence text,
  last_observed date,
  source_load_run_id text
);

CREATE TABLE IF NOT EXISTS recast.building_energy_signal (
  building_id text PRIMARY KEY,
  source_parcel_id text,
  energy_rows integer,
  latest_data_year text,
  latest_energy_star_score text,
  latest_site_eui_kbtu_sf text,
  latest_compliance_status text,
  source_load_run_id text
);

CREATE TABLE IF NOT EXISTS recast.building_signal_snapshot (
  building_id text PRIMARY KEY,
  source_parcel_id text,
  as_is_evidence_state text NOT NULL DEFAULT 'KNOWN',
  value_compression_pct numeric,
  availability_pct numeric,
  permit_rows integer,
  energy_rows integer,
  has_review_gated_private_signal boolean NOT NULL DEFAULT false,
  missing_signal_flags text[],
  source_load_run_id text,
  generated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recast.building_attention_candidate (
  building_id text PRIMARY KEY,
  source_parcel_id text,
  load_tier text,
  value_compression_flag boolean,
  availability_flag boolean,
  permit_silence_flag boolean,
  stale_permit_flag boolean,
  attention_reason text[],
  source_load_run_id text,
  generated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recast.building_recast_assessment (
  assessment_id bigserial PRIMARY KEY,
  building_id text,
  assessment_state text,
  trajectory_state text,
  recommendation text,
  evidence_label text,
  source_load_run_id text,
  generated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recast.building_recast_option (
  option_id bigserial PRIMARY KEY,
  assessment_id bigint REFERENCES recast.building_recast_assessment(assessment_id),
  building_id text,
  candidate_use text,
  outcome text,
  rank integer,
  evidence_label text,
  notes text
);

CREATE TABLE IF NOT EXISTS vss.capture_session (
  capture_session_id text PRIMARY KEY,
  building_id text,
  source_type text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vss.video_source (
  video_source_id text PRIMARY KEY,
  capture_session_id text,
  building_id text,
  source_label text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vss.vss_prompt (
  prompt_id bigserial PRIMARY KEY,
  building_id text,
  prompt text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vss.vss_observation (
  observation_id bigserial PRIMARY KEY,
  building_id text,
  prompt_id bigint,
  evidence_label text,
  claim text,
  confidence numeric,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vss.vss_clip (
  clip_id text PRIMARY KEY,
  building_id text,
  timestamp_start text,
  timestamp_end text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vss.evidence_claim (
  evidence_claim_id bigserial PRIMARY KEY,
  building_id text,
  source_type text,
  evidence_label text,
  claim text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS capital.program (
  program_id text PRIMARY KEY,
  program_name text NOT NULL,
  agency text,
  government_level text,
  support_type text,
  source_url text,
  confidence text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS capital.program_requirement (
  requirement_id bigserial PRIMARY KEY,
  program_id text REFERENCES capital.program(program_id),
  requirement_text text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS capital.program_fit (
  program_fit_id bigserial PRIMARY KEY,
  program_id text REFERENCES capital.program(program_id),
  building_id text,
  candidate_use text,
  fit_status text,
  evidence_label text,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS capital.capital_stack_option (
  capital_stack_option_id bigserial PRIMARY KEY,
  building_id text,
  candidate_use text,
  program_id text,
  role_in_stack text,
  fit_status text,
  created_at timestamptz NOT NULL DEFAULT now()
);
