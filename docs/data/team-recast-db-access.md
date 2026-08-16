# Team Recast Database Access

The Recast demo database is running locally on the Acer GN100 in PostgreSQL.

Peter will share the password separately. Do not put the password in Git, Slack, docs, screenshots, or issue comments.

## Connection Info

| Field | Value |
| --- | --- |
| Database | `recast` |
| Username | `recast_readonly` |
| Password | Ask Peter |
| PostgreSQL port on GB100 | `5432` |
| Access level | Read-only |

The read-only role can query current demo data but cannot write to the database.

## If You Are Working Directly On The GB100

Use localhost:

```bash
psql \
  -h 127.0.0.1 \
  -p 5432 \
  -U recast_readonly \
  -d recast
```

For a local app running on the GB100, use:

```bash
DATABASE_URL=postgresql://recast_readonly:<password-from-peter>@127.0.0.1:5432/recast
```

Store that value in a local `.env` file or secret manager outside Git.

## If You Are Connecting From Your Laptop

Do not expose PostgreSQL publicly. Use an SSH tunnel to the GB100:

```bash
ssh -L 15432:127.0.0.1:5432 acer01@gn100-3315.local
```

Leave that SSH session open. In another terminal, connect through the tunnel:

```bash
psql \
  -h 127.0.0.1 \
  -p 15432 \
  -U recast_readonly \
  -d recast
```

For a local app running on your laptop through the tunnel:

```bash
DATABASE_URL=postgresql://recast_readonly:<password-from-peter>@127.0.0.1:15432/recast
```

## Quick Smoke Test

After connecting, run:

```sql
SELECT current_user, current_database();

SELECT count(*) AS buildings
FROM recast.building;

SELECT building_id, address, asset_class, gross_sf
FROM recast.building
ORDER BY address
LIMIT 5;
```

Expected current result:

```text
current_user = recast_readonly
current_database = recast
buildings = 69
```

## What To Query

Use `recast.*` for app/product data:

- `recast.building`
- `recast.building_signal_snapshot`
- `recast.building_attention_candidate`
- `recast.building_value_trajectory`
- `recast.building_permit_activity`
- `recast.building_availability`
- `recast.building_energy_signal`

Use `source_outerspaces.*` only when the UI or analysis needs underlying evidence:

- `source_outerspaces.building_profile_with_coords_subset`
- `source_outerspaces.parcel_subset`
- `source_outerspaces.kingcounty_raw_parcel_subset`
- `source_outerspaces.assessed_value_history_subset`
- `source_outerspaces.permit_history_subset`
- `source_outerspaces.availability_signal`
- `source_outerspaces.seattle_building_energy_benchmarking_subset`

`vss.*` and `capital.*` schemas exist, but they are intentionally empty until real VSS observations and capital-stack analyses are produced.

## Important Rules

- Do not commit credentials.
- Do not write directly to the database with the shared read-only user.
- Do not copy the full Outerspaces database locally.
- Do not promote, redistribute, or show private JLL/distress seed data as verified fact unless Peter explicitly approves the licensing/review posture.
- Keep the distinction clear:
  - `source_outerspaces` = what the source data says.
  - `recast` = what Recast derives from that evidence.
