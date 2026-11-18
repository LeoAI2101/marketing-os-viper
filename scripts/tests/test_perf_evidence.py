"""Cờ đạt tự khai không thay số đo độc lập cho bảy tiêu chí; không phát tải trong test."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))
from lib.perf_evidence import CRITERIA, assess  # noqa: E402
from perf_fixtures import evidence, stress_stage, workload  # noqa: E402


class PerfEvidenceTest(unittest.TestCase):
    def status(self, bundle, criterion, wl=None):
        return assess(bundle, wl or workload())["results"][criterion]["status"]

    def test_full_evidence_passes_all_seven_criteria(self):
        result = assess(evidence(), workload())
        self.assertEqual(tuple(result["results"]), CRITERIA)
        self.assertTrue(all(item["status"] == "pass" for item in result["results"].values()), result)
        self.assertFalse(result["errors"])

    def test_self_declared_flags_do_not_replace_measurements(self):
        result = assess({"environment": "PT", "source_hash": "x", "success": True, "accepted": True,
                         "latency": True, "regression": True}, workload())
        self.assertTrue(all(item["status"] == "missing" for item in result["results"].values()))
        self.assertTrue(all(item["reasons"] for item in result["results"].values()))

    def test_removed_section_is_missing_not_inherited(self):
        for section, criterion in [("load", "latency"), ("peak", "throughput"), ("soak", "soak"),
                                   ("stress", "headroom"), ("recovery", "recovery"), ("plans", "plans"),
                                   ("regression", "regression")]:
            with self.subTest(section=section):
                bundle = evidence()
                del bundle[section]
                self.assertEqual(self.status(bundle, criterion), "missing")

    def test_latency_checks_each_group_tail_and_errors(self):
        for group, field, value in [("R1", "p95_ms", 301), ("W1", "p95_ms", 501), ("R1", "p99_ms", 251),
                                    ("R1", "server_error_rate", .001), ("W1", "semantic_error_rate", .001)]:
            with self.subTest(group=group, field=field):
                bundle = evidence()
                bundle["load"]["groups"][group][field] = value
                self.assertEqual(self.status(bundle, "latency"), "fail")

    def test_thresholds_come_from_workload_not_code(self):
        bundle = evidence()
        bundle["load"]["groups"]["R1"].update(p95_ms=400, p99_ms=500)
        self.assertEqual(self.status(bundle, "latency"), "fail")
        wl = workload()
        wl["groups"]["R1"]["p95_ms"] = 450
        self.assertEqual(self.status(bundle, "latency", wl), "pass")

    def test_throughput_rejects_short_thin_dropped_or_wrong_mix(self):
        for section, key, value in [("load", "duration_seconds", 1799), ("load", "warmup_seconds", 299),
                                    ("load", "achieved_rps", 899), ("load", "active_users", 999),
                                    ("load", "dropped_iterations", 1), ("load", "scope", "probe"),
                                    ("peak", "duration_seconds", 899), ("peak", "achieved_rps", 1199)]:
            with self.subTest(section=section, key=key):
                bundle = evidence()
                bundle[section][key] = value
                self.assertEqual(self.status(bundle, "throughput"), "fail")
        bundle = evidence()
        shift = bundle["load"]["requests"] // 50
        bundle["load"]["groups"]["R1"]["requests"] -= shift
        bundle["load"]["groups"]["R2"]["requests"] += shift
        self.assertEqual(self.status(bundle, "throughput"), "fail")

    def test_nan_bool_and_missing_metric_are_not_measurements(self):
        for value in [float("nan"), float("inf"), True, False, None]:
            with self.subTest(value=value):
                bundle = evidence()
                bundle["load"]["groups"]["R1"]["p95_ms"] = value
                self.assertEqual(self.status(bundle, "latency"), "missing")
        for key in ["duration_seconds", "active_users", "dropped_iterations"]:
            with self.subTest(key=key):
                bundle = evidence()
                bundle["load"][key] = False
                self.assertEqual(self.status(bundle, "throughput"), "missing")
        bundle = evidence()
        del bundle["load"]["groups"]["W1"]
        self.assertEqual(self.status(bundle, "latency"), "missing")

    def test_short_soak_or_lost_sample_does_not_prove_full_window(self):
        bundle = evidence()
        bundle["soak"]["duration_seconds"] = 28799
        self.assertEqual(self.status(bundle, "soak"), "fail")
        for index in [0, 48, -1]:
            with self.subTest(missing_sample=index):
                bundle = evidence()
                del bundle["soak"]["samples"][index]
                self.assertNotEqual(self.status(bundle, "soak"), "pass")
        bundle = evidence()
        bundle["soak"]["samples"][48]["pool_error_coverage"] = False
        self.assertEqual(self.status(bundle, "soak"), "missing")

    def test_soak_fails_on_queue_stream_leak_restart_or_pool_error(self):
        for defect in ["queue", "stream", "rss", "restart", "pid", "pool"]:
            with self.subTest(defect=defect):
                bundle = evidence()
                samples = bundle["soak"]["samples"]
                if defect == "queue":
                    for sample in samples:
                        sample["queues"]["orders.outbox"] = 1
                elif defect == "stream":
                    for index, sample in enumerate(samples):
                        sample["streams"]["orders.events"]["length"] = 100 + index
                elif defect == "rss":
                    for sample in samples[-20:]:
                        sample["units"]["svc-orders"]["rss_bytes"] *= 1.2
                elif defect in ["restart", "pid"]:
                    samples[48]["units"]["svc-orders"]["restarts" if defect == "restart" else "pid"] += 1
                else:
                    samples[48]["pool_errors"]["pool_timeout"] = 1
                self.assertEqual(self.status(bundle, "soak"), "fail")

    def test_soak_requires_declared_inventory(self):
        bundle = evidence()
        for sample in bundle["soak"]["samples"]:
            del sample["queues"]
        self.assertEqual(self.status(bundle, "soak"), "missing")
        wl = workload()
        wl["soak"]["inventory"] = ["units", "streams"]
        self.assertEqual(self.status(bundle, "soak", wl), "pass")

    def test_soak_rejects_bool_counts(self):
        for key in ["pid", "restarts"]:
            with self.subTest(key=key):
                bundle = evidence()
                bundle["soak"]["samples"][48]["units"]["svc-orders"][key] = False
                self.assertEqual(self.status(bundle, "soak"), "missing")
        bundle = evidence()
        bundle["soak"]["samples"][48]["queues"]["orders.outbox"] = False
        self.assertEqual(self.status(bundle, "soak"), "missing")

    def test_stress_needs_breaking_point_and_real_headroom(self):
        bundle = evidence()
        bundle["stress"]["stages"][1] = stress_stage(3500)
        self.assertEqual(self.status(bundle, "headroom"), "fail")
        bundle = evidence()
        bundle["stress"]["stages"][-1]["slo_met"] = True
        self.assertNotEqual(self.status(bundle, "headroom"), "pass")
        bundle = evidence()
        bundle["stress"]["stages"][0]["slo_met"] = 1
        self.assertEqual(self.status(bundle, "headroom"), "missing")

    def test_stress_distrusts_flags_and_probe_or_side_door(self):
        for defect in ["forged_flag", "decreasing_step", "missing_metrics", "probe", "side_door"]:
            with self.subTest(defect=defect):
                bundle = evidence()
                stress = bundle["stress"]
                if defect == "forged_flag":
                    stress["stages"][1]["groups"]["R1"]["p95_ms"] = 900
                elif defect == "decreasing_step":
                    stress["stages"][-1]["requested_rps"] = 1
                elif defect == "missing_metrics":
                    del stress["stages"][1]["groups"]
                elif defect == "probe":
                    stress["scope"] = "probe"
                else:
                    stress["surface"] = "internal-single-pod"
                self.assertNotEqual(self.status(bundle, "headroom"), "pass")

    def test_recovery_needs_every_fault_and_consecutive_samples_in_time(self):
        for defect in ["missing_fault", "late", "bad_middle", "not_restored", "slow_spike"]:
            with self.subTest(defect=defect):
                bundle = evidence()
                section = bundle["recovery"]
                if defect == "missing_fault":
                    section["drills"].pop()
                elif defect == "late":
                    section["drills"][0]["samples"][-1]["elapsed_seconds"] = 61
                elif defect == "bad_middle":
                    section["drills"][0]["samples"][1]["semantic_error_rate"] = .01
                elif defect == "not_restored":
                    section["drills"][0]["restored"] = False
                else:
                    section["spike"]["ramp_seconds"] = 31
                self.assertNotEqual(self.status(bundle, "recovery"), "pass")

    def test_nested_large_seq_scan_is_found(self):
        bundle = evidence()
        query = bundle["plans"]["queries"][0]
        scan = {"Node Type": "Seq Scan", "Schema": "orders", "Relation Name": "order"}
        query["plan"] = [{"Plan": {"Node Type": "Aggregate", "Plans": [scan]}}]
        self.assertEqual(self.status(bundle, "plans"), "fail")
        query["relation_rows"]["orders.order"] = 99999
        self.assertEqual(self.status(bundle, "plans"), "pass")
        del query["relation_rows"]["orders.order"]
        self.assertEqual(self.status(bundle, "plans"), "missing")

    def test_empty_plan_is_not_execution_plan_evidence(self):
        bundle = evidence()
        bundle["plans"] = {"success": True, "queries": [{"name": "x", "plan": [{"Plan": {}}], "relation_rows": {}}]}
        self.assertEqual(self.status(bundle, "plans"), "missing")
        bundle = evidence()
        bundle["plans"]["queries"][0]["plan"][0]["Plan"]["Plans"] = [{}]
        self.assertEqual(self.status(bundle, "plans"), "missing")

    def test_regression_missing_run_is_missing_and_slower_is_fail(self):
        bundle = evidence()
        bundle["regression"]["current"].pop()
        self.assertEqual(self.status(bundle, "regression"), "missing")
        bundle = evidence()
        for item in bundle["regression"]["current"]:
            item["p95_ms"] = 120
        self.assertEqual(self.status(bundle, "regression"), "fail")
        bundle = evidence()
        bundle["source_hash"] = "a-different-build"
        self.assertEqual(self.status(bundle, "regression"), "missing")


if __name__ == "__main__":
    unittest.main()
