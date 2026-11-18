"""Gate trước deploy: probe, source cũ, ngưỡng chưa chốt hay workload đổi đều không mở được."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))
from lib.perf_report import gate, load_workload, source_hash, workload_hash  # noqa: E402
from perf_fixtures import evidence, workload  # noqa: E402


def accepted(wl):
    bundle = dict(evidence(), scope="full", success=True, environment="SIT", source_hash="same",
                  workload_hash=workload_hash(wl))
    for side in ("baseline", "current"):
        for run in bundle["regression"][side]:
            run["environment"] = "SIT"
            if side == "current":
                run["source_hash"] = "same"
    return bundle


class PerfGateTest(unittest.TestCase):
    def test_full_confirmed_current_bundle_passes(self):
        wl = workload()
        self.assertEqual(gate(accepted(wl), "same", "sit", wl), [])

    def test_probe_stale_source_or_wrong_env_cannot_pass(self):
        wl = workload()
        errors = gate(dict(scope="probe", success=True, source_hash="old", environment="PT"), "new", "SIT", wl)
        self.assertGreaterEqual(len(errors), 3)

    def test_unconfirmed_or_changed_workload_blocks(self):
        wl = workload()
        bundle = accepted(wl)
        wl["confirmed"] = False
        self.assertTrue(gate(bundle, "same", "SIT", wl))
        wl = workload()
        bundle = accepted(wl)
        wl["groups"]["R1"]["p95_ms"] = 1000
        self.assertTrue(any("WORKLOAD" in e for e in gate(bundle, "same", "SIT", wl)))

    def test_full_flag_does_not_replace_measurements(self):
        wl = workload()
        self.assertTrue(gate(dict(scope="full", success=True, source_hash="same", environment="SIT",
                                  workload_hash=workload_hash(wl)), "same", "SIT", wl))

    def test_workload_read_from_markdown_json_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "context/perf/WORKLOAD.md"
            path.parent.mkdir(parents=True)
            path.write_text('# x\n\n```json\n{"confirmed": false, "groups": {"R1": {"weight": 1, "p95_ms": 1}}}\n```\n')
            self.assertEqual(load_workload(tmp)["groups"]["R1"]["p95_ms"], 1)
            path.write_text("# x\n")
            with self.assertRaises(ValueError):
                load_workload(tmp)

    def test_template_workload_parses_and_is_unconfirmed(self):
        wl = load_workload(Path(__file__).parents[2])
        self.assertIs(wl["confirmed"], False)

    def test_source_hash_ignores_docs_but_tracks_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            (Path(tmp) / "context").mkdir()
            (Path(tmp) / "app.py").write_text("a = 1\n")
            (Path(tmp) / "context/PRD.md").write_text("v1\n")
            before = source_hash(tmp)
            (Path(tmp) / "context/PRD.md").write_text("v2\n")
            self.assertEqual(source_hash(tmp), before)
            (Path(tmp) / "app.py").write_text("a = 2\n")
            self.assertNotEqual(source_hash(tmp), before)


if __name__ == "__main__":
    unittest.main()
