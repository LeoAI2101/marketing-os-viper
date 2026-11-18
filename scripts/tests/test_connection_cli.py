"""CLI thật trên repo fixture, không chạm DB hay môi trường triển khai."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[2]
# release_fixtures (test deploy) đặt VIPER_OPS_ROOT cho cả tiến trình — CLI con phải thấy repo fixture CỦA MÌNH.
CHILD_ENV = {k: v for k, v in os.environ.items() if k != 'VIPER_OPS_ROOT'}
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.connection_budget import source_hash


class ConnectionCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / '.local/sm').mkdir(parents=True)
        for name in ('check-connection-budget.py', 'guard_bc.py', 'lib/connection_budget.py', 'lib/ops_env.py'):
            target = self.root / 'scripts' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / 'scripts' / name, target)
        boundary = self.root / 'srcroot/boundaries/d-demo'
        (boundary / 'src').mkdir(parents=True)
        (boundary / 'prisma').mkdir()
        (boundary / 'package.json').write_text('{}')
        (boundary / 'prisma/schema.prisma').write_text('model Demo {}')
        (boundary / 'src/app.module.ts').write_text('@Module({providers: [PrismaService]}) class App {}')
        self.profile = dict(max_connections=100, max_connections_confirmed=True,
                            topology_confirmed=True, extras_confirmed=True,
                            boundaries={'d-demo': dict(max_replicas=1, surge=0, pool_instances=1)},
                            extras=dict(keycloak=0, migration=0, operations=0, reserved=3))
        (self.root / 'context/perf').mkdir(parents=True)
        (self.root / '.local/sm/sm_configs.sit.py').write_text("SM_CONFIGS={'d-demo':{'DB_CONNECTION_LIMIT':'5'}}\nraise Exception('MUST NOT EXECUTE')")
        self.save()

    def save(self):
        (self.root / 'context/perf/CONNECTION-BUDGET.json').write_text(json.dumps({'environments': {'SIT': self.profile, 'PT': self.profile}}))

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, 'scripts/check-connection-budget.py', *args],
                              cwd=self.root, capture_output=True, text=True, env=CHILD_ENV)

    def test_cli_math_and_literal_only_config(self):
        result = self.cli('--env', 'SIT', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['total'], 8)

    def test_cli_missing_config_and_over_budget_are_red(self):
        (self.root / '.local/sm/sm_configs.sit.py').unlink()
        self.assertEqual(self.cli().returncode, 1)
        (self.root / '.local/sm/sm_configs.sit.py').write_text("SM_CONFIGS={'d-demo':{'DB_CONNECTION_LIMIT':'100'}}")
        self.assertEqual(self.cli().returncode, 1)

    def test_cli_invalid_numbers_do_not_print_other_config_fields(self):
        (self.root / '.local/sm/sm_configs.sit.py').write_text("SM_CONFIGS={'d-demo':{'DB_CONNECTION_LIMIT':True,'DB_PASSWORD':'SECRET_SENTINEL'}}")
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('SECRET_SENTINEL', result.stdout + result.stderr)

    def test_expired_snapshot_cannot_grant_green(self):
        observed = dict(environment='PT', observed_at=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),
                        pool_limits={'d-demo': 5}, max_connections=100,
                        keycloak_connections=0, reserved_connections=3)
        (self.root / 'snapshot.json').write_text(json.dumps(observed))
        result = self.cli('--env', 'PT', '--observation', 'snapshot.json', '--json')
        self.assertEqual(result.returncode, 1)
        self.assertTrue(any('quá hạn' in e for e in json.loads(result.stdout)['errors']))

    def test_snapshot_same_env_missing_identity_or_wrong_instance_not_green(self):
        observed = dict(environment='PT', observed_at=datetime.now(timezone.utc).isoformat(),
                        host='pt-machine', project_id='squad-project', db_instance_id='12345',
                        source_sha256=source_hash(self.root, ['d-demo']),
                        pool_limits={'d-demo': 5}, max_connections=100,
                        keycloak_connections=0, reserved_connections=3)
        snapshot = self.root / 'snapshot.json'
        snapshot.write_text(json.dumps(observed))
        arguments = ['--env', 'PT', '--observation', 'snapshot.json', '--json']
        result = self.cli(*arguments)
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(json.loads(result.stdout)['total'])
        identity = ['--expected-host', 'pt-machine', '--expected-project', 'squad-project',
                    '--expected-db-instance', '12345']
        result = self.cli(*arguments, *identity)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['total'], 8)
        observed['db_instance_id'] = 'other-cluster'
        snapshot.write_text(json.dumps(observed))
        self.assertEqual(self.cli(*arguments, *identity).returncode, 1)
        observed['db_instance_id'] = '12345'
        snapshot.write_text(json.dumps(observed))
        (self.root / 'srcroot/boundaries/d-demo/src/new.ts').write_text('class NewSource {}')
        result = self.cli(*arguments, *identity)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(any('source_sha256' in error for error in json.loads(result.stdout)['errors']))

    def test_factory_cannot_hide_behind_known_provider_module(self):
        path = self.root / 'srcroot/boundaries/d-demo/src/factory.ts'
        path.write_text('@Module({providers: [{provide: "db", useFactory: () => buildDb()}]}) class Extra {}')
        result = self.cli('--json')
        self.assertEqual(result.returncode, 1)
        self.assertTrue(any('chưa chứng minh' in error for error in json.loads(result.stdout)['errors']))

    def test_real_hook_calls_checker_without_executing_deploy(self):
        payload = json.dumps({'tool_input': {'command': 'make deploy ENV=SIT'}})
        def hook():
            return subprocess.run([sys.executable, 'scripts/guard_bc.py'], cwd=self.root,
                                  input=payload, capture_output=True, text=True, env=CHILD_ENV).returncode
        self.assertEqual(hook(), 0)
        self.profile['topology_confirmed'] = False
        self.save()
        self.assertEqual(hook(), 2)
