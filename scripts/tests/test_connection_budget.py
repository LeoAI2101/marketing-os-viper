"""Ngân sách DB phải đủ cả rollout; thiếu bằng chứng không được báo xanh."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from lib.connection_budget import evaluate


def baseline():
    replicas = {
        "d-profile": 6, "d-social-discovery": 5, "d-review": 4,
        "d-notification": 4, "d-catalog": 3, "d-identity-trust": 4,
        "d-booking": 3, "d-taxonomy": 2, "d-operations": 2,
        "d-order-dispute": 2, "d-ecosystem-integration": 2,
    }
    profile = {
        "max_connections": 1000,
        "max_connections_confirmed": True,
        "topology_confirmed": True,
        "extras_confirmed": True,
        "boundaries": {name: {"max_replicas": count, "surge": "25%", "pool_instances": 1}
                       for name, count in replicas.items()},
        "extras": {"keycloak": 60, "migration": 22, "operations": 20, "reserved": 5},
    }
    pools = {name: 10 for name in replicas}
    candidates = {name: [f"{name}/src/shared/prisma.module.ts"] for name in replicas}
    return profile, list(replicas), pools, candidates


class ConnectionBudgetTest(unittest.TestCase):
    def test_reference_total_counts_surge_and_four_extra_groups(self):
        result = evaluate(*baseline())
        self.assertEqual(result["total"], 607)
        self.assertEqual(result["known_total"], 607)
        self.assertEqual(result["capacity"], 800)
        self.assertEqual(len(result["rows"]), 11)
        self.assertFalse(result["errors"])
        self.assertFalse(result["unconfirmed"])

    def test_more_pools_per_pod_can_exceed_cap_with_same_replicas(self):
        profile, targets, pools, candidates = baseline()
        profile["boundaries"]["d-profile"]["pool_instances"] = 4
        candidates["d-profile"] = [f"module-{i}.ts" for i in range(4)]
        result = evaluate(profile, targets, pools, candidates)
        self.assertEqual(result["total"], 847)
        self.assertEqual(result["capacity"], 800)
        self.assertTrue(result["errors"])

    def test_surge_absolute_pods_or_percent_rounded_up(self):
        for surge, expected in [(0, 30), (2, 50), ("0%", 30), ("25%", 40), ("100%", 60)]:
            with self.subTest(surge=surge):
                profile, _, _, _ = baseline()
                profile["boundaries"] = {"d-booking": {
                    "max_replicas": 3, "surge": surge, "pool_instances": 1}}
                profile["extras"] = dict.fromkeys(profile["extras"], 0)
                result = evaluate(profile, ["d-booking"], {"d-booking": 10}, {"d-booking": ["prisma.module.ts"]})
                self.assertEqual(result["total"], expected)
                self.assertFalse(result["errors"])

    def test_surge_rounded_per_boundary_before_sum(self):
        profile, _, _, _ = baseline()
        names = ["d-booking", "d-profile"]
        profile["boundaries"] = {name: {"max_replicas": 1, "surge": "25%", "pool_instances": 1}
                                 for name in names}
        profile["extras"] = dict.fromkeys(profile["extras"], 0)
        result = evaluate(profile, names, dict.fromkeys(names, 10),
                          {name: ["prisma.module.ts"] for name in names})
        self.assertEqual(result["total"], 40, "Hai boundary đều cần thêm một pod khi rollout")

    def test_new_boundary_without_budget_row_blocks(self):
        profile, targets, pools, candidates = baseline()
        targets.append("d-new")
        pools["d-new"] = 10
        candidates["d-new"] = ["d-new/prisma.module.ts"]
        result = evaluate(profile, targets, pools, candidates)
        self.assertTrue(result["errors"])
        self.assertIsNone(result["total"])

    def test_unknown_pool_or_extra_is_not_treated_as_zero(self):
        for kind in ["pool_absent", "pool_null", "extra_absent", "extra_null"]:
            with self.subTest(kind=kind):
                profile, targets, pools, candidates = baseline()
                if kind == "pool_absent":
                    del pools["d-profile"]
                elif kind == "pool_null":
                    pools["d-profile"] = None
                elif kind == "extra_absent":
                    del profile["extras"]["keycloak"]
                else:
                    profile["extras"]["keycloak"] = None
                if kind == "extra_absent":
                    with self.assertRaises(ValueError):
                        evaluate(profile, targets, pools, candidates)
                    continue
                result = evaluate(profile, targets, pools, candidates)
                self.assertTrue(result["errors"])
                self.assertIsNone(result["total"])
                self.assertIsInstance(result["known_total"], int)

    def test_unknown_max_connections_cannot_conclude_budget(self):
        profile, targets, pools, candidates = baseline()
        profile["max_connections"] = None
        result = evaluate(profile, targets, pools, candidates)
        self.assertIsNone(result["capacity"])
        self.assertTrue(result["errors"])

    def test_arithmetic_within_cap_but_unconfirmed_inputs_still_blocks(self):
        for flag in ["max_connections_confirmed", "topology_confirmed", "extras_confirmed"]:
            with self.subTest(flag=flag):
                profile, targets, pools, candidates = baseline()
                profile[flag] = False
                result = evaluate(profile, targets, pools, candidates)
                self.assertEqual(result["total"], 607)
                self.assertTrue(result["errors"])
                self.assertTrue(result["unconfirmed"])

    def test_missing_pool_evidence_or_more_pools_than_declared_blocks(self):
        for modules in [None, [], ["first.module.ts", "second.module.ts"]]:
            with self.subTest(modules=modules):
                profile, targets, pools, candidates = baseline()
                if modules is None:
                    del candidates["d-profile"]
                else:
                    candidates["d-profile"] = modules
                result = evaluate(profile, targets, pools, candidates)
                self.assertTrue(result["errors"])

    def test_bool_negative_and_float_not_coerced_into_budget(self):
        for field in ["max_connections", "max_replicas", "pool_instances", "pool", "extra", "surge"]:
            for value in [True, False, -1, 1.5]:
                with self.subTest(field=field, value=value):
                    profile, targets, pools, candidates = baseline()
                    if field == "max_connections":
                        profile[field] = value
                    elif field == "pool":
                        pools["d-profile"] = value
                    elif field == "extra":
                        profile["extras"]["keycloak"] = value
                    else:
                        profile["boundaries"]["d-profile"][field] = value
                    with self.assertRaises(ValueError):
                        evaluate(profile, targets, pools, candidates)


if __name__ == "__main__":
    unittest.main()
