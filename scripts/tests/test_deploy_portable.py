"""Bộ deploy trên hai layout, AWS/SM/Jenkins đều giả lập, không gọi mạng."""
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / 'lib')]
import release_fixtures  # noqa: F401,E402 — dựng repo mẫu + VIPER_OPS_ROOT trước khi import tool
import aws_session
import release_ecr
import secrets_manager
from deploy_manifest import PLATFORM, TOOLING, read_manifest, targets, platform_contract
from deploy_secrets import snapshot
from env_inventory import scan_text
from env_audit import audit, source_hash
from jenkins_approval import check_inputs
from ops_env import aws_env_cfg
from jenkins_parallel import run_parallel
from private_config import declarations, private_write, pull


class PortableTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        (self.root / '.gitignore').write_text('.local/\n')
        (self.root / 'deployment').mkdir()
        (self.root / '.local/templates').mkdir(parents=True)
        (self.root / '.local/sm').mkdir()
        self.schema = self.root / '.local/templates/sm_configs.example.py'
        self.schema.write_text("SM_CONFIGS={'orders':{'TOKEN':''}}\nMUST_MATCH=[]\n")
        self.file = self.root / '.local/sm/sm_configs.uat.py'
        self.path = self.root / 'apps/orders'
        self.path.mkdir(parents=True)
        (self.path / 'package.json').write_text('{}')
        self.manifest = {'version': 1, 'targets': {'orders': {'path': 'apps/orders', 'group': 'boundaries', 'service': 'order-service'}},
                         'schema': '.local/templates/sm_configs.example.py', 'env_helpers': ['requireEnv'],
                         'derived_env': [], 'environments': {}}
        (self.root / 'deployment/deploy-manifest.json').write_text(json.dumps(self.manifest))

    def tearDown(self):
        self.temp.cleanup()

    def test_two_layouts_use_manifest_without_names(self):
        self.assertEqual(targets(self.root, self.manifest)['orders']['service'], 'order-service')
        self.manifest.pop('targets')
        self.manifest['discovery'] = {'boundaries': 'apps/*'}
        self.manifest['mapping_file'] = 'deployment/map.tsv'
        (self.root / 'deployment/map.tsv').write_text('apps/orders group/order-service\n')
        self.assertEqual(targets(self.root, self.manifest)['orders']['path'], 'apps/orders')

    def test_pull_all_targets_preserves_extra_and_local(self):
        self.file.write_text("SM_CONFIGS={'orders':{'TOKEN':'local'}}\n")
        sm = Mock()
        sm.get_secret_value.return_value = {'VersionId': 'v1', 'SecretString': json.dumps({'TOKEN': 'remote', 'EXTRA': 'keep'})}
        report = pull(sm, {}, declarations(self.schema), {'orders': {}}, self.file, lambda c, t: 'uat/' + t, self.root)
        values, _ = declarations(self.file)
        self.assertEqual(values['orders'], {'TOKEN': 'local', 'EXTRA': 'keep'})
        self.assertEqual(report['conflicts'], [{'target': 'orders', 'key': 'TOKEN'}])
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)

    def test_secret_shell_without_value_is_empty_payload_but_missing_shell_fails(self):
        class NotFound(Exception):
            response = {'Error': {'Code': 'ResourceNotFoundException'}}
        sm = Mock()
        sm.get_secret_value.side_effect = NotFound()
        report = pull(sm, {}, declarations(self.schema), {'orders': {}}, self.file, lambda c, t: t, self.root)
        self.assertEqual(report['versions'], {'orders': None})
        self.assertEqual(declarations(self.file)[0]['orders'], {'TOKEN': ''})
        self.assertEqual(snapshot(sm, {}, 'UAT', {'orders': {'TOKEN': 'new'}}, lambda c, t, e: t)['orders'],
                         {'name': 'orders', 'version': None, 'added': ['TOKEN'], 'changed': []})
        sm.describe_secret.side_effect = NotFound()
        self.file.unlink()
        with self.assertRaises(NotFound):
            pull(sm, {}, declarations(self.schema), {'orders': {}}, self.file, lambda c, t: t, self.root)
        self.assertFalse(self.file.exists())

    def test_missing_secret_leaves_no_partial_file(self):
        sm = Mock()
        sm.get_secret_value.side_effect = RuntimeError('denied')
        with self.assertRaises(RuntimeError):
            pull(sm, {}, declarations(self.schema), {'orders': {}}, self.file, lambda c, t: t, self.root)
        self.assertFalse(self.file.exists())

    def test_first_init_from_code_leaves_new_keys_blank(self):
        (self.path / 'index.js').write_text("const port=process.env.PORT || '3000';\nconst token=requireEnv('TOKEN');\nprocess.env.REDIS_TOOLING_HOST;")
        with patch.object(secrets_manager, 'ROOT', self.root), patch.object(secrets_manager, 'EXAMPLE_FILE', self.schema), redirect_stdout(io.StringIO()):
            secrets_manager.cmd_init({}, SimpleNamespace(env='UAT'))
        values, _ = declarations(self.file)
        # Mặc định của code KHÔNG vào file: chỉ giá trị người điền mới lên SM.
        self.assertEqual(values['orders']['PORT'], '')
        self.assertEqual(values['orders']['TOKEN'], '')
        self.assertTrue(set(PLATFORM).issubset(values['orders']))
        self.assertIn('REDIS_TOOLING_HOST', values['orders'])

    def test_required_read_cannot_borrow_other_default(self):
        source = "const password=requireEnv('PASSWORD'); const port=process.env.PORT || '3000';"
        found, _ = scan_text(source, 'index.js', ['requireEnv'])
        self.assertTrue(found['PASSWORD']['required'])
        self.assertEqual(found['PASSWORD']['defaults'], [])
        self.assertEqual(found['PORT']['defaults'], ['3000'])

    def test_dynamic_python_and_destructured_node_are_not_silent(self):
        found, unknown = scan_text('value=os.environ[name]', 'main.py', [])
        self.assertTrue(unknown)
        found, _ = scan_text('const { DB_HOST, REDIS_HOST }=process.env;', 'index.js', [])
        self.assertEqual(set(found), {'DB_HOST', 'REDIS_HOST'})

    def test_required_read_is_not_masked_by_optional(self):
        self.file.write_text("SM_CONFIGS={'orders':{'TOKEN':''}}\n")
        (self.path / 'index.js').write_text("requireEnv('TOKEN');\nconst token=process.env.TOKEN || 'sample';")
        report = audit(self.root, 'UAT', add_missing=True)
        self.assertTrue(any('TOKEN chưa có giá trị' in w for w in report['warnings']))
        self.assertEqual(declarations(self.file)[0]['orders']['TOKEN'], '')

    def test_javascript_changes_fingerprint(self):
        file = self.path / 'index.js'
        file.write_text('process.env.TOKEN')
        entries = targets(self.root, self.manifest)
        old = source_hash(self.root, entries)
        file.write_text('process.env.EXTRA_TOKEN')
        self.assertNotEqual(old, source_hash(self.root, entries))

    def test_inject_keys_are_trusted_by_declaration(self):
        self.file.write_text("SM_CONFIGS={'orders':{'TOKEN':'ok'}}\n")
        (self.path / 'index.js').write_text('process.env.DB_HOST; process.env.REDIS_TOOLING_HOST')
        report = audit(self.root, 'UAT', add_missing=True)
        self.assertEqual(report['warnings'], [])
        self.assertEqual({r['key']: r['kind'] for r in report['rows'] if r['key'] != 'TOKEN'},
                         {'DB_HOST': 'inject', 'REDIS_TOOLING_HOST': 'inject'})
        self.assertEqual(report['added'], [])

    def test_value_findings_are_warnings_and_missing_code_key_gets_blank_line(self):
        self.file.write_text("SM_CONFIGS={'orders':{'TOKEN':'<điền>'}}\n")
        (self.path / 'index.js').write_text("requireEnv('TOKEN'); requireEnv('NEW_KEY'); const x=process.env[name];")
        report = audit(self.root, 'UAT', add_missing=True)
        self.assertEqual(report['added'], ['orders:NEW_KEY'])
        self.assertEqual(declarations(self.file)[0]['orders'], {'TOKEN': '<điền>', 'NEW_KEY': ''})
        text = ' | '.join(report['warnings'])
        for expected in ('TOKEN còn placeholder', 'NEW_KEY chưa có giá trị', 'truy cập env động'):
            self.assertIn(expected, text)
        self.assertNotIn('errors', report)

    def test_aws_old_shell_keys_do_not_override_refreshed_file(self):
        cfg = dict(AWS_ACCESS_KEY_ID='new', AWS_SECRET_ACCESS_KEY='new-secret', AWS_SESSION_TOKEN='new-session', AWS_DEFAULT_REGION='test-region')
        with patch.dict(os.environ, {k: 'stale' for k in aws_session.KEYS}), patch.object(secrets_manager, 'load_env_file', return_value=cfg):
            secrets_manager.load_cfg()
            self.assertEqual(os.environ['AWS_ACCESS_KEY_ID'], 'new')

    def test_aws_capture_and_preserve_other_settings(self):
        file = self.root / '.local/env/.env.aws'
        private_write(file, 'AWS_DEFAULT_REGION=ap-southeast-1\nSM_NAME_TEMPLATE={env}/{target}\n')
        runner = Mock(side_effect=[SimpleNamespace(), SimpleNamespace(stdout=json.dumps({'AccessKeyId': 'key', 'SecretAccessKey': 'secret', 'SessionToken': 'session'})), SimpleNamespace(stdout='{"Account":"123"}')])
        output = io.StringIO()
        with redirect_stdout(output):
            aws_session.refresh(self.root, 'fixture', 'login', runner)
        self.assertNotIn('secret', output.getvalue())
        self.assertIn('SM_NAME_TEMPLATE={env}/{target}', file.read_text())
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertTrue(runner.call_args_list[1].kwargs['capture_output'])

    def test_sso_declared_in_env_file_uses_generated_cli_config(self):
        file = self.root / '.local/env/.env.aws'
        sso = ('AWS_DEFAULT_REGION=ap-southeast-1\nAWS_SSO_START_URL=https://fixture.awsapps.com/start\nAWS_SSO_REGION=ap-southeast-1\n'
               'AWS_SSO_ACCOUNT_ID=123456789012\nAWS_SSO_ROLE_NAME=Developer\n')
        private_write(file, sso)
        exported = SimpleNamespace(returncode=0, stdout=json.dumps({'AccessKeyId': 'key', 'SecretAccessKey': 'secret', 'SessionToken': 'session'}))
        identity = SimpleNamespace(stdout='{"Account":"123456789012"}')
        # Phiên SSO còn hạn: không gọi `aws sso login`.
        runner = Mock(side_effect=[exported, exported, identity])
        with redirect_stdout(io.StringIO()):
            aws_session.refresh(self.root, 'default', 'auto', runner)
        config = self.root / '.local/env/.env.aws.cli-config'
        self.assertIn('[default]\nsso_start_url = https://fixture.awsapps.com/start', config.read_text())
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertFalse(any('login' in call.args[0] for call in runner.call_args_list))
        self.assertEqual(runner.call_args_list[0].kwargs['env']['AWS_CONFIG_FILE'], str(config))
        # Phiên hết hạn: đăng nhập bằng mã thiết bị, không mở trình duyệt tại chỗ.
        runner = Mock(side_effect=[SimpleNamespace(returncode=255, stdout=''), SimpleNamespace(), exported, identity])
        with redirect_stdout(io.StringIO()):
            aws_session.refresh(self.root, 'default', 'auto', runner)
        self.assertEqual(runner.call_args_list[1].args[0], ['aws', 'sso', 'login', '--profile', 'default', '--no-browser'])
        self.assertNotIn('capture_output', runner.call_args_list[1].kwargs)
        # Khai thiếu thì dừng trước khi gọi AWS CLI.
        private_write(file, 'AWS_SSO_START_URL=https://fixture.awsapps.com/start\n')
        runner = Mock()
        with self.assertRaisesRegex(ValueError, 'AWS_SSO_REGION'):
            aws_session.refresh(self.root, 'default', 'auto', runner)
        runner.assert_not_called()

    # --- Đa account: PREPROD là account AWS riêng, khóa nằm ở *_PREPROD trong cùng .env.aws ---
    SSO_BASE = ('AWS_DEFAULT_REGION=ap-southeast-1\nAWS_SSO_START_URL=https://fixture.awsapps.com/start\nAWS_SSO_REGION=us-east-1\n'
                'AWS_SSO_ACCOUNT_ID=111111111111\nAWS_SSO_ROLE_NAME=Developer\n'
                'AWS_ACCESS_KEY_ID=base-key\nAWS_SECRET_ACCESS_KEY=base-secret\nAWS_SESSION_TOKEN=base-token\n')

    def test_env_cfg_own_account_never_falls_back_to_shared_keys(self):
        raw = aws_session.env_values(self.SSO_BASE + 'AWS_SSO_ACCOUNT_ID_PREPROD=222222222222\n')
        pre = aws_env_cfg(raw, 'PREPROD')
        self.assertEqual(pre['AWS_SSO_ACCOUNT_ID'], '222222222222')
        self.assertEqual(pre['AWS_SSO_START_URL'], 'https://fixture.awsapps.com/start')  # không-bí-mật: kế thừa
        for key in aws_session.KEYS:
            self.assertEqual(pre[key], '')  # chưa khai *_PREPROD thì trống, không mượn khóa nonprod
        raw.update(AWS_ACCESS_KEY_ID_PREPROD='pre-key', AWS_SECRET_ACCESS_KEY_PREPROD='pre-secret')
        pre = aws_env_cfg(raw, 'PREPROD')
        self.assertEqual((pre['AWS_ACCESS_KEY_ID'], pre['AWS_SECRET_ACCESS_KEY'], pre['AWS_SESSION_TOKEN']), ('pre-key', 'pre-secret', ''))
        uat = aws_env_cfg(raw, 'UAT')
        self.assertEqual((uat['AWS_ACCESS_KEY_ID'], uat['AWS_SSO_ACCOUNT_ID']), ('base-key', '111111111111'))
        self.assertEqual(aws_env_cfg(raw, None)['AWS_SSO_ACCOUNT_ID'], '111111111111')

    def test_load_cfg_for_own_account_env_stops_without_its_keys_and_does_not_use_shared(self):
        raw = aws_session.env_values(self.SSO_BASE + 'AWS_SSO_ACCOUNT_ID_PREPROD=222222222222\n')
        with patch.object(secrets_manager, 'load_env_file', return_value=raw), patch.dict(os.environ, {}, clear=False):
            with self.assertRaises(SystemExit) as stop:
                secrets_manager.load_cfg('PREPROD')
            self.assertIn('AWS_ACCESS_KEY_ID_PREPROD', str(stop.exception))
            self.assertIn('--env PREPROD', str(stop.exception))
            raw.update(AWS_ACCESS_KEY_ID_PREPROD='pre-key', AWS_SECRET_ACCESS_KEY_PREPROD='pre-secret', AWS_SESSION_TOKEN_PREPROD='pre-token')
            cfg = secrets_manager.load_cfg('PREPROD')
            self.assertEqual((cfg['AWS_ACCESS_KEY_ID'], os.environ['AWS_ACCESS_KEY_ID'], os.environ['AWS_SESSION_TOKEN']), ('pre-key', 'pre-key', 'pre-token'))
            self.assertEqual(secrets_manager.load_cfg('UAT')['AWS_ACCESS_KEY_ID'], 'base-key')
            self.assertEqual(secrets_manager.load_cfg()['AWS_ACCESS_KEY_ID'], 'base-key')

    def test_client_uses_the_keys_of_its_cfg_not_the_shell(self):
        cfg = dict(AWS_ACCESS_KEY_ID='pre-key', AWS_SECRET_ACCESS_KEY='pre-secret', AWS_SESSION_TOKEN='pre-token', AWS_DEFAULT_REGION='ap-southeast-1')
        with patch.dict(os.environ, {'AWS_ACCESS_KEY_ID': 'shell-key'}), patch.object(secrets_manager.boto3, 'client') as make:
            secrets_manager.client(cfg)
        make.assert_called_once_with('secretsmanager', region_name='ap-southeast-1', aws_access_key_id='pre-key',
                                     aws_secret_access_key='pre-secret', aws_session_token='pre-token')

    def test_refresh_own_account_env_writes_only_suffixed_keys_and_own_cli_config(self):
        file = self.root / '.local/env/.env.aws'
        private_write(file, self.SSO_BASE + 'AWS_SSO_ACCOUNT_ID_PREPROD=222222222222\n')
        keys = SimpleNamespace(returncode=0, stdout=json.dumps({'AccessKeyId': 'pre-key', 'SecretAccessKey': 'pre-secret', 'SessionToken': 'pre-token'}))
        runner = Mock(side_effect=[keys, keys, SimpleNamespace(stdout='{"Account":"222222222222"}')])
        output = io.StringIO()
        with redirect_stdout(output):
            aws_session.refresh(self.root, 'default', 'auto', runner, env='PREPROD')
        text = file.read_text()
        for line in ('AWS_ACCESS_KEY_ID=base-key', 'AWS_SECRET_ACCESS_KEY=base-secret', 'AWS_SESSION_TOKEN=base-token',
                     'AWS_ACCESS_KEY_ID_PREPROD=pre-key', 'AWS_SECRET_ACCESS_KEY_PREPROD=pre-secret', 'AWS_SESSION_TOKEN_PREPROD=pre-token'):
            self.assertIn(line + '\n', text)
        self.assertIn('222222222222 (PREPROD)', output.getvalue())
        self.assertNotIn('pre-secret', output.getvalue())
        own = self.root / '.local/env/.env.aws.preprod.cli-config'
        self.assertIn('sso_account_id = 222222222222', own.read_text())
        self.assertFalse((self.root / '.local/env/.env.aws.cli-config').exists())  # bản của account chung không bị đụng
        self.assertEqual(runner.call_args_list[0].kwargs['env']['AWS_CONFIG_FILE'], str(own))
        # Lượt làm mới tiếp theo thay đúng ba dòng hậu tố, không nhân đôi.
        runner = Mock(side_effect=[keys, keys, SimpleNamespace(stdout='{"Account":"222222222222"}')])
        with redirect_stdout(io.StringIO()):
            aws_session.refresh(self.root, 'default', 'auto', runner, env='PREPROD')
        self.assertEqual(file.read_text().count('AWS_ACCESS_KEY_ID_PREPROD='), 1)

    def test_refresh_refuses_to_write_when_sts_account_differs_from_declared(self):
        file = self.root / '.local/env/.env.aws'
        private_write(file, self.SSO_BASE + 'AWS_SSO_ACCOUNT_ID_PREPROD=222222222222\n')
        before = file.read_text()
        keys = SimpleNamespace(returncode=0, stdout=json.dumps({'AccessKeyId': 'wrong-key', 'SecretAccessKey': 'wrong-secret', 'SessionToken': 'wrong-token'}))
        runner = Mock(side_effect=[keys, keys, SimpleNamespace(stdout='{"Account":"111111111111"}')])
        with self.assertRaisesRegex(ValueError, r'111111111111.*222222222222'):
            aws_session.refresh(self.root, 'default', 'auto', runner, env='PREPROD')
        self.assertEqual(file.read_text(), before)

    def test_refresh_for_env_without_own_account_still_updates_shared_keys(self):
        file = self.root / '.local/env/.env.aws'
        private_write(file, self.SSO_BASE + 'AWS_SSO_ACCOUNT_ID_PREPROD=222222222222\n')
        keys = SimpleNamespace(returncode=0, stdout=json.dumps({'AccessKeyId': 'new-key', 'SecretAccessKey': 'new-secret', 'SessionToken': 'new-token'}))
        runner = Mock(side_effect=[keys, keys, SimpleNamespace(stdout='{"Account":"111111111111"}')])
        with redirect_stdout(io.StringIO()):
            aws_session.refresh(self.root, 'default', 'auto', runner, env='UAT')
        self.assertIn('AWS_ACCESS_KEY_ID=new-key\n', file.read_text())
        self.assertFalse((self.root / '.local/env/.env.aws.uat.cli-config').exists())

    def test_ecr_config_own_account_env_ignores_shell_aws_keys(self):
        raw = aws_session.env_values(self.SSO_BASE + 'AWS_SSO_ACCOUNT_ID_PREPROD=222222222222\nAWS_ACCESS_KEY_ID_PREPROD=pre-key\nAWS_SECRET_ACCESS_KEY_PREPROD=pre-secret\nAWS_DEFAULT_REGION=ap-southeast-1\nECR_REGISTRY_ID=000000000000\nECR_REPOSITORY_TEMPLATE={env}/sample/{service}\n')
        shell = {'AWS_ACCESS_KEY_ID': 'shell-key', 'AWS_SECRET_ACCESS_KEY': 'shell-secret', 'AWS_SESSION_TOKEN': 'shell-token'}
        with patch.object(release_ecr, 'load_env_file', return_value=raw), patch.object(release_ecr, 'ENV_DIR', self.root / '.local/env'), patch.dict(os.environ, shell):
            private_write(self.root / '.local/env/.env.aws', '')
            pre, uat = release_ecr.ecr_config('PREPROD'), release_ecr.ecr_config('UAT')
        self.assertEqual((pre['AWS_ACCESS_KEY_ID'], pre['AWS_SESSION_TOKEN']), ('pre-key', ''))
        self.assertEqual(uat['AWS_ACCESS_KEY_ID'], 'shell-key')  # env dùng khóa chung: giữ quy tắc cũ, shell thắng

    def test_sso_declared_follows_env(self):
        private_write(self.root / '.local/env/.env.aws', 'AWS_SSO_START_URL_PREPROD=https://fixture.awsapps.com/start\n')
        flow = __import__('deploy_workflow')
        self.assertTrue(flow.sso_declared(self.root, 'PREPROD'))
        self.assertFalse(flow.sso_declared(self.root, 'UAT'))

    def test_secret_merge_keeps_extra_and_excludes_platform(self):
        sm = Mock()
        payload = {'TOKEN': 'old', 'EXTRA': 'keep', 'DB_HOST': 'platform'}
        sm.get_secret_value.return_value = {'VersionId': 'v1', 'SecretString': json.dumps(payload)}
        values = {'orders': {'TOKEN': 'new', 'DB_HOST': 'do-not-push'}}
        plan = snapshot(sm, {}, 'UAT', values, lambda c, t, e: t)
        self.assertEqual(plan['orders']['changed'], ['TOKEN'])
        sm.describe_secret.return_value = {'VersionIdsToStages': {'v1': ['AWSCURRENT']}}
        with patch.object(secrets_manager, 'client', return_value=sm), patch.object(secrets_manager, 'load_values', return_value=values), patch.object(secrets_manager, 'secret_name', side_effect=lambda c, t, e: t), redirect_stdout(io.StringIO()):
            secrets_manager.cmd_push({}, SimpleNamespace(env='UAT', target=None, only_new=False, yes=True))
        sent = json.loads(sm.put_secret_value.call_args.kwargs['SecretString'])
        self.assertEqual(sent['EXTRA'], 'keep')
        self.assertEqual(sent['DB_HOST'], 'platform')


    def test_inject_comment_in_one_service_is_global_and_survives_rewrites(self):
        self.schema.write_text("SM_CONFIGS = {\n 'orders': {\n  'SHARED_HOST': '', # DevOps inject\n  'REDIS_TOOLING_HOST': '', # DevOps inject\n  'TOKEN': '',\n },\n}\nMUST_MATCH=[]\n")
        other = self.root / 'apps/payments'
        other.mkdir()
        (other / 'package.json').write_text('{}')
        self.manifest['targets']['payments'] = {'path': 'apps/payments', 'group': 'boundaries', 'service': 'payments'}
        (self.root / 'deployment/deploy-manifest.json').write_text(json.dumps(self.manifest))
        with patch.object(secrets_manager, 'ROOT', self.root), redirect_stdout(io.StringIO()):
            secrets_manager.cmd_init({}, SimpleNamespace(env='UAT'))
        values, _ = declarations(self.file)
        self.assertIn('SHARED_HOST', values['payments'])
        self.assertEqual(platform_contract(self.root), ('SHARED_HOST',))
        self.assertNotIn('REDIS_TOOLING_HOST', values['payments'])
        from private_config import write_sm
        write_sm(self.file, values, ())
        self.assertEqual(platform_contract(self.root), ('SHARED_HOST',))
        self.assertIn('DevOps inject global mọi pod', self.file.read_text())

    def test_custom_schema_is_used_by_code_reconcile(self):
        custom = self.root / 'deployment/schema.py'
        custom.write_text("SM_CONFIGS={'orders':{'TOKEN':''}}\nMUST_MATCH=[]\n")
        self.manifest['schema'] = 'deployment/schema.py'
        (self.root / 'deployment/deploy-manifest.json').write_text(json.dumps(self.manifest))
        (self.path / 'index.js').write_text("const port=process.env.PORT || '3000';")
        with patch.object(secrets_manager, 'ROOT', self.root), patch.object(secrets_manager, 'EXAMPLE_FILE', self.schema), redirect_stdout(io.StringIO()):
            secrets_manager.cmd_init({}, SimpleNamespace(env='UAT'))
        self.assertIn('PORT', declarations(custom)[0]['orders'])
        self.assertNotIn('PORT', declarations(self.schema)[0]['orders'])

    def test_build_env_outside_target_changes_fingerprint(self):
        import deploy_workflow as flow
        self.manifest['build_env'] = {'boundaries': 'deployment/build.{env}.json'}
        (self.root / 'deployment/deploy-manifest.json').write_text(json.dumps(self.manifest))
        file = self.root / 'deployment/build.uat.json'
        file.write_text('{"VITE_URL":"first"}')
        with patch.object(flow, 'ROOT', self.root):
            before = flow.fingerprint('UAT', targets(self.root, self.manifest))
            file.write_text('{"VITE_URL":"second"}')
            self.assertNotEqual(before, flow.fingerprint('UAT', targets(self.root, self.manifest)))

    def test_prepare_needs_profile_unless_sso_declared_in_env_file(self):
        import deploy_workflow as flow
        args = SimpleNamespace(env='UAT', profile=None)
        with patch.object(flow, 'ROOT', self.root), patch.object(flow, 'command') as runner:
            with self.assertRaisesRegex(ValueError, '--profile'):
                flow.prepare(args, self.manifest, targets(self.root, self.manifest))
            runner.assert_not_called()
            private_write(self.root / '.local/env/.env.aws', 'AWS_SSO_START_URL=https://fixture.awsapps.com/start\n')
            self.assertTrue(flow.sso_declared(self.root))

    def test_only_question_is_code_key_missing_from_config_file_and_sm(self):
        import deploy_workflow as flow
        (self.path / 'index.js').write_text("requireEnv('TOKEN'); requireEnv('NEW_KEY'); requireEnv('IN_SM'); process.env.DB_HOST;")
        self.manifest['environments'] = {'SIT': {'source': None, 'parameters': {'boundaries': {}}}}
        (self.root / 'deployment/deploy-manifest.json').write_text(json.dumps(self.manifest))
        subprocess.run(['git', 'add', 'apps', 'deployment', '.gitignore'], cwd=self.root, check=True, capture_output=True)
        subprocess.run(['git', '-c', 'user.name=fixture', '-c', 'user.email=fixture@local', 'commit', '-qm', 'fixture'], cwd=self.root, check=True, capture_output=True)
        sm = Mock()
        sm.get_secret_value.return_value = {'VersionId': 'v1', 'SecretString': json.dumps({'IN_SM': 'remote'})}
        args = SimpleNamespace(env='SIT', profile='fixture', only=['orders'], rc_tag=None)
        output = io.StringIO()
        with patch.object(flow, 'ROOT', self.root), patch.object(flow, 'ledger_path', side_effect=lambda env: self.root / f'.local/releases/{env.lower()}-ledger.json'), patch.object(flow, 'command'), patch.object(secrets_manager, 'ROOT', self.root), patch.object(secrets_manager, 'EXAMPLE_FILE', self.schema), patch.object(secrets_manager, 'load_cfg', return_value={}), patch.object(secrets_manager, 'client', return_value=sm), patch.object(secrets_manager, 'secret_name', side_effect=lambda cfg, target, env: env + '/' + target), redirect_stdout(output):
            with self.assertRaisesRegex(ValueError, 'chưa có trong file cấu hình'):
                flow.prepare(args, self.manifest, targets(self.root, self.manifest))
            first = output.getvalue()
            # Dòng đã có trong file (kể cả để trống) thì không hỏi nữa.
            flow.prepare(args, self.manifest, targets(self.root, self.manifest))
        for key in ('orders/TOKEN: code dùng', 'orders/NEW_KEY: code dùng'):
            self.assertIn(key, first)
        for key in ('IN_SM: code dùng', 'DB_HOST: code dùng'):
            self.assertNotIn(key, first)
        self.assertEqual(declarations(self.root / '.local/sm/sm_configs.sit.py')[0]['orders']['NEW_KEY'], '')

    def test_first_deploy_full_flow_uses_sm_script_after_source_sync(self):
        import deploy_workflow as flow
        (self.path / 'index.js').write_text("requireEnv('TOKEN'); const port=process.env.PORT || '3000';")
        self.manifest['environments'] = {'SIT': {'source': None, 'parameters': {'boundaries': {}}, 'sync_command': ['bash', 'fixture-sync.sh']}}
        (self.root / 'deployment/deploy-manifest.json').write_text(json.dumps(self.manifest))
        subprocess.run(['git', 'add', 'apps', 'deployment', '.gitignore'], cwd=self.root, check=True, capture_output=True)
        subprocess.run(['git', '-c', 'user.name=fixture', '-c', 'user.email=fixture@local', 'commit', '-qm', 'fixture'], cwd=self.root, check=True, capture_output=True)
        remote = {'TOKEN': 'remote', 'EXTRA': 'keep'}
        sm = Mock()
        sm.get_secret_value.side_effect = lambda **kw: {'VersionId': 'v1', 'SecretString': json.dumps(remote)}
        calls = []
        def command(argv):
            calls.append(argv)
            if 'scripts/secrets_manager.py' in argv:
                # Simulate the existing push command and preserve unrelated remote keys.
                local = declarations(self.root / '.local/sm/sm_configs.sit.py')[0]['orders']
                remote.update({k: v for k, v in local.items() if v and k not in PLATFORM + TOOLING})
        args = SimpleNamespace(env='SIT', profile='fixture', only=['orders'], rc_tag=None)
        with patch.object(flow, 'ROOT', self.root), patch.object(flow, 'ledger_path', side_effect=lambda env: self.root / f'.local/releases/{env.lower()}-ledger.json'), patch.object(flow, 'command', side_effect=command), patch.object(flow, 'main_commits', return_value={'orders': {'commit_short': 'abcd1234', 'commit_sha': 'a' * 40}}), patch.object(secrets_manager, 'ROOT', self.root), patch.object(secrets_manager, 'EXAMPLE_FILE', self.schema), patch.object(secrets_manager, 'load_cfg', return_value={}), patch.object(secrets_manager, 'client', return_value=sm), patch.object(secrets_manager, 'secret_name', side_effect=lambda cfg, target, env: env + '/' + target), redirect_stdout(io.StringIO()):
            # Lượt đầu: PORT code đọc mà file và SM chưa có → dừng để hỏi; lượt sau dòng đã có thì đi tiếp.
            with self.assertRaisesRegex(ValueError, 'chưa có trong file cấu hình'):
                flow.prepare(args, self.manifest, targets(self.root, self.manifest))
            flow.prepare(args, self.manifest, targets(self.root, self.manifest))
            from release_ledger import load
            args.run = load(flow.ledger_path('SIT'), 'SIT')['runs'][-1]['id']
            args.phase = 'dry-run'
            flow.deploy(args, self.manifest, targets(self.root, self.manifest))
            self.assertFalse(any('--push' in c or 'push' in c for c in calls))
            args.phase = 'execute'
            flow.deploy(args, self.manifest, targets(self.root, self.manifest))
            source = next(i for i, c in enumerate(calls) if 'fixture-sync.sh' in c)
            secrets = next(i for i, c in enumerate(calls) if 'scripts/secrets_manager.py' in c)
            jenkins = next(i for i, c in enumerate(calls) if '--wait' in c)
            self.assertLess(source, secrets)
            self.assertLess(secrets, jenkins)
            self.assertIn('--parallel', calls[jenkins])
            with self.assertRaisesRegex(ValueError, 'đã bắt đầu execute'):
                flow.deploy(args, self.manifest, targets(self.root, self.manifest))
        self.assertEqual(remote['EXTRA'], 'keep')


