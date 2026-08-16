"""Adapter connecting build_vitals_v2.py's economic/usage inputs to the real
recast Postgres database (~/recast on gn100), when it's available.

Reads DB connection details from the same env file the team's own load
script (~/recast/scripts/load-local-recast.sh) already uses:
  ~/.config/recast/local-postgres.env
Never hardcoded, never printed, never committed -- this module only works
when run on a machine that already has that file.

Falls back to None everywhere the DB, a table, or a specific building's row
isn't reachable -- never a fabricated value. Caller decides what T0 looks
like when this returns None.
"""
import os

ENV_PATH = os.path.expanduser("~/.config/recast/local-postgres.env")


def _load_env():
    if not os.path.exists(ENV_PATH):
        return None
    env = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _connect():
    env = _load_env()
    if not env:
        return None
    try:
        import psycopg2
    except ImportError:
        return None
    try:
        return psycopg2.connect(
            host=env.get("RECAST_DB_HOST"), port=env.get("RECAST_DB_PORT"),
            user=env.get("RECAST_DB_USER"), password=env.get("RECAST_DB_PASSWORD"),
            dbname=env.get("RECAST_DB_NAME"), connect_timeout=5,
        )
    except Exception:
        return None


def fetch_building_record(building_id):
    """Real fields for one building from the recast DB, or None if
    unreachable/not found. Caller is responsible for deciding which fields
    are trustworthy enough to score -- some columns in this schema are
    known-incomplete for specific buildings (see build_vitals_v2.py's notes
    on permit_value_since_2019)."""
    conn = _connect()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.latest_assessment_year, t.latest_assessed_value,
                       t.peak_assessed_value, t.peak_to_latest_compression_pct,
                       p.permit_rows, p.permit_value_since_2019,
                       a.availability_pct, a.confidence,
                       e.latest_data_year, e.latest_energy_star_score,
                       e.latest_site_eui_kbtu_sf, e.latest_compliance_status
                FROM recast.building b
                LEFT JOIN recast.building_value_trajectory t ON t.building_id = b.building_id
                LEFT JOIN recast.building_permit_activity p ON p.building_id = b.building_id
                LEFT JOIN recast.building_availability a ON a.building_id = b.building_id
                LEFT JOIN recast.building_energy_signal e ON e.building_id = b.building_id
                WHERE b.building_id = %s
            """, (building_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = ["latest_assessment_year", "latest_assessed_value", "peak_assessed_value",
                    "peak_to_latest_compression_pct", "permit_rows", "permit_value_since_2019",
                    "availability_pct", "availability_confidence",
                    "energy_data_year", "energy_star_score", "site_eui_kbtu_sf", "compliance_status"]
            return dict(zip(cols, row))
    except Exception:
        return None
    finally:
        conn.close()


if __name__ == "__main__":
    import sys, json
    bid = sys.argv[1] if len(sys.argv) > 1 else "king_county_wa:4088803750:profile"
    print(json.dumps(fetch_building_record(bid), indent=2, default=str))
