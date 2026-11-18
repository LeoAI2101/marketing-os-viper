#!/usr/bin/env python3
"""Deploy dùng lại giữa repo: prepare → dry-run → execute; chỉ execute khi được yêu cầu."""
import argparse
import hashlib
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'scripts/lib')]
from deploy_manifest import inside, read_manifest, targets, platform_contract
from private_config import declarations, private_write, secret_value
from deploy_secrets import snapshot
from release_common import now, write_json
from release_ledger import editing, find_run, ledger_path, load, new_run_id
from env_audit import audit, source_hash
import aws_session
from ops_env import aws_env_cfg, aws_file, jenkins_file


def command(argv):
    env = {k: v for k, v in os.environ.items() if k not in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')}
    subprocess.run(argv, cwd=ROOT, env=env, check=True)


def fingerprint(env, entries):
    manifest = read_manifest(ROOT)
    paths = [ROOT / 'deployment/deploy-manifest.json', ROOT / f'.local/sm/sm_configs.{env.lower()}.py',
             inside(ROOT, manifest['schema']), ROOT / '.local/env/.env.jenkins', ROOT / '.local/env' / jenkins_file(env),
             ROOT / '.local/env/.env.gitlab-sync']
    for entry in entries.values():
        rule = manifest.get('build_env', {}).get(entry['group'])
        if rule:
            paths.append(inside(ROOT, rule.format(target_path=entry['path'], env=env.lower())))
    paths.extend(p for p in (ROOT / 'scripts/lib').glob('*.py'))
    paths.extend(ROOT / 'scripts' / name for name in ('deploy_workflow.py', 'env_audit.py',
                 'aws_session.py', 'secrets_manager.py', 'jenkins_build.py', 'prepare_deploy.py'))
    digest = hashlib.sha256(source_hash(ROOT, entries).encode())
    digest.update(json.dumps(platform_contract(ROOT, manifest)).encode())
    for path in paths:
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def sso_declared(root, env=None):
    path = root / '.local/env' / aws_file(env)
    return path.exists() and bool(aws_env_cfg(aws_session.env_values(path.read_text()), env).get('AWS_SSO_START_URL'))


def selection(a, entries):
    names = a.only or [n for n, r in entries.items() if r['group'] in ('boundaries', 'web-experiences')]
    if not names or any(n not in entries for n in names) or len(set(names)) != len(names):
        raise ValueError('Phạm vi target thiếu/trùng/không có trong manifest.')
    return names


def main_commits(names):
    import gitlab_main_commits as gitlab
    cfg = gitlab.load_config()
    repos = {row['target']: row for row in gitlab.repo_list(cfg)}
    result = {}
    for name in names:
        repo = repos[name].get('repository')
        if not repo:
            raise ValueError(f'{name}: thiếu mapping GitLab.')
        result[name] = gitlab.read_main(cfg, repo)
    return result


def sit_prepare(names, entries, a):
    local_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    with editing(ledger_path('SIT'), 'SIT') as ledger:
        run_id = new_run_id(ledger, 'SIT')
        services = [{'target': name, 'group': entries[name]['group'], 'service': entries[name]['service'],
                     'source': None, 'deploy': {'rc_tag': None, 'image_tag': local_head[:8],
                     'status': 'pending', 'skip': False}} for name in names]
        ledger['runs'].append({'id': run_id, 'created_at': now(), 'source_env': None,
                               'services': services, 'local_head': local_head, 'awaiting_synced_main': True})
    return run_id


def prepare(a, manifest, entries):
    profile = a.profile or ('default' if sso_declared(ROOT, a.env) else None)
    if not profile:
        raise ValueError('prepare cần --profile cho AWS login trước khi đọc SM (hoặc khai AWS_SSO_* trong .local/env/.env.aws).')
    command([sys.executable, 'scripts/aws_session.py', '--profile', profile, '--env', a.env])
    import secrets_manager as smtool
    cfg = smtool.load_cfg(a.env)
    sm = smtool.client(cfg)
    values_file = ROOT / f'.local/sm/sm_configs.{a.env.lower()}.py'
    from types import SimpleNamespace
    first = not values_file.exists()
    # Dòng đã có trong file TRƯỚC lượt này (kể cả để trống) là dòng người dùng đã thấy: không hỏi lại.
    known = {} if first else declarations(values_file)[0]
    smtool.cmd_init(cfg, SimpleNamespace(env=a.env, reconcile=True))
    remote = smtool.cmd_pull(cfg, SimpleNamespace(env=a.env, prefer_remote=first))['remote_keys']
    result = audit(ROOT, a.env, add_missing=True)
    # Câu hỏi duy nhất của luồng: code đọc key mà file chưa có dòng và SM cũng chưa có.
    schema = declarations(inside(ROOT, manifest['schema']))[0]
    result['blockers'] = [
        f"{row['target']}/{row['key']}: code dùng mà sm_configs chưa có; đã thêm dòng trống vào file — hỏi người dùng giá trị rồi prepare lại"
        for row in result['rows'] if row['kind'] == 'sm' and row['target'] in schema
        and row['key'] not in known.get(row['target'], {}) and row['key'] not in remote.get(row['target'], ())]
    report = ROOT / f'.local/releases/{a.env.lower()}-env-audit.json'
    write_json(report, result)
    for warning in result['warnings']:
        print('LƯU Ý: ' + warning)
    if result['blockers']:
        for blocker in result['blockers']:
            print('✗ ' + blocker)
        raise ValueError(f'Có key code dùng mà chưa có trong file cấu hình; xem {report.relative_to(ROOT)}.')
    names = selection(a, entries)
    source = manifest['environments'][a.env]['source']
    if a.env == 'SIT':
        run_id = sit_prepare(names, entries, a)
    else:
        argv = [sys.executable, 'scripts/prepare_deploy.py', *names, '--env', a.env, '--source-env', source]
        if a.rc_tag:
            argv += ['--rc-tag', a.rc_tag]
        if a.release_tag:
            argv += ['--release-tag', a.release_tag]
        command(argv)
        run_id = find_run(load(ledger_path(a.env), a.env), None)['id']
    proof = {'environment': a.env, 'run_id': run_id, 'targets': names, 'fingerprint': fingerprint(a.env, entries),
             'secrets': snapshot(sm, cfg, a.env, declarations(values_file)[0], smtool.secret_name, platform_contract(ROOT, manifest))}
    run = find_run(load(ledger_path(a.env), a.env), run_id)
    proof['release_hash'] = release_hash(run, names)
    private_write(proof_path(a.env, run_id), json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
    print(f'✓ Lượt {run_id} đã chuẩn bị. Chốt RC/commit, rồi dry-run --run {run_id}.')


def proof_path(env, run_id):
    if not __import__('re').fullmatch(r'[A-Z]+-\d{8}-\d+', run_id or '') or not run_id.startswith(env + '-'):
        raise ValueError('Run ID không hợp lệ hoặc sai môi trường.')
    return ROOT / f'.local/releases/{run_id}-proof.json'


def release_hash(run, names):
    rows = [{'target': r['target'], 'group': r['group'], 'service': r['service'], 'source': r.get('source'),
             'deploy': {k: r['deploy'].get(k) for k in ('rc_tag', 'image_tag', 'skip', 'release_tag') if k in r['deploy']}}
            for r in run['services'] if r['target'] in names]
    return hashlib.sha256(json.dumps({'source_env': run.get('source_env'), 'services': rows}, sort_keys=True).encode()).hexdigest()


def checked(a, entries):
    proof = json.loads(proof_path(a.env, a.run).read_text())
    if a.only and a.only != proof['targets']:
        raise ValueError('Phạm vi khác bản chuẩn bị; prepare lại.')
    if proof['fingerprint'] != fingerprint(a.env, entries):
        raise ValueError('Code/cấu hình đã đổi; prepare và dry-run lại.')
    run = find_run(load(ledger_path(a.env), a.env), a.run)
    selected = [row for row in run['services'] if row['target'] in proof['targets']]
    if len(selected) != len(proof['targets']) or {r['target'] for r in selected} != set(proof['targets']):
        raise ValueError('Ledger thiếu/trùng target của lượt đã chốt.')
    for row in selected:
        entry = entries[row['target']]
        if row['service'] != entry['service'] or row['group'] != entry['group']:
            raise ValueError('Định tuyến Jenkins khác manifest; prepare lại.')
    if release_hash(run, proof['targets']) != proof['release_hash']:
        raise ValueError('RC/commit đã đổi; prepare lại để ghim bản mới.')
    return proof


def wait_flags(env):
    """PROD KHÔNG BAO GIỜ auto-approve (deployment/PROD-DEPLOY.md): người có quyền duyệt tay.

    Dự án muốn khác thì đó là quyết định của Authority — ghi DECISIONS.md rồi mới đổi ở đây.
    """
    return ['--wait'] if env.upper() == 'PROD' else ['--wait', '--auto-approve']


def deploy(a, manifest, entries):
    proof = checked(a, entries)
    argv = [sys.executable, 'scripts/jenkins_build.py', 'deploy-run', '--env', a.env,
            '--run', a.run, '--only', *proof['targets'], '--parallel']
    import jenkins_build
    run = find_run(load(ledger_path(a.env), a.env), a.run)
    for row in run['services']:
        if row['target'] in proof['targets'] and (why := jenkins_build.deploy_blocker(row, a.env)):
            raise ValueError(f"{row['target']}: {why}; không gửi lại lượt này.")
    if a.phase == 'dry-run':
        for name, diff in proof['secrets'].items():
            print(f"{name}: thêm {','.join(diff['added']) or '-'}; đổi {','.join(diff['changed']) or '-'}; giữ key khác.")
        command(argv + ['--dry-run'])
        proof['dry_run_at'] = now()
        private_write(proof_path(a.env, a.run), json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
        return
    if not proof.get('dry_run_at'):
        raise ValueError('Phải dry-run đúng lượt trước execute.')
    if proof.get('execution_started_at'):
        raise ValueError('Lượt đã bắt đầu execute; dùng status, kiểm ledger rồi prepare lượt mới nếu cần.')
    env_cfg = manifest.get('environments', {}).get(a.env, {})
    for check in env_cfg.get('pre_execute_checks', manifest.get('pre_execute_checks', [])):
        command(check)
    proof = checked(a, entries)  # Check/test có thể sinh lại contract; không đẩy source khác bản đã rà.
    # Snapshot nguồn phải sạch; sync repo con trước mọi lượt gửi Jenkins.
    source_paths = [entries[name]['path'] for name in proof['targets']]
    status = subprocess.check_output(['git', 'status', '--porcelain', '--', *source_paths], cwd=ROOT, text=True)
    if status:
        raise ValueError('Sync source cần các target sạch; commit thay đổi source trước execute.')
    import secrets_manager as smtool
    cfg = smtool.load_cfg(a.env)
    values = declarations(ROOT / f'.local/sm/sm_configs.{a.env.lower()}.py')[0]
    sm = smtool.client(cfg)
    for item in proof['secrets'].values():
        if secret_value(sm, item['name'])[1] != item['version']:
            raise ValueError('SM đổi từ dry-run; prepare lại trước sync source/secret.')
    sync = manifest.get('sync_command') or manifest['environments']['SIT']['sync_command']
    proof['execution_started_at'] = now()
    private_write(proof_path(a.env, a.run), json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
    command([*sync, '--push', *proof['targets']])
    if a.env == 'SIT':
        commits = main_commits(proof['targets'])
        with editing(ledger_path(a.env), a.env) as ledger:
            run = find_run(ledger, a.run)
            for row in run['services']:
                if row['target'] in commits:
                    row['deploy']['image_tag'] = commits[row['target']]['commit_short']
            run['synced_main'] = commits
            run['awaiting_synced_main'] = False
            run['synced_source_head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
            proof['release_hash'] = release_hash(run, proof['targets'])
        private_write(proof_path(a.env, a.run), json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
    # Dùng chính script SM hiện có, không tạo một đường ghi secret thứ hai.
    command([sys.executable, 'scripts/secrets_manager.py', 'push', '--env', a.env, '--yes',
             '--expected-versions', str(proof_path(a.env, a.run))])
    current = snapshot(sm, cfg, a.env, values, smtool.secret_name, platform_contract(ROOT, manifest))
    if any(row['added'] or row['changed'] for row in current.values()):
        raise ValueError('Sync SM chưa đủ/không xác nhận được; chưa gửi Jenkins.')
    # PROD không auto-approve: người có quyền duyệt tay, skill chỉ theo dõi (in link chờ duyệt).
    command(argv + wait_flags(a.env))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=('prepare', 'dry-run', 'execute', 'status'))
    p.add_argument('--env', required=True, choices=('SIT', 'UAT', 'PT', 'PREPROD', 'PROD'))
    p.add_argument('--profile')
    p.add_argument('--run')
    p.add_argument('--only', nargs='+')
    p.add_argument('--rc-tag')
    p.add_argument('--release-tag', help='PROD: tag release vX.Y.Z (mặc định v1.0.1 cho bản đầu)')
    a = p.parse_args()
    try:
        manifest = read_manifest(ROOT)
        entries = targets(ROOT, manifest)
        if a.phase == 'prepare':
            prepare(a, manifest, entries)
        elif a.phase == 'status':
            command([sys.executable, 'scripts/jenkins_build.py', 'sync', '--env', a.env])
        else:
            lock_path = proof_path(a.env, a.run).with_suffix('.execute.lock')
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                deploy(a, manifest, entries)
        return 0
    except (Exception, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code == 0:
            return 0
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print('✗ deploy: ' + message + ' — xem ledger/kết quả; không tự hủy hoặc gửi lại build.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
