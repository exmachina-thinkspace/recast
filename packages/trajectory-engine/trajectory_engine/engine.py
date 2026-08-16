"""Deterministic, evidence-gated building trajectory calculations.

This module intentionally has no network, database, service, or AI dependencies.
It accepts reviewed inputs and explicit assumptions, then produces auditable
12/24/36-month scenarios. Missing evidence remains missing instead of being
silently replaced with invented values.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any


USABLE_EVIDENCE = {"KNOWN", "OBSERVED", "INFERRED"}
ALL_EVIDENCE = USABLE_EVIDENCE | {"UNKNOWN", "INSUFFICIENT_EVIDENCE"}
SCENARIO_NAMES = ("improving", "base", "adverse")
RENEWAL_CASES = {"low", "mid", "high"}
DEBT_SIGNAL_STATES = {
    "MATURITY_KNOWN_NEAR_TERM",
    "MATURITY_INFERRED_NEAR_TERM",
    "REFI_OR_EXTENSION_FOUND",
    "POSSIBLY_PAID_OFF",
    "LEGAL_DISTRESS_ACTIVE",
    "FORECLOSURE_OR_LENDER_ACTION",
    "RECEIVERSHIP_REQUESTED",
    "RECEIVER_APPOINTED",
    "INSUFFICIENT_DEBT_EVIDENCE",
    "NO_SIGNAL_FOUND",
}


class InputError(ValueError):
    """Raised when the input contract is structurally invalid."""


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _parse_date(value: Any, path: str) -> date:
    if not isinstance(value, str):
        raise InputError(f"{path} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InputError(f"{path} must use YYYY-MM-DD") from exc


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _fact_value(fact: Any) -> Any:
    if not isinstance(fact, dict):
        return None
    if fact.get("evidence_label") not in USABLE_EVIDENCE:
        return None
    return fact.get("value")


def _fact_label(fact: Any) -> str:
    if not isinstance(fact, dict):
        return "INSUFFICIENT_EVIDENCE"
    label = fact.get("evidence_label", "INSUFFICIENT_EVIDENCE")
    return label if label in ALL_EVIDENCE else "INSUFFICIENT_EVIDENCE"


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InputError(f"{path} must be an object")
    return value


def _require_number(value: Any, path: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{path} must be a number")
    number = float(value)
    if minimum is not None and number < minimum:
        raise InputError(f"{path} must be at least {minimum}")
    return number


def _validate_fact(fact: Any, path: str, required: bool = False) -> None:
    if fact is None and not required:
        return
    mapping = _require_mapping(fact, path)
    if "value" not in mapping:
        raise InputError(f"{path}.value is required (use null when unknown)")
    label = mapping.get("evidence_label")
    if label not in ALL_EVIDENCE:
        raise InputError(f"{path}.evidence_label must be one of {sorted(ALL_EVIDENCE)}")
    if label in USABLE_EVIDENCE and mapping.get("value") is None:
        raise InputError(f"{path}.value is required when the evidence is usable")
    if label in USABLE_EVIDENCE and not mapping.get("source_ref"):
        raise InputError(f"{path}.source_ref is required when the evidence is usable")


def _validate_input(payload: dict[str, Any]) -> None:
    _require_mapping(payload, "input")
    if payload.get("schema_version") != "1.0":
        raise InputError("schema_version must be '1.0'")
    if "demo_only" in payload and not isinstance(payload["demo_only"], bool):
        raise InputError("demo_only must be a boolean")
    _parse_date(payload.get("as_of_date"), "as_of_date")

    building = _require_mapping(payload.get("building"), "building")
    if not building.get("building_id"):
        raise InputError("building.building_id is required")
    for key in ("current_use", "gross_area_sf", "market_value"):
        _validate_fact(building.get(key), f"building.{key}", required=True)
    for key in ("address", "acquisition_date", "acquisition_price"):
        _validate_fact(building.get(key), f"building.{key}")
    address = _fact_value(building.get("address"))
    if address is not None and not isinstance(address, str):
        raise InputError("building.address.value must be a string")
    current_use = _fact_value(building.get("current_use"))
    if current_use is not None and not isinstance(current_use, str):
        raise InputError("building.current_use.value must be a string")
    for key, minimum in (("gross_area_sf", 1), ("market_value", 0), ("acquisition_price", 0)):
        value = _fact_value(building.get(key))
        if value is not None:
            _require_number(value, f"building.{key}.value", minimum)
    acquisition_date = _fact_value(building.get("acquisition_date"))
    if acquisition_date is not None:
        _parse_date(acquisition_date, "building.acquisition_date.value")

    bhi = _require_mapping(payload.get("bhi"), "bhi")
    _validate_fact(bhi.get("score"), "bhi.score", required=True)
    _validate_fact(bhi.get("evidence_coverage"), "bhi.evidence_coverage", required=True)
    score = _fact_value(bhi.get("score"))
    if score is not None:
        score = _require_number(score, "bhi.score.value", 0)
        if score > 100:
            raise InputError("bhi.score.value must not exceed 100")
    coverage = _fact_value(bhi.get("evidence_coverage"))
    if coverage is not None:
        coverage = _require_number(coverage, "bhi.evidence_coverage.value", 0)
        if coverage > 1:
            raise InputError("bhi.evidence_coverage.value must not exceed 1")

    operations = _require_mapping(payload.get("operations"), "operations")
    for key in (
        "annual_gross_revenue",
        "annual_operating_expenses",
        "annual_required_capex",
        "cash_reserves",
    ):
        _validate_fact(operations.get(key), f"operations.{key}", required=True)
        value = _fact_value(operations.get(key))
        if value is not None:
            _require_number(value, f"operations.{key}.value", 0)

    debts = payload.get("debt")
    if not isinstance(debts, list):
        raise InputError("debt must be an array")
    for index, loan in enumerate(debts):
        loan = _require_mapping(loan, f"debt[{index}]")
        if not loan.get("loan_id"):
            raise InputError(f"debt[{index}].loan_id is required")
        for key in ("current_balance", "maturity_date", "annual_debt_service"):
            _validate_fact(loan.get(key), f"debt[{index}].{key}", required=True)
        for key in ("original_principal", "current_all_in_rate_pct", "debt_signal_state"):
            _validate_fact(loan.get(key), f"debt[{index}].{key}")
        for key in (
            "original_principal",
            "current_balance",
            "annual_debt_service",
            "current_all_in_rate_pct",
        ):
            value = _fact_value(loan.get(key))
            if value is not None:
                _require_number(value, f"debt[{index}].{key}.value", 0)
        maturity = _fact_value(loan.get("maturity_date"))
        if maturity is not None:
            _parse_date(maturity, f"debt[{index}].maturity_date.value")
        debt_state = _fact_value(loan.get("debt_signal_state"))
        if debt_state is not None and debt_state not in DEBT_SIGNAL_STATES:
            raise InputError(
                f"debt[{index}].debt_signal_state.value must be an approved Recast debt state"
            )

    leases = payload.get("leases")
    if not isinstance(leases, list):
        raise InputError("leases must be an array")
    for index, lease in enumerate(leases):
        lease = _require_mapping(lease, f"leases[{index}]")
        if not lease.get("tenant_id"):
            raise InputError(f"leases[{index}].tenant_id is required")
        for key in ("tenant_name", "leased_area_sf", "annual_base_rent", "lease_end"):
            _validate_fact(lease.get(key), f"leases[{index}].{key}", required=True)
        for key in ("business_health", "ai_space_demand_exposure"):
            _validate_fact(lease.get(key), f"leases[{index}].{key}")
        tenant_name = _fact_value(lease.get("tenant_name"))
        if tenant_name is not None and not isinstance(tenant_name, str):
            raise InputError(f"leases[{index}].tenant_name.value must be a string")
        _validate_fact(lease.get("industry"), f"leases[{index}].industry")
        industry = _fact_value(lease.get("industry"))
        if industry is not None and not isinstance(industry, str):
            raise InputError(f"leases[{index}].industry.value must be a string")
        events = lease.get("public_distress_events", [])
        if not isinstance(events, list):
            raise InputError(f"leases[{index}].public_distress_events must be an array")
        for event_index, event in enumerate(events):
            _validate_fact(
                event,
                f"leases[{index}].public_distress_events[{event_index}]",
                required=True,
            )
        for key in ("leased_area_sf", "annual_base_rent"):
            value = _fact_value(lease.get(key))
            if value is not None:
                _require_number(value, f"leases[{index}].{key}.value", 0)
        lease_end = _fact_value(lease.get("lease_end"))
        if lease_end is not None:
            _parse_date(lease_end, f"leases[{index}].lease_end.value")
        business_health = _fact_value(lease.get("business_health"))
        if business_health is not None and str(business_health).lower() not in {
            "strong",
            "stable",
            "watch",
            "distressed",
            "unknown",
        }:
            raise InputError(
                f"leases[{index}].business_health.value must be strong, stable, watch, distressed, or unknown"
            )
        ai_exposure = _fact_value(lease.get("ai_space_demand_exposure"))
        if ai_exposure is not None and str(ai_exposure).lower() not in {
            "low",
            "medium",
            "high",
            "unknown",
        }:
            raise InputError(
                f"leases[{index}].ai_space_demand_exposure.value must be low, medium, high, or unknown"
            )
        renewal = lease.get("renewal_probability_range")
        _validate_fact(renewal, f"leases[{index}].renewal_probability_range")
        renewal_value = _fact_value(renewal)
        if renewal_value is not None:
            renewal_value = _require_mapping(
                renewal_value, f"leases[{index}].renewal_probability_range.value"
            )
            low = _require_number(
                renewal_value.get("low"),
                f"leases[{index}].renewal_probability_range.value.low",
                0,
            )
            high = _require_number(
                renewal_value.get("high"),
                f"leases[{index}].renewal_probability_range.value.high",
                0,
            )
            if low > 1 or high > 1 or low > high:
                raise InputError(
                    f"leases[{index}] renewal range must satisfy 0 <= low <= high <= 1"
                )

    assumptions = _require_mapping(payload.get("scenario_assumptions"), "scenario_assumptions")
    horizons = assumptions.get("horizons_months")
    if not isinstance(horizons, list) or not horizons:
        raise InputError("scenario_assumptions.horizons_months must be a non-empty array")
    for index, horizon in enumerate(horizons):
        number = _require_number(horizon, f"scenario_assumptions.horizons_months[{index}]", 1)
        if not number.is_integer():
            raise InputError("scenario horizons must be whole months")

    scenarios = _require_mapping(assumptions.get("scenarios"), "scenario_assumptions.scenarios")
    for name in SCENARIO_NAMES:
        scenario = _require_mapping(scenarios.get(name), f"scenario_assumptions.scenarios.{name}")
        for key in (
            "annual_rent_growth_pct",
            "annual_expense_growth_pct",
            "annual_value_change_pct",
            "refinance_interest_rate_pct",
            "existing_debt_service_change_pct",
            "annual_bhi_change_points",
        ):
            value = _require_number(
                scenario.get(key), f"scenario_assumptions.scenarios.{name}.{key}"
            )
            if key in {
                "annual_rent_growth_pct",
                "annual_expense_growth_pct",
                "annual_value_change_pct",
                "existing_debt_service_change_pct",
            } and value <= -100:
                raise InputError(
                    f"scenario_assumptions.scenarios.{name}.{key} must be greater than -100"
                )
            if key == "refinance_interest_rate_pct" and value < 0:
                raise InputError(
                    f"scenario_assumptions.scenarios.{name}.{key} must not be negative"
                )
        if scenario.get("renewal_case") not in RENEWAL_CASES:
            raise InputError(
                f"scenario_assumptions.scenarios.{name}.renewal_case must be low, mid, or high"
            )

    underwriting = _require_mapping(
        assumptions.get("underwriting"), "scenario_assumptions.underwriting"
    )
    for key in (
        "max_ltv",
        "minimum_dscr",
        "dscr_watch",
        "lease_rollover_warning_pct",
        "tenant_concentration_warning_pct",
        "refinance_gap_critical_pct",
    ):
        value = _require_number(
            underwriting.get(key), f"scenario_assumptions.underwriting.{key}", 0
        )
        if key in {
            "max_ltv",
            "lease_rollover_warning_pct",
            "tenant_concentration_warning_pct",
            "refinance_gap_critical_pct",
        } and value > 1:
            raise InputError(f"scenario_assumptions.underwriting.{key} must not exceed 1")
    if underwriting["minimum_dscr"] <= 0:
        raise InputError("scenario_assumptions.underwriting.minimum_dscr must be greater than 0")
    if underwriting["dscr_watch"] < underwriting["minimum_dscr"]:
        raise InputError(
            "scenario_assumptions.underwriting.dscr_watch must be at least minimum_dscr"
        )

    reuse_candidates = payload.get("reuse_candidates")
    if not isinstance(reuse_candidates, list):
        raise InputError("reuse_candidates must be an array")
    for index, candidate in enumerate(reuse_candidates):
        candidate = _require_mapping(candidate, f"reuse_candidates[{index}]")
        if not candidate.get("candidate_use"):
            raise InputError(f"reuse_candidates[{index}].candidate_use is required")
        for fit_name in ("physical", "regulatory", "market", "financial"):
            fact = candidate.get(f"{fit_name}_fit")
            _validate_fact(
                fact,
                f"reuse_candidates[{index}].{fit_name}_fit",
                required=True,
            )
            value = _fact_value(fact)
            if value is not None and str(value).lower() not in {
                "pass",
                "conditional",
                "fail",
                "unknown",
            }:
                raise InputError(
                    f"reuse_candidates[{index}].{fit_name}_fit.value must be pass, conditional, fail, or unknown"
                )


def _active_leases(payload: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for lease in payload.get("leases", []):
        end_value = _fact_value(lease.get("lease_end"))
        if end_value is None:
            continue
        if _parse_date(end_value, f"lease {lease.get('tenant_id')} lease_end") >= as_of:
            active.append(lease)
    return active


def _tenant_metrics(
    leases: list[dict[str, Any]], gross_area: float | None, as_of: date
) -> dict[str, Any]:
    rows: list[tuple[dict[str, Any], float, date]] = []
    for lease in leases:
        area = _fact_value(lease.get("leased_area_sf"))
        end = _fact_value(lease.get("lease_end"))
        if isinstance(area, (int, float)) and end is not None:
            rows.append((lease, float(area), _parse_date(end, "lease_end")))

    occupied = sum(row[1] for row in rows)
    shares = [row[1] / occupied for row in rows] if occupied else []
    sorted_shares = sorted(shares, reverse=True)
    weighted_days = sum(max((row[2] - as_of).days, 0) * row[1] for row in rows)

    high_ai_area = sum(
        area
        for lease, area, _ in rows
        if str(_fact_value(lease.get("ai_space_demand_exposure"))).lower() == "high"
    )
    distressed_area = sum(
        area
        for lease, area, _ in rows
        if str(_fact_value(lease.get("business_health"))).lower() == "distressed"
    )
    public_distress_event_count = sum(
        1
        for lease, _, _ in rows
        for event in lease.get("public_distress_events", [])
        if _fact_value(event) is not None
    )
    public_distress_area = sum(
        area
        for lease, area, _ in rows
        if any(_fact_value(event) is not None for event in lease.get("public_distress_events", []))
    )

    return {
        "occupied_area_sf": _round(occupied, 2),
        "occupancy_rate": _round(occupied / gross_area if gross_area else None),
        "largest_tenant_share_of_occupied": _round(max(shares) if shares else None),
        "top_three_tenant_share_of_occupied": _round(sum(sorted_shares[:3]) if shares else None),
        "tenant_hhi": _round(sum(share * share for share in shares) if shares else None),
        "weighted_average_lease_term_years": _round(
            weighted_days / occupied / 365.25 if occupied else None, 2
        ),
        "high_ai_exposure_share_of_occupied": _round(
            high_ai_area / occupied if occupied else None
        ),
        "distressed_tenant_share_of_occupied": _round(
            distressed_area / occupied if occupied else None
        ),
        "public_distress_event_count": public_distress_event_count,
        "public_distress_event_tenant_share_of_occupied": _round(
            public_distress_area / occupied if occupied else None
        ),
    }


def _debt_summary(payload: dict[str, Any], as_of: date) -> dict[str, Any]:
    total_balance = 0.0
    total_service = 0.0
    balance_complete = True
    service_complete = True
    known_near = False
    inferred_near = False
    maturities: list[dict[str, Any]] = []
    reported_signal_states: list[str] = []
    horizon = _add_months(as_of, 36)

    for loan in payload.get("debt", []):
        balance = _fact_value(loan.get("current_balance"))
        service = _fact_value(loan.get("annual_debt_service"))
        if isinstance(balance, (int, float)):
            total_balance += float(balance)
        else:
            balance_complete = False
        if isinstance(service, (int, float)):
            total_service += float(service)
        else:
            service_complete = False

        maturity_fact = loan.get("maturity_date")
        maturity = _fact_value(maturity_fact)
        if maturity is not None:
            maturity_date = _parse_date(maturity, f"debt {loan.get('loan_id')} maturity_date")
            label = _fact_label(maturity_fact)
            if maturity_date <= horizon:
                if label in {"KNOWN", "OBSERVED"}:
                    known_near = True
                elif label == "INFERRED":
                    inferred_near = True
            maturities.append(
                {
                    "loan_id": loan.get("loan_id"),
                    "maturity_date": maturity,
                    "evidence_label": label,
                    "source_ref": maturity_fact.get("source_ref"),
                }
            )

        reported_state = _fact_value(loan.get("debt_signal_state"))
        if reported_state in DEBT_SIGNAL_STATES:
            reported_signal_states.append(reported_state)

    legal_priority = (
        "RECEIVER_APPOINTED",
        "RECEIVERSHIP_REQUESTED",
        "FORECLOSURE_OR_LENDER_ACTION",
        "LEGAL_DISTRESS_ACTIVE",
    )
    explicit_legal_state = next(
        (state for state in legal_priority if state in reported_signal_states), None
    )
    if explicit_legal_state:
        state = explicit_legal_state
    elif "REFI_OR_EXTENSION_FOUND" in reported_signal_states:
        state = "REFI_OR_EXTENSION_FOUND"
    elif known_near:
        state = "MATURITY_KNOWN_NEAR_TERM"
    elif inferred_near:
        state = "MATURITY_INFERRED_NEAR_TERM"
    elif "POSSIBLY_PAID_OFF" in reported_signal_states:
        state = "POSSIBLY_PAID_OFF"
    elif not payload.get("debt") or any(
        _fact_value(loan.get("maturity_date")) is None for loan in payload.get("debt", [])
    ):
        state = "INSUFFICIENT_DEBT_EVIDENCE"
    else:
        state = "NO_SIGNAL_FOUND"

    return {
        "state": state,
        "total_current_balance": _round(total_balance, 2) if balance_complete else None,
        "total_annual_debt_service": _round(total_service, 2) if service_complete else None,
        "maturities": sorted(maturities, key=lambda item: item["maturity_date"]),
        "reported_signal_states": sorted(set(reported_signal_states)),
    }


def _renewal_multiplier(renewal_fact: Any, renewal_case: str) -> float | None:
    value = _fact_value(renewal_fact)
    if not isinstance(value, dict):
        return None
    low = value.get("low")
    high = value.get("high")
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return None
    if renewal_case == "low":
        return float(low)
    if renewal_case == "high":
        return float(high)
    return (float(low) + float(high)) / 2


def _projection(
    payload: dict[str, Any],
    scenario_name: str,
    scenario: dict[str, Any],
    horizon_months: int,
    as_of: date,
    tenant_metrics: dict[str, Any],
) -> dict[str, Any]:
    horizon_date = _add_months(as_of, horizon_months)
    years = horizon_months / 12
    gross_area = _fact_value(payload["building"].get("gross_area_sf"))
    current_occupied = tenant_metrics.get("occupied_area_sf")
    expiring: list[dict[str, Any]] = []
    projected_area = 0.0
    occupancy_complete = tenant_metrics.get("evidence_label") != "INSUFFICIENT_EVIDENCE"

    for lease in _active_leases(payload, as_of):
        area = _fact_value(lease.get("leased_area_sf"))
        lease_end_value = _fact_value(lease.get("lease_end"))
        if not isinstance(area, (int, float)) or lease_end_value is None:
            occupancy_complete = False
            continue
        lease_end = _parse_date(lease_end_value, "lease_end")
        multiplier = 1.0
        if lease_end <= horizon_date:
            multiplier = _renewal_multiplier(
                lease.get("renewal_probability_range"), scenario["renewal_case"]
            )
            expiring.append(
                {
                    "tenant_id": lease.get("tenant_id"),
                    "tenant_name": _fact_value(lease.get("tenant_name")),
                    "lease_end": lease_end_value,
                    "leased_area_sf": float(area),
                    "renewal_probability_range": _fact_value(
                        lease.get("renewal_probability_range")
                    ),
                    "selected_scenario_probability": _round(multiplier),
                    "evidence_label": _fact_label(lease.get("renewal_probability_range")),
                }
            )
            if multiplier is None:
                occupancy_complete = False
                continue
        projected_area += float(area) * multiplier

    rollover_area = sum(item["leased_area_sf"] for item in expiring)
    rollover_pct = rollover_area / gross_area if gross_area else None
    projected_occupancy = projected_area / gross_area if gross_area and occupancy_complete else None

    operations = payload["operations"]
    current_revenue = _fact_value(operations.get("annual_gross_revenue"))
    current_expenses = _fact_value(operations.get("annual_operating_expenses"))
    current_capex = _fact_value(operations.get("annual_required_capex"))
    current_reserves = _fact_value(operations.get("cash_reserves"))
    current_value = _fact_value(payload["building"].get("market_value"))
    current_bhi = _fact_value(payload["bhi"].get("score"))
    finance_inputs = (
        current_revenue,
        current_expenses,
        current_capex,
        current_reserves,
        current_value,
        current_occupied,
    )
    finance_complete = occupancy_complete and all(
        isinstance(value, (int, float)) for value in finance_inputs
    )

    debt_summary = _debt_summary(payload, as_of)
    total_balance = debt_summary["total_current_balance"]
    current_debt_service = debt_summary["total_annual_debt_service"]
    finance_complete = finance_complete and total_balance is not None and current_debt_service is not None
    finance_complete = finance_complete and debt_summary["state"] != "INSUFFICIENT_DEBT_EVIDENCE"

    financials: dict[str, Any] | None = None
    refinance: dict[str, Any] | None = None
    flags: list[str] = []
    assumptions = payload["scenario_assumptions"]["underwriting"]

    if finance_complete:
        occupied_ratio = projected_area / float(current_occupied) if current_occupied else 0
        rent_factor = (1 + scenario["annual_rent_growth_pct"] / 100) ** years
        expense_factor = (1 + scenario["annual_expense_growth_pct"] / 100) ** years
        value_factor = (1 + scenario["annual_value_change_pct"] / 100) ** years
        debt_service_factor = (
            1 + scenario["existing_debt_service_change_pct"] / 100
        ) ** years
        projected_revenue = float(current_revenue) * occupied_ratio * rent_factor
        projected_expenses = float(current_expenses) * expense_factor
        projected_noi = projected_revenue - projected_expenses
        projected_value = float(current_value) * value_factor
        projected_debt_service = float(current_debt_service) * debt_service_factor
        projected_capex = float(current_capex) * expense_factor
        dscr = projected_noi / projected_debt_service if projected_debt_service else None
        ltv = float(total_balance) / projected_value if projected_value else None
        debt_yield = projected_noi / float(total_balance) if total_balance else None

        current_noi = float(current_revenue) - float(current_expenses)
        current_cash_after = current_noi - float(current_debt_service) - float(current_capex)
        projected_cash_after = projected_noi - projected_debt_service - projected_capex
        cumulative_cash = years * (current_cash_after + projected_cash_after) / 2
        ending_reserves = float(current_reserves) + cumulative_cash

        financials = {
            "projected_annual_gross_revenue": _round(projected_revenue, 2),
            "projected_annual_operating_expenses": _round(projected_expenses, 2),
            "projected_noi": _round(projected_noi, 2),
            "projected_annual_debt_service": _round(projected_debt_service, 2),
            "projected_annual_required_capex": _round(projected_capex, 2),
            "projected_market_value": _round(projected_value, 2),
            "dscr": _round(dscr),
            "ltv": _round(ltv),
            "debt_yield": _round(debt_yield),
            "ending_cash_reserves": _round(ending_reserves, 2),
            "evidence_label": "INFERRED",
        }

        maturing_balance = 0.0
        maturity_complete = True
        for loan in payload.get("debt", []):
            maturity_value = _fact_value(loan.get("maturity_date"))
            balance = _fact_value(loan.get("current_balance"))
            if maturity_value is None:
                maturity_complete = False
                continue
            if _parse_date(maturity_value, "maturity_date") <= horizon_date:
                if isinstance(balance, (int, float)):
                    maturing_balance += float(balance)
                else:
                    maturity_complete = False

        if maturing_balance and maturity_complete:
            refi_rate = scenario["refinance_interest_rate_pct"] / 100
            by_ltv = projected_value * assumptions["max_ltv"]
            by_dscr = (
                projected_noi / assumptions["minimum_dscr"] / refi_rate
                if refi_rate > 0 and projected_noi > 0
                else 0.0
            )
            supportable_proceeds = max(min(by_ltv, by_dscr), 0.0)
            refinance_gap = max(maturing_balance - supportable_proceeds, 0.0)
            refinance = {
                "maturing_balance": _round(maturing_balance, 2),
                "supportable_by_ltv": _round(by_ltv, 2),
                "supportable_by_dscr_interest_only_proxy": _round(by_dscr, 2),
                "supportable_refinance_proceeds": _round(supportable_proceeds, 2),
                "refinance_gap": _round(refinance_gap, 2),
                "refinance_gap_pct_of_maturing_balance": _round(
                    refinance_gap / maturing_balance if maturing_balance else None
                ),
                "evidence_label": "INFERRED",
                "limitation": (
                    "Interest-only sizing proxy; lender amortization, fees, reserves, covenants, "
                    "and closing costs are not modeled."
                ),
            }

        if dscr is not None and dscr < assumptions["minimum_dscr"]:
            flags.append("DSCR_BELOW_UNDERWRITING_MINIMUM")
        if dscr is not None and dscr < 1:
            flags.append("DEBT_SERVICE_SHORTFALL")
        if ending_reserves < 0:
            flags.append("RESERVE_DEPLETION")
        if refinance and refinance["refinance_gap"] > 0:
            flags.append("REFINANCE_GAP")
    else:
        flags.append("INSUFFICIENT_FINANCIAL_EVIDENCE")

    if rollover_pct is not None and rollover_pct >= assumptions["lease_rollover_warning_pct"]:
        flags.append("LEASE_ROLLOVER_CONCENTRATION")
    largest_share = tenant_metrics.get("largest_tenant_share_of_occupied")
    if (
        largest_share is not None
        and largest_share >= assumptions["tenant_concentration_warning_pct"]
    ):
        flags.append("TENANT_CONCENTRATION")
    if tenant_metrics.get("high_ai_exposure_share_of_occupied", 0) > 0:
        flags.append("AI_SPACE_DEMAND_EXPOSURE_REQUIRES_VALIDATION")
    if (
        tenant_metrics.get("distressed_tenant_share_of_occupied", 0) > 0
        or tenant_metrics.get("public_distress_event_count", 0) > 0
    ):
        flags.append("PUBLIC_TENANT_DISTRESS_SIGNAL")

    projected_bhi = None
    if isinstance(current_bhi, (int, float)):
        projected_bhi = min(
            max(float(current_bhi) + scenario["annual_bhi_change_points"] * years, 0), 100
        )
        if projected_bhi < float(current_bhi):
            flags.append("BHI_DETERIORATION_ASSUMPTION")

    if "INSUFFICIENT_FINANCIAL_EVIDENCE" in flags:
        stress_state = "INSUFFICIENT_EVIDENCE"
    else:
        gap_pct = refinance.get("refinance_gap_pct_of_maturing_balance") if refinance else 0
        if (
            "DEBT_SERVICE_SHORTFALL" in flags
            or "RESERVE_DEPLETION" in flags
            or (gap_pct is not None and gap_pct >= assumptions["refinance_gap_critical_pct"])
        ):
            stress_state = "CRITICAL"
        elif (
            "REFINANCE_GAP" in flags
            or (
                financials
                and financials["dscr"] is not None
                and financials["dscr"] < assumptions["dscr_watch"]
            )
        ):
            stress_state = "HIGH"
        elif "LEASE_ROLLOVER_CONCENTRATION" in flags or "TENANT_CONCENTRATION" in flags:
            stress_state = "WATCH"
        else:
            stress_state = "STABLE"

    return {
        "horizon_months": horizon_months,
        "horizon_date": horizon_date.isoformat(),
        "financial_stress_state": stress_state,
        "projected_bhi": _round(projected_bhi, 1),
        "projected_occupied_area_sf": _round(projected_area, 2) if occupancy_complete else None,
        "projected_occupancy_rate": _round(projected_occupancy),
        "lease_rollover_area_sf": _round(rollover_area, 2),
        "lease_rollover_pct_of_building": _round(rollover_pct),
        "expiring_leases": expiring,
        "financials": financials,
        "refinance": refinance,
        "flags": sorted(set(flags)),
        "evidence_label": "INFERRED" if finance_complete else "INSUFFICIENT_EVIDENCE",
    }


def _reuse_screen(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for candidate in payload.get("reuse_candidates", []):
        fits = {
            key: _fact_value(candidate.get(f"{key}_fit"))
            for key in ("physical", "regulatory", "market", "financial")
        }
        normalized = {key: str(value).lower() if value is not None else None for key, value in fits.items()}
        if "fail" in normalized.values():
            status = "SCREEN_OUT"
        elif None in normalized.values() or "unknown" in normalized.values():
            status = "INSUFFICIENT_EVIDENCE"
        elif "conditional" in normalized.values():
            status = "CONDITIONAL_DUE_DILIGENCE"
        else:
            status = "KEEP_FOR_DUE_DILIGENCE"
        results.append(
            {
                "candidate_use": candidate.get("candidate_use"),
                "status": status,
                "fit": normalized,
                "required_next_evidence": candidate.get("required_next_evidence", []),
                "limitation": "Screening result only; it is not a feasibility, code, cost, or entitlement conclusion.",
            }
        )
    return results


def _evidence_gaps(payload: dict[str, Any], as_of: date) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []

    def check(path: str, fact: Any, why: str, severity: str = "high") -> None:
        if _fact_value(fact) is None:
            gaps.append({"path": path, "severity": severity, "why_it_matters": why})
        elif not fact.get("source_ref"):
            gaps.append(
                {
                    "path": f"{path}.source_ref",
                    "severity": severity,
                    "why_it_matters": "A usable claim must remain traceable to reviewed evidence.",
                }
            )

    building = payload["building"]
    check("building.gross_area_sf", building.get("gross_area_sf"), "Occupancy and tenant concentration cannot be normalized.")
    check("building.market_value", building.get("market_value"), "LTV and refinance capacity cannot be calculated.")
    check("bhi.score", payload["bhi"].get("score"), "The current health baseline is unavailable.")
    check("bhi.evidence_coverage", payload["bhi"].get("evidence_coverage"), "BHI reliability cannot be communicated.")
    coverage = _fact_value(payload["bhi"].get("evidence_coverage"))
    if isinstance(coverage, (int, float)) and coverage < 0.4:
        gaps.append(
            {
                "path": "bhi.evidence_coverage",
                "severity": "medium",
                "why_it_matters": "BHI evidence coverage is below 40%; direction and recommendations need additional corroboration.",
            }
        )

    for key in ("annual_gross_revenue", "annual_operating_expenses", "annual_required_capex", "cash_reserves"):
        check(f"operations.{key}", payload["operations"].get(key), "Cash-flow stress cannot be calculated.")

    for index, loan in enumerate(payload.get("debt", [])):
        check(f"debt[{index}].current_balance", loan.get("current_balance"), "Debt ratios and refinance gap cannot be calculated.")
        check(f"debt[{index}].maturity_date", loan.get("maturity_date"), "Maturity timing cannot be stated.")
        check(f"debt[{index}].annual_debt_service", loan.get("annual_debt_service"), "DSCR cannot be calculated.")

    horizon = _add_months(as_of, max(payload["scenario_assumptions"]["horizons_months"]))
    for index, lease in enumerate(payload.get("leases", [])):
        check(f"leases[{index}].leased_area_sf", lease.get("leased_area_sf"), "Tenant concentration cannot be calculated.")
        check(f"leases[{index}].lease_end", lease.get("lease_end"), "Lease rollover timing cannot be calculated.")
        end_value = _fact_value(lease.get("lease_end"))
        if end_value is not None and _parse_date(end_value, "lease_end") <= horizon:
            check(
                f"leases[{index}].renewal_probability_range",
                lease.get("renewal_probability_range"),
                "An expiring lease cannot be included in the occupancy scenario without an explicit reviewed range.",
            )
    return gaps


def _recommendations(
    payload: dict[str, Any], scenarios: list[dict[str, Any]], tenant_metrics: dict[str, Any]
) -> list[dict[str, Any]]:
    underwriting = payload["scenario_assumptions"]["underwriting"]
    all_horizons = [h for scenario in scenarios for h in scenario["horizons"]]
    recommendations: list[dict[str, Any]] = []

    def add(
        rec_id: str,
        priority: str,
        action: str,
        basis: str,
        decision_gate: str,
        limitation: str,
    ) -> None:
        recommendations.append(
            {
                "id": rec_id,
                "priority": priority,
                "action": action,
                "basis": basis,
                "decision_gate": decision_gate,
                "limitation": limitation,
            }
        )

    if any(h["refinance"] and h["refinance"]["refinance_gap"] > 0 for h in all_horizons):
        add(
            "REFINANCE_READINESS",
            "urgent",
            "Open lender, extension, recapitalization, and sale-path diligence before the first modeled maturity.",
            "At least one explicit scenario produces a positive refinance gap.",
            "Verify the current payoff, maturity, covenants, amortization, extension rights, and lender sizing terms.",
            "The result is an underwriting proxy, not a prediction of default, foreclosure, or bankruptcy.",
        )
    elif _debt_summary(payload, _parse_date(payload["as_of_date"], "as_of_date"))["state"] == "INSUFFICIENT_DEBT_EVIDENCE":
        add(
            "VERIFY_DEBT_TERMS",
            "urgent",
            "Obtain the current lender statement and executed loan documents before presenting a maturity narrative.",
            "Debt timing or balance evidence is incomplete.",
            "Confirm current balance, rate, amortization, maturity, extension options, and recourse.",
            "Original principal, acquisition price, and recorded deed dates are not substitutes for a current payoff.",
        )

    if any(
        (h["lease_rollover_pct_of_building"] or 0) >= underwriting["lease_rollover_warning_pct"]
        for h in all_horizons
    ):
        add(
            "LEASE_ROLLOVER_PLAN",
            "high",
            "Start tenant-by-tenant renewal and backfill plans, with staggered expirations where negotiations allow.",
            "Lease rollover exceeds the operator-supplied warning threshold within the modeled horizon.",
            "Validate the rent roll, options, notices, concessions, subleases, guaranties, and tenant interviews.",
            "Renewal ranges are scenarios, not statements of tenant intent.",
        )

    largest = tenant_metrics.get("largest_tenant_share_of_occupied")
    if largest is not None and largest >= underwriting["tenant_concentration_warning_pct"]:
        add(
            "TENANT_CONCENTRATION",
            "high",
            "Test a phased multi-tenant leasing plan and compare it with an anchor renewal on risk-adjusted economics.",
            f"The largest tenant occupies {largest:.1%} of occupied area.",
            "Compare TI, leasing commissions, downtime, credit quality, management cost, and floorplate divisibility.",
            "More small tenants may reduce single-name exposure but can increase turnover, capital cost, and management burden.",
        )

    if any("RESERVE_DEPLETION" in h["flags"] for h in all_horizons):
        add(
            "CAPITAL_AND_CAPEX_PLAN",
            "urgent",
            "Create a dated liquidity plan covering debt service, required capex, leasing costs, and downside reserves.",
            "At least one scenario depletes modeled cash reserves.",
            "Replace annual proxies with monthly cash flow, actual vendor budgets, TI/LC schedules, and funding commitments.",
            "This engine does not model taxes, closing costs, covenant remedies, or every capital call.",
        )

    if (tenant_metrics.get("high_ai_exposure_share_of_occupied") or 0) > 0:
        add(
            "TENANT_DEMAND_VALIDATION",
            "medium",
            "Validate exposed tenants' hiring, office attendance, space plans, sublease activity, and public filings.",
            "One or more tenants were tagged with high AI-related space-demand exposure.",
            "Use tenant-specific primary evidence and document the date of each signal.",
            "AI exposure is not a job-loss, bankruptcy, or non-renewal prediction and does not alter renewal rates automatically.",
        )

    if payload.get("reuse_candidates"):
        add(
            "PHASED_REUSE_DILIGENCE",
            "medium",
            "Advance only the retained reuse candidates through physical, regulatory, market, and financial diligence.",
            "Alternative uses were supplied for screening.",
            "Complete site measurements, zoning/code review, demand study, concept design, cost plan, and financing analysis.",
            "A screened use is not a confirmed conversion, entitlement, cost, or incentive eligibility conclusion.",
        )

    priority_order = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(recommendations, key=lambda item: (priority_order[item["priority"]], item["id"]))


def analyze_trajectory(payload: dict[str, Any]) -> dict[str, Any]:
    """Analyze explicit building facts and scenarios without external side effects."""

    _validate_input(payload)
    as_of = _parse_date(payload["as_of_date"], "as_of_date")
    gross_area_value = _fact_value(payload["building"].get("gross_area_sf"))
    gross_area = float(gross_area_value) if isinstance(gross_area_value, (int, float)) else None
    active_leases = _active_leases(payload, as_of)
    tenant_metrics = _tenant_metrics(active_leases, gross_area, as_of)
    tenant_data_complete = all(
        _fact_value(lease.get("lease_end")) is not None
        and isinstance(_fact_value(lease.get("leased_area_sf")), (int, float))
        for lease in payload.get("leases", [])
    )
    tenant_metrics["evidence_label"] = (
        "INFERRED" if tenant_data_complete else "INSUFFICIENT_EVIDENCE"
    )
    if not tenant_data_complete:
        for key in (
            "occupied_area_sf",
            "occupancy_rate",
            "largest_tenant_share_of_occupied",
            "top_three_tenant_share_of_occupied",
            "tenant_hhi",
            "weighted_average_lease_term_years",
            "high_ai_exposure_share_of_occupied",
            "distressed_tenant_share_of_occupied",
            "public_distress_event_tenant_share_of_occupied",
        ):
            tenant_metrics[key] = None
    debt_summary = _debt_summary(payload, as_of)

    scenario_results: list[dict[str, Any]] = []
    assumptions = payload["scenario_assumptions"]
    for name in SCENARIO_NAMES:
        scenario = assumptions["scenarios"][name]
        scenario_results.append(
            {
                "name": name,
                "assumptions": scenario,
                "horizons": [
                    _projection(
                        payload,
                        name,
                        scenario,
                        int(months),
                        as_of,
                        tenant_metrics,
                    )
                    for months in sorted(set(assumptions["horizons_months"]))
                ],
            }
        )

    evidence_gaps = _evidence_gaps(payload, as_of)
    if (tenant_metrics.get("occupancy_rate") or 0) > 1:
        evidence_gaps.append(
            {
                "path": "leases",
                "severity": "high",
                "why_it_matters": "Active leased area exceeds reported gross building area; reconcile the rent roll and area definition.",
            }
        )
    acquisition_date = payload["building"].get("acquisition_date")
    acquisition_price = payload["building"].get("acquisition_price")
    current_snapshot = {
        "current_use": _fact_value(payload["building"].get("current_use")),
        "bhi_score": _fact_value(payload["bhi"].get("score")),
        "bhi_evidence_coverage": _fact_value(payload["bhi"].get("evidence_coverage")),
        "acquisition": {
            "date": _fact_value(acquisition_date),
            "price": _fact_value(acquisition_price),
            "note": "Reported for context only; neither value is used as current debt balance or market value.",
        },
        "tenant_metrics": tenant_metrics,
        "debt": debt_summary,
    }

    output = {
        "schema_version": "1.0",
        "building_id": payload["building"]["building_id"],
        "as_of_date": payload["as_of_date"],
        "methodology": {
            "type": "deterministic_scenario_analysis",
            "forecast_probability": False,
            "external_data_access": False,
            "ai_generated_recommendations": False,
            "statement": (
                "Results are calculations from reviewed facts and visible operator assumptions. "
                "They are scenarios, not forecasts of default, bankruptcy, job loss, or tenant intent."
            ),
        },
        "current_snapshot": current_snapshot,
        "scenarios": scenario_results,
        "reuse_screen": _reuse_screen(payload),
        "recommendations": _recommendations(payload, scenario_results, tenant_metrics),
        "evidence_gaps": evidence_gaps,
        "challenge_notes": [
            "Acquisition price and recorded original principal do not establish the current loan balance.",
            "Lease-renewal inputs are reviewed scenario ranges, not tenant promises or calibrated probabilities.",
            "AI space-demand exposure is a diligence flag; it is not used to infer layoffs, bankruptcy, or non-renewal.",
            "Replacing an anchor with smaller tenants can diversify credit exposure while increasing TI, leasing, downtime, and management costs.",
            "Reuse screening does not establish physical feasibility, zoning/code compliance, conversion cost, market demand, or financing availability.",
        ],
        "review_status": (
            "DEMO_ONLY"
            if payload.get("demo_only")
            else "REVIEW_REQUIRED"
            if evidence_gaps
            else "READY_FOR_HUMAN_REVIEW"
        ),
    }
    return output
