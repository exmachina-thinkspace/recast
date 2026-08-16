"""Validates events against packages/contracts/sensor_observation.schema.json
without adding a jsonschema dependency to the Spark's already-tight venv.
Hand-rolled on purpose -- keep this in sync with the schema file by hand.
"""
import re
from datetime import datetime, timezone

EVENT_ID_RE = re.compile(r"^ev_[a-zA-Z0-9_-]+$")
BUILDING_ID_RE = re.compile(r"^BV_SEA_[A-Z0-9_]+$")
SOURCE_RE = re.compile(r"^camera:[a-z0-9-]+$")

EVENT_TYPES = {"zone_occupancy", "entry_count", "exit_count", "line_crossing", "activity_summary"}
UNITS = {"people", "count", "events_per_min"}
TIERS = {"T1", "T2", "T3", "T0"}


class ContractError(ValueError):
    pass


def _require(cond, msg):
    if not cond:
        raise ContractError(msg)


def validate(event: dict) -> None:
    required = ["event_id", "building_id", "event_type", "value", "unit",
                "evidence_tier", "confidence", "source", "observed_at", "expires_at"]
    for field in required:
        _require(field in event, f"missing required field: {field}")

    _require(EVENT_ID_RE.match(event["event_id"]), f"bad event_id: {event['event_id']}")
    _require(BUILDING_ID_RE.match(event["building_id"]), f"bad building_id: {event['building_id']}")
    _require(event["event_type"] in EVENT_TYPES, f"bad event_type: {event['event_type']}")
    _require(isinstance(event["value"], (int, float)), "value must be numeric")
    _require(event["unit"] in UNITS, f"bad unit: {event['unit']}")
    _require(event["evidence_tier"] in TIERS, f"bad evidence_tier: {event['evidence_tier']}")
    _require(0 <= event["confidence"] <= 1, "confidence must be in [0, 1]")
    _require(SOURCE_RE.match(event["source"]), f"bad source: {event['source']}")

    for ts_field in ("observed_at", "expires_at"):
        try:
            datetime.fromisoformat(event[ts_field])
        except ValueError:
            raise ContractError(f"bad ISO8601 timestamp in {ts_field}: {event[ts_field]}")

    if "space_id" in event and event["space_id"] is not None:
        _require(isinstance(event["space_id"], str), "space_id must be string or null")


def make_event(*, event_id, building_id, event_type, value, unit, evidence_tier,
                confidence, source, space_id=None, freshness_seconds=600):
    now = datetime.now(timezone.utc).astimezone()
    expires = now.timestamp() + freshness_seconds
    event = {
        "event_id": event_id,
        "building_id": building_id,
        "space_id": space_id,
        "event_type": event_type,
        "value": value,
        "unit": unit,
        "evidence_tier": evidence_tier,
        "confidence": confidence,
        "source": source,
        "observed_at": now.isoformat(timespec="seconds"),
        "expires_at": datetime.fromtimestamp(expires, tz=now.tzinfo).isoformat(timespec="seconds"),
    }
    validate(event)
    return event
