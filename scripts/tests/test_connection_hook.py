"""Kiểm hook bằng subprocess giả: không gọi deploy thật."""
import importlib.util
import io
import tempfile
from pathlib import Path
from subprocess import CompletedProcess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('connection_guard', Path(__file__).parents[1] / 'guard_bc.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ConnectionHookTest(unittest.TestCase):
    def test_check_does_not_invoke_budget_or_deploy(self):
        with patch('sys.stdin', io.StringIO('{"tool_input":{"command":"make check"}}')), \
                patch.object(guard.subprocess, 'run') as run:
            self.assertEqual(guard.main(), 0)
            run.assert_not_called()

    def test_budget_status_and_target_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / 'scripts/check-connection-budget.py').touch()
            (root / 'context/perf').mkdir(parents=True, exist_ok=True)
            (root / 'context/perf/CONNECTION-BUDGET.json').write_text('{}')
            for code in (0, 1, 2):
                with self.subTest(code=code), patch.object(guard, 'ROOT', root), \
                        patch.object(guard.subprocess, 'run', return_value=CompletedProcess([], code, 'bằng chứng', '')) as run, \
                        patch('sys.stderr', io.StringIO()):
                    self.assertEqual(guard.connection_gate('make -C somewhere deploy ENV=UAT'), 0 if code == 0 else 2)
                    self.assertEqual(run.call_args.args[0][-2:], ['--env', 'UAT'])

    def test_dynamic_or_ambiguous_environment_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / 'scripts/check-connection-budget.py').touch()
            (root / 'context/perf').mkdir(parents=True, exist_ok=True)
            (root / 'context/perf/CONNECTION-BUDGET.json').write_text('{}')
            for command in ('make deploy ENV=$NEXT', 'ENV=SIT make deploy ENV=PROD'):
                with patch.object(guard, 'ROOT', root), patch.object(guard.subprocess, 'run') as run, patch('sys.stderr', io.StringIO()):
                    self.assertEqual(guard.connection_gate(command), 2)
                    run.assert_not_called()

    def test_ENV_matches_deploy_script_and_prod_cli(self):
        for command, expected in [('ENV=PT make deploy','PT'), ('make deploy ENV=UAT','UAT'),
                                  ('vercel --prod','PROD'), ('bash -lc "make deploy ENV=PT"','PT')]:
            self.assertEqual(guard.deployment_environment(command),expected)
        for command in ['make deploy env=PT', 'vercel --prod ENV=PT', 'ENV=PT make deploy; make deploy ENV=PROD']:
            with self.assertRaises(ValueError): guard.deployment_environment(command)

    def test_documentation_and_quoted_examples_do_not_invoke_deploy_gate(self):
        commands = ['echo "make deploy ENV=PROD"', "printf '%s' 'fly deploy'",
                    "cat <<'EOF' > note.md\nmake deploy ENV=PT\nEOF\npython3 check.py"]
        for command in commands:
            self.assertEqual(guard.deploy_commands(command),[])
        self.assertTrue(guard.deploy_commands("cat <<'EOF'\nexample\nEOF\nmake deploy ENV=PT"))
        self.assertTrue(guard.deploy_commands('true && make -C srcroot/foo deploy ENV=PT'))

    def test_perf_and_budget_use_same_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'scripts').mkdir()
            for name in ['check-connection-budget.py','check-perf-report.py']: (root/'scripts'/name).touch()
            (root/'context/perf').mkdir(parents=True)
            (root/'context/perf/CONNECTION-BUDGET.json').write_text('{}')
            (root/'context/perf/WORKLOAD.md').write_text('```json\n{"confirmed": true}\n```\n')
            with patch.object(guard,'ROOT',root), patch.object(guard.subprocess,'run',return_value=CompletedProcess([],0,'','')) as run:
                for gate in [guard.connection_gate,guard.perf_gate]:
                    self.assertEqual(gate('vercel --prod'),0)
                    self.assertEqual(run.call_args.args[0][-2:],['--env','PROD'])

    def test_executable_wrappers_and_environment_mutations_cannot_bypass(self):
        for command in ['env -i ENV=PROD make deploy', 'sudo -u app make deploy ENV=PROD',
                        'export ENV=PROD; make deploy', 'ENV=PROD; make deploy',
                        "bash <<'EOF'\nmake deploy ENV=PROD\nEOF",
                        'cat <<EOF\n$(make deploy ENV=PROD)\nEOF']:
            with self.subTest(command=command), self.assertRaises(ValueError): guard.deploy_commands(command)
        self.assertEqual(guard.deployment_environment('ENV=PROD command make deploy'),'PROD')
        self.assertEqual(guard.deployment_environment('env ENV=PROD make deploy'),'PROD')

    def test_shell_script_argument_does_not_execute_stdin_and_literal_is_not_heredoc(self):
        self.assertEqual(guard.deploy_commands("bash scripts/remote/ub 'node --input-type=module' <<'JS'\nconsole.log('make deploy')\nJS"),[])
        self.assertEqual(guard.deploy_commands("printf '%s' '<<EOF' 'make deploy'"),[])

    def test_remote_deploy_cannot_skip_local_gate_by_wrapping_command(self):
        for command in ['ssh pt-host "make deploy ENV=PROD"', 'bash scripts/remote/ub "make deploy ENV=PT"']:
            with self.assertRaises(ValueError): guard.deploy_commands(command)

    def test_skill_gates_skip_until_skill_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'scripts').mkdir()
            for name in ['check-connection-budget.py', 'check-perf-report.py']: (root/'scripts'/name).touch()
            (root/'context/perf').mkdir(parents=True)
            (root/'context/perf/WORKLOAD.md').write_text('```json\n{"confirmed": false}\n```\n')
            with patch.object(guard, 'ROOT', root), patch.object(guard.subprocess, 'run') as run:
                self.assertEqual(guard.connection_gate('make deploy ENV=SIT'), 0)
                self.assertEqual(guard.perf_gate('make deploy ENV=SIT'), 0)
                run.assert_not_called()

    def test_deploy_workflow_execute_is_deploy_with_its_env(self):
        self.assertEqual(guard.deployment_environment('python3 scripts/deploy_workflow.py execute --env uat'), 'UAT')
        self.assertEqual(guard.deployment_environment('python3 scripts/deploy_workflow.py execute --env=prod'), 'PROD')
        self.assertEqual(guard.deploy_commands('python3 scripts/deploy_workflow.py dry-run --env uat'), [])


if __name__ == '__main__':
    unittest.main()