class JenkinsTest(unittest.TestCase):
    def test_all_submitted_before_wait(self):
        barrier, sent = threading.Barrier(2), []
        def trigger(cfg, url, params):
            sent.append(url)
            barrier.wait(timeout=3)
            return url + 'queue/'
        def wait(cfg, queue):
            self.assertEqual(len(sent), 2)
            return queue + 'build/'
        api = SimpleNamespace(ledger_path=lambda env: Path('/unused'), job_url=lambda cfg, target, env, service: 'https://jenkins.test/' + target + '/',
                              deploy_params=lambda *a: {}, trigger_build=trigger, save_deploy=Mock(), now=lambda: 'now',
                              wait_for_build_start=wait, wait_for_build_finish=lambda *a, **k: 'SUCCESS', sync_ledger=Mock())
        rows = [{'target': n, 'service': n, 'group': 'boundaries', 'deploy': {}} for n in ('a', 'b')]
        with redirect_stdout(io.StringIO()):
            run_parallel(api, {}, 'UAT', {'id': 'run'}, rows, SimpleNamespace(ledger=None, wait=True, auto_approve=True))
        api.sync_ledger.assert_called_once()

    def test_unstable_build_counts_as_deployed_and_failure_does_not(self):
        for result, status in (('UNSTABLE', 'success'), ('FAILURE', 'failed')):
            with self.subTest(result=result):
                api = SimpleNamespace(ledger_path=lambda env: Path('/unused'), job_url=lambda *a: 'https://jenkins.test/a/',
                                      deploy_params=lambda *a: {}, trigger_build=lambda *a: 'queue/', save_deploy=Mock(), now=lambda: 'now',
                                      wait_for_build_start=lambda *a: 'build/', wait_for_build_finish=lambda *a, **k: result, sync_ledger=Mock())
                rows = [{'target': 'a', 'service': 'a', 'group': 'boundaries', 'deploy': {}}]
                args = SimpleNamespace(ledger=None, wait=True, auto_approve=False)
                with redirect_stdout(io.StringIO()):
                    if status == 'success':
                        run_parallel(api, {}, 'UAT', {'id': 'run'}, rows, args)
                    else:
                        with self.assertRaises(SystemExit):
                            run_parallel(api, {}, 'UAT', {'id': 'run'}, rows, args)
                self.assertEqual(api.save_deploy.call_args.kwargs['status'], status)
                self.assertEqual(api.save_deploy.call_args.kwargs['result'], result)

    def test_input_with_wfapi_parameter_list_waits_for_dev_lead(self):
        transport = Mock()
        base = 'https://jenkins.test/job/a/42/'
        transport.get.return_value = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: [
            {'id': 'data', 'proceedUrl': base + 'input/data/proceedEmpty', 'inputs': [{'name': 'VERSION'}]}])
        output = io.StringIO()
        with redirect_stdout(output):
            check_inputs({'JENKINS_USER': 'a', 'JENKINS_TOKEN': 't'}, base, set(), transport, lambda cfg: {})
        transport.post.assert_not_called()
        self.assertIn('ping dev lead', output.getvalue())

    def test_input_submit_url_is_approved_via_proceed_empty_of_the_same_build(self):
        # Jenkins đang dùng trả `wfapi/inputSubmit?inputId=`; trước đây dạng này bị coi là URL lạ và build kẹt chờ người.
        base = 'https://jenkins.test/job/a/42/'
        cases = [
            (base + 'wfapi/inputSubmit?inputId=Abc123', True),
            ('/job/a/42/wfapi/inputSubmit?inputId=Abc123', True),
            ('https://evil.test/job/a/42/wfapi/inputSubmit?inputId=Abc123', False),
            ('/job/b/7/wfapi/inputSubmit?inputId=Abc123', False),
            (base + 'wfapi/inputSubmit?inputId=Other', False),
        ]
        for proceed, approved in cases:
            with self.subTest(proceed=proceed):
                transport = Mock()
                transport.get.return_value = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: [
                    {'id': 'Abc123', 'proceedUrl': proceed, 'inputs': []}])
                transport.post.return_value.status_code = 200
                with redirect_stdout(io.StringIO()):
                    check_inputs({'JENKINS_USER': 'a', 'JENKINS_TOKEN': 't'}, base, set(), transport, lambda cfg: {})
                if approved:
                    self.assertEqual(transport.post.call_args.args[0], base + 'input/Abc123/proceedEmpty')
                else:
                    transport.post.assert_not_called()

    def test_denied_approval_prints_dev_lead_once_and_does_not_retry(self):
        transport = Mock()
        base = 'https://jenkins.test/job/a/42/'
        transport.get.return_value = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: [{'id': 'deploy', 'proceedUrl': base + 'input/deploy/proceedEmpty'}])
        transport.post.return_value.status_code = 403
        seen, output = set(), io.StringIO()
        cfg = {'JENKINS_USER': 'dev', 'JENKINS_TOKEN': 'test'}
        with redirect_stdout(output):
            check_inputs(cfg, base, seen, transport, lambda cfg: {})
            check_inputs(cfg, base, seen, transport, lambda cfg: {})
        transport.post.assert_called_once()
        self.assertEqual(output.getvalue().count('ping dev lead'), 1)

    def test_approval_redirect_to_login_reports_dev_lead(self):
        transport = Mock()
        base = 'https://jenkins.test/job/a/42/'
        transport.get.return_value = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: [{'id': 'deploy', 'proceedUrl': base + 'input/deploy/proceedEmpty'}])
        transport.post.return_value = SimpleNamespace(status_code=302, headers={'Location': '/login'})
        output = io.StringIO()
        with redirect_stdout(output):
            check_inputs({'JENKINS_USER': 'dev', 'JENKINS_TOKEN': 'test'}, base, set(), transport, lambda cfg: {})
        self.assertIn('ping dev lead', output.getvalue())
        self.assertNotIn('đã approve', output.getvalue())

    def test_successful_approval_is_not_posted_twice(self):
        transport = Mock()
        base = 'https://jenkins.test/job/a/42/'
        transport.get.return_value = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: [{'id': 'deploy', 'proceedUrl': base + 'input/deploy/proceedEmpty'}])
        transport.post.return_value = SimpleNamespace(status_code=200, headers={})
        seen, output = set(), io.StringIO()
        with redirect_stdout(output):
            check_inputs({'JENKINS_USER': 'dev', 'JENKINS_TOKEN': 'test'}, base, seen, transport, lambda cfg: {})
            transport.get.return_value.json = lambda: []
            check_inputs({'JENKINS_USER': 'dev', 'JENKINS_TOKEN': 'test'}, base, seen, transport, lambda cfg: {})
        transport.post.assert_called_once()
        self.assertIn('đã approve', output.getvalue())
        self.assertNotIn('ping dev lead', output.getvalue())

    def test_cross_build_or_parameterized_input_not_approved(self):
        transport = Mock()
        base = 'https://jenkins.test/job/a/42/'
        transport.get.return_value = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: [
            {'id': 'x', 'proceedUrl': 'https://other.test/input/x/proceedEmpty'},
            {'id': 'y', 'proceedUrl': base + 'input/y/proceedEmpty', 'parameters': ['value']}])
        with redirect_stdout(io.StringIO()):
            check_inputs({'JENKINS_USER': 'a', 'JENKINS_TOKEN': 't'}, base, set(), transport, lambda cfg: {})
        transport.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
