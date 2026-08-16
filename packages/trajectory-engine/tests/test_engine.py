import copy
import json
import sys
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from trajectory_engine import InputError, analyze_trajectory  # noqa: E402


class TrajectoryEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        example = PACKAGE_ROOT / "examples" / "distressed-office.synthetic.json"
        cls.payload = json.loads(example.read_text(encoding="utf-8"))

    def test_synthetic_example_produces_three_auditable_scenarios(self):
        result = analyze_trajectory(copy.deepcopy(self.payload))

        self.assertEqual(result["review_status"], "DEMO_ONLY")
        self.assertEqual(result["current_snapshot"]["debt"]["state"], "MATURITY_KNOWN_NEAR_TERM")
        self.assertEqual([item["name"] for item in result["scenarios"]], ["improving", "base", "adverse"])
        self.assertTrue(all(len(item["horizons"]) == 3 for item in result["scenarios"]))

        base_12 = result["scenarios"][1]["horizons"][0]
        self.assertEqual(base_12["financial_stress_state"], "CRITICAL")
        self.assertGreater(base_12["refinance"]["refinance_gap"], 0)
        self.assertIn("REFINANCE_GAP", base_12["flags"])

    def test_acquisition_price_does_not_change_financial_projection(self):
        first = analyze_trajectory(copy.deepcopy(self.payload))
        modified = copy.deepcopy(self.payload)
        modified["building"]["acquisition_price"]["value"] = 999999999
        second = analyze_trajectory(modified)

        self.assertEqual(first["scenarios"], second["scenarios"])

    def test_unknown_renewal_range_blocks_affected_projection(self):
        modified = copy.deepcopy(self.payload)
        modified["leases"][0]["renewal_probability_range"] = {
            "value": None,
            "evidence_label": "UNKNOWN"
        }
        result = analyze_trajectory(modified)

        base_12 = result["scenarios"][1]["horizons"][0]
        self.assertEqual(base_12["financial_stress_state"], "INSUFFICIENT_EVIDENCE")
        self.assertIsNone(base_12["projected_occupancy_rate"])
        self.assertIn("INSUFFICIENT_FINANCIAL_EVIDENCE", base_12["flags"])
        self.assertTrue(
            any(gap["path"].endswith("renewal_probability_range") for gap in result["evidence_gaps"])
        )

    def test_usable_fact_requires_source(self):
        modified = copy.deepcopy(self.payload)
        del modified["debt"][0]["current_balance"]["source_ref"]

        with self.assertRaises(InputError):
            analyze_trajectory(modified)

    def test_ai_exposure_never_changes_renewal_math(self):
        first = analyze_trajectory(copy.deepcopy(self.payload))
        modified = copy.deepcopy(self.payload)
        modified["leases"][0]["ai_space_demand_exposure"]["value"] = "low"
        second = analyze_trajectory(modified)

        first_area = first["scenarios"][1]["horizons"][0]["projected_occupied_area_sf"]
        second_area = second["scenarios"][1]["horizons"][0]["projected_occupied_area_sf"]
        self.assertEqual(first_area, second_area)

    def test_verified_public_distress_event_becomes_a_flag_not_a_bankruptcy_prediction(self):
        modified = copy.deepcopy(self.payload)
        modified["leases"][1]["public_distress_events"] = [
            {
                "value": "WARN notice published 2026-07-01",
                "evidence_label": "KNOWN",
                "source_ref": "synthetic://example/warn-notice"
            }
        ]
        result = analyze_trajectory(modified)

        metrics = result["current_snapshot"]["tenant_metrics"]
        self.assertEqual(metrics["public_distress_event_count"], 1)
        self.assertIn(
            "PUBLIC_TENANT_DISTRESS_SIGNAL",
            result["scenarios"][1]["horizons"][0]["flags"],
        )
        self.assertFalse(result["methodology"]["forecast_probability"])

    def test_verified_legal_debt_state_takes_priority(self):
        modified = copy.deepcopy(self.payload)
        modified["debt"][0]["debt_signal_state"]["value"] = "RECEIVER_APPOINTED"
        result = analyze_trajectory(modified)

        self.assertEqual(result["current_snapshot"]["debt"]["state"], "RECEIVER_APPOINTED")

    def test_underwriting_fraction_cannot_exceed_one(self):
        modified = copy.deepcopy(self.payload)
        modified["scenario_assumptions"]["underwriting"]["max_ltv"] = 1.2

        with self.assertRaises(InputError):
            analyze_trajectory(modified)


if __name__ == "__main__":
    unittest.main()
