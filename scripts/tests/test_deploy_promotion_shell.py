"""Entrypoint make deploy không bỏ qua proof hoặc gửi từng Jenkins job."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DeployPromotionShellTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'bin').mkdir()
        source = Path(__file__).resolve().parents[1] / 'deploy.sh'
        (self.root / 'scripts/deploy.sh').write_text(source.read_text())
        self.log = self.root / 'calls.jsonl'
        python = self.root / 'bin/python3'
        python.write_text(f'#!{sys.executable}\nimport json,os,sys\nwith open(os.environ["DP_TEST_CALLS"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n')
        python.chmod(0o755)

    def run_deploy(self, **overrides):
        env = {**os.environ, 'PATH': str(self.root / 'bin') + os.pathsep + os.environ['PATH'],
               'DP_TEST_CALLS': str(self.log), 'ENV': 'PT', 'TARGET': 'orders', 'CONFIRM': '0',
               'RUN': '', 'DEPLOY_PHASE': '', 'SKIP_TEST': '0', **overrides}
        result = subprocess.run(['bash', str(self.root / 'scripts/deploy.sh')], env=env,
                                capture_output=True, text=True, timeout=10)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result, calls

    def test_default_prepares_without_tooling_or_pod_flags(self):
        result, calls = self.run_deploy(AWS_PROFILE='fixture', REDIS_TOOLING='no', POD_EVIDENCE='x', REVIEW_NOTES='y')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0][:4], ['scripts/deploy_workflow.py', 'prepare', '--env', 'PT'])
        self.assertEqual(calls[0][4:], ['--profile', 'fixture', '--only', 'orders'])
        self.assertFalse(any('jenkins_build.py' in ' '.join(call) for call in calls))

    def test_existing_run_defaults_dry_run(self):
        result, calls = self.run_deploy(RUN='PT-20261008-01')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0][1], 'dry-run')

    def test_confirm_requires_a_reviewable_run(self):
        result, calls = self.run_deploy(CONFIRM='1')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, [])
        result, calls = self.run_deploy(CONFIRM='1', RUN='PT-20261008-01')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0][1], 'execute')

    def test_phase_flag_cannot_implicitly_execute(self):
        result, calls = self.run_deploy(DEPLOY_PHASE='execute', RUN='PT-20261008-01')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, [])

    def test_cannot_skip_checks(self):
        result, calls = self.run_deploy(SKIP_TEST='1', CONFIRM='1', RUN='PT-20261008-01')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, [])
