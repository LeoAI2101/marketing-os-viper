"""Checklist xanh không được che checker lỗi; không thực hiện deploy trong test."""
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("guard_bc", Path(__file__).parents[1] / "guard_bc.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ContractGateTest(unittest.TestCase):
    def run_gate(self, checker_code):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "context/perf").mkdir(parents=True)
            (root / "STATE.md").write_text("Vòng : 10")
            (root / "context/BACKWARD-COMPATIBILITY-CHECKLIST.md").write_text("## 3. Rà\n- [x] Đã rà\n")
            (root / "context/perf/API-CONTRACT-BASE.json").write_text(json.dumps({"base": "a" * 40}))
            payload = io.StringIO(json.dumps({"tool_input": {"command": "make deploy"}}))
            completed = CompletedProcess([], checker_code, "", "hợp đồng đã mất trường")
            with patch.object(guard, "ROOT", root), patch("sys.stdin", payload), \
                    patch.object(guard.subprocess, "run", return_value=completed) as run, redirect_stderr(io.StringIO()):
                result = guard.main()
                self.assertEqual(run.call_args.args[0][-2:], ["--base", "a" * 40])
                return result

    def test_green_checklist_still_blocks_breaking(self):
        self.assertEqual(self.run_gate(1), 2)

    def test_missing_input_does_not_pass(self):
        self.assertEqual(self.run_gate(2), 2)

    def test_compatible_contract_passes(self):
        self.assertEqual(self.run_gate(0), 0)


if __name__ == "__main__":
    unittest.main()
