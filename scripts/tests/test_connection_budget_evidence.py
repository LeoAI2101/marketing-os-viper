"""Bằng chứng phải gắn đúng instance/source; provider chưa giải được không được xanh."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from lib.connection_budget import provider_candidates, evaluate, source_hash, validate_observation
from test_connection_budget import baseline


class ProviderEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'srcroot/boundaries/d-demo/src'
        self.source.mkdir(parents=True)

    def write(self, name, source):
        (self.source / name).write_text(source)

    def check(self):
        return provider_candidates(self.root, ['d-demo'])

    def test_alias_import_reexport_and_subclass_found_in_any_file(self):
        self.write('renamed.ts', "import { PrismaService as Db } from './prisma'; @Module({providers: [Db]}) class A {}")
        self.write('barrel.ts', "export { PrismaService as SharedDb } from './prisma'")
        self.write('second.ts', '@Module({providers: [SharedDb]}) class B {}')
        self.write('subclass.ts', 'class CustomDb extends PrismaClient {} @Module({providers: [CustomDb]}) class C {}')
        result = self.check()
        self.assertEqual(len(result['d-demo']), 3)
        self.assertEqual(result.uncertainties['d-demo'], [])
        self.write('quoted.ts', '@Module({"providers": [PrismaService]}) class D {}')
        self.assertEqual(len(self.check()['d-demo']), 4)

    def test_dynamic_module_of_external_nest_package_creates_no_pool(self):
        self.write('app.module.ts', "import { ConfigModule } from '@nestjs/config'\n@Module({imports: [ConfigModule.forRoot({ isGlobal: true })], providers: [PrismaService]}) class App {}")
        self.assertEqual(self.check().uncertainties['d-demo'], [])
        self.write('app.module.ts', "import { ConfigModule } from './config'\n@Module({imports: [ConfigModule.forRoot()]}) class App {}")
        self.assertEqual(len(self.check().uncertainties['d-demo']), 1)
        self.write('app.module.ts', "import { DbModule } from '@acme/db'\n@Module({imports: [DbModule.forRoot()]}) class App {}")
        self.assertEqual(len(self.check().uncertainties['d-demo']), 1)

    def test_comments_and_strings_do_not_create_fake_providers(self):
        self.write('app.module.ts', '// providers: [PrismaService]\nconst example = "providers: [PrismaService]"; @Module({providers: [OtherService]}) class A {}')
        self.assertEqual(self.check()['d-demo'], [])

    def test_factory_spread_shorthand_dynamic_and_new_client_not_fake_green(self):
        cases = [
            '@Module({providers: [{provide: "DB", useFactory: () => buildDb()}]}) class A {}',
            '@Module({providers: [PrismaService, ...shared]}) class A {}',
            '@Module({providers: shared}) class A {}',
            'const providers = buildProviders(); @Module({providers}) class A {}',
            '@Module({providers: [buildDb()]}) class A {}',
            '@Module({providers: [flag ? DbA : DbB]}) class A {}',
            '@Module({imports: [CustomDbModule.register()]}) class A {}',
            'const client = new PrismaClient()',
            'const client = new db.PrismaClient()',
            '@Module({["providers"]: [PrismaService]}) class A {}',
            'import { PrismaClient as P } from "@prisma/client"; const client = new P()',
            '@Module({providers: [{provide: "DB", useExisting: PrismaService}]}) class A {}',
        ]
        self.write('known.module.ts', '@Module({providers: [PrismaService]}) class Known {}')
        for source in cases:
            with self.subTest(source=source):
                self.write('additional.ts', source)
                candidates = self.check()
                self.assertTrue(candidates.uncertainties['d-demo'])
                profile, _, _, _ = baseline()
                profile['boundaries'] = {'d-demo': {'max_replicas': 1, 'surge': 0, 'pool_instances': 10}}
                result = evaluate(profile, ['d-demo'], {'d-demo': 1}, candidates)
                self.assertTrue(any('chưa chứng minh' in error for error in result['errors']))

    def test_hash_independent_of_machine_path_but_changes_with_source(self):
        self.write('app.ts', 'class App {}')
        before = source_hash(self.root, ['d-demo'])
        with tempfile.TemporaryDirectory() as other:
            destination = Path(other) / 'srcroot/boundaries/d-demo/src'
            destination.mkdir(parents=True)
            (destination / 'app.ts').write_text('class App {}')
            self.assertEqual(before, source_hash(Path(other), ['d-demo']))
        self.write('app.ts', 'class App { db = new PrismaClient() }')
        self.assertNotEqual(before, source_hash(self.root, ['d-demo']))


class ObservationEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.expected = dict(host='pt-host', project_id='squad-project', db_instance_id='123456')
        self.observed = dict(self.expected, environment='PT', observed_at=self.now.isoformat(),
                             source_sha256='a' * 64, pool_limits={'d-demo': 5},
                             max_connections=100, keycloak_connections=None, reserved_connections=3)

    def errors(self, expected=None):
        return validate_observation(self.observed, 'PT', ['d-demo'],
                                    self.expected if expected is None else expected, 'a' * 64, self.now)

    def test_valid_snapshot_needs_independent_identity_and_source_hash(self):
        self.assertEqual(self.errors(), [])
        self.assertEqual(len(self.errors({})), 3)
        for field in ('host', 'project_id', 'db_instance_id'):
            with self.subTest(field=field):
                before = self.observed[field]
                self.observed[field] = 'máy/DB khác'
                self.assertTrue(any(field in error for error in self.errors()))
                self.observed[field] = before
        self.observed['source_sha256'] = 'b' * 64
        self.assertTrue(any('source_sha256' in error for error in self.errors()))

    def test_stale_snapshot_missing_identity_or_timezone_not_applied(self):
        for field in ('host', 'project_id', 'db_instance_id', 'source_sha256'):
            before = self.observed.pop(field)
            self.assertTrue(self.errors())
            self.observed[field] = before
        self.observed['observed_at'] = self.now.replace(tzinfo=None).isoformat()
        self.assertTrue(any('múi giờ' in error for error in self.errors()))
        for delta in (timedelta(days=-2), timedelta(seconds=1)):
            self.observed['observed_at'] = (self.now + delta).isoformat()
            self.assertTrue(any('quá hạn' in error for error in self.errors()))

    def test_idp_pool_not_inferred_from_default_and_bad_number_errors(self):
        self.assertIsNone(self.observed['keycloak_connections'])
        self.assertEqual(self.errors(), [])
        for field in ('keycloak_connections', 'reserved_connections', 'max_connections'):
            before = self.observed[field]
            for value in (-1, True, 1.5):
                self.observed[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.errors()
            self.observed[field] = before
        self.observed['pool_limits'] = {'other-target': 5}
        self.assertTrue(any('target' in error for error in self.errors()))


if __name__ == '__main__':
    unittest.main()
