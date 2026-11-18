"""Chỉ so hồi quy khi ba lượt đo sạch và cùng điều kiện."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))
from lib.perf_report import regression  # noqa: E402
from perf_fixtures import runs  # noqa: E402


class PerfRegressionTest(unittest.TestCase):
    def test_three_stable_runs_same_conditions_compare_two_builds(self):
        result = regression(runs(), runs((100, 101, 102), source="new-source"))
        self.assertFalse(result["errors"])
        self.assertEqual(result["baseline_mean"], 100)
        self.assertEqual(result["current_mean"], 101)
        margin = 4.303 / math.sqrt(3)
        self.assertAlmostEqual(result["ci95"][0], 100 - margin)
        self.assertAlmostEqual(result["ci95"][1], 100 + margin)

    def test_over_ten_percent_fails_even_inside_wide_ci(self):
        result = regression(runs((50, 100, 150)), runs((111, 111, 111)))
        self.assertLess(result["ci95"][0], 111)
        self.assertGreater(result["ci95"][1], 111)
        self.assertTrue(result["errors"])

    def test_outside_ci95_fails_even_under_ten_percent(self):
        for latency in [95, 105]:
            with self.subTest(latency=latency):
                result = regression(runs(), runs((latency,) * 3))
                self.assertEqual(result["current_mean"], latency)
                self.assertTrue(result["errors"])

    def test_identical_runs_do_not_divide_by_zero(self):
        result = regression(runs((100, 100, 100)), runs((100, 100, 100)))
        self.assertEqual(list(result["ci95"]), [100, 100])
        self.assertFalse(result["errors"])

    def test_exactly_three_runs_per_side(self):
        for count in [0, 1, 2, 4]:
            for side in [0, 1]:
                with self.subTest(count=count, side=side):
                    pair = [runs(), runs()]
                    pair[side] = runs([100] * count)
                    with self.assertRaises(ValueError):
                        regression(*pair)

    def test_any_changed_condition_is_not_comparable(self):
        changes = {
            "workload_hash": "other", "dataset_hash": "other", "topology_hash": "other",
            "runner_host": "other", "target_host": "other", "kind": "soak", "scope": "health",
            "rate": 10, "duration_seconds": 30, "warmup_seconds": 5, "case_hash": "other",
        }
        for key, value in changes.items():
            with self.subTest(key=key):
                current = runs((90, 90, 90))
                for item in current:
                    item[key] = value
                with self.assertRaises(ValueError):
                    regression(runs(), current)

    def test_one_run_with_missing_or_different_config_rejects_cohort(self):
        for side in [0, 1]:
            for missing in [False, True]:
                with self.subTest(side=side, missing=missing):
                    pair = [runs(), runs()]
                    if missing:
                        del pair[side][1]["dataset_hash"]
                    else:
                        pair[side][1]["dataset_hash"] = "dataset-different"
                    with self.assertRaises(ValueError):
                        regression(*pair)

    def test_failed_dropped_or_semantic_error_run_is_not_comparable(self):
        invalid = [("success", False), ("success", 1), ("requests", 0), ("requests", -1),
                   ("dropped_iterations", 1), ("semantic_error_rate", 0.001), ("server_error_rate", 0.001)]
        for key, value in invalid:
            for side in [0, 1]:
                with self.subTest(key=key, value=value, side=side):
                    pair = [runs(), runs()]
                    pair[side][1][key] = value
                    with self.assertRaises(ValueError):
                        regression(*pair)

    def test_nan_inf_negative_and_bool_are_not_latency(self):
        for value in [float("nan"), float("inf"), -float("inf"), 0, -1, True, False]:
            for side in [0, 1]:
                with self.subTest(value=value, side=side):
                    pair = [runs(), runs()]
                    pair[side][1]["p95_ms"] = value
                    with self.assertRaises(ValueError):
                        regression(*pair)

    def test_bool_cannot_pose_as_count_or_zero_rate(self):
        for key, value in [("requests", True), ("dropped_iterations", False), ("semantic_error_rate", False)]:
            with self.subTest(key=key):
                current = runs()
                current[0][key] = value
                with self.assertRaises(ValueError):
                    regression(runs(), current)

    def test_environment_k6_and_source_not_mixed_inside_cohort(self):
        for key in ["environment", "k6_version", "source_hash"]:
            for side in [0, 1]:
                for missing in [True, False]:
                    with self.subTest(key=key, side=side, missing=missing):
                        pair = [runs(), runs()]
                        if missing:
                            del pair[side][1][key]
                        else:
                            pair[side][1][key] = "different"
                        with self.assertRaises(ValueError):
                            regression(*pair)

    def test_same_host_runs_never_compare_with_two_host_runs(self):
        current = runs()
        for item in current:
            item["same_host"] = True
        with self.assertRaises(ValueError):
            regression(runs(), current)

    def test_empty_or_bool_config_is_not_identical_config(self):
        for key, value in [("duration_seconds", True), ("warmup_seconds", False),
                           ("environment", ""), ("source_hash", None), ("k6_version", None)]:
            pair = [runs(), runs()]
            for cohort in pair:
                for item in cohort:
                    item[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                regression(*pair)


if __name__ == "__main__":
    unittest.main()
