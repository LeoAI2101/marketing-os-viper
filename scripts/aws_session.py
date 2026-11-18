#!/usr/bin/env python3
"""AWS login → khóa STS → kiểm identity → .local/env/.env.aws, không in giá trị bí mật."""
import argparse
import configparser
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
from ops_env import aws_env_cfg, aws_file, has_own_aws_file, own_aws_account
from private_config import private_write, untracked

KEYS = ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')
# SSO khai ngay trong .env.aws: helper tự sinh file cấu hình AWS CLI trong .local/env/.
SSO_KEYS = {'AWS_SSO_START_URL': r'https://[A-Za-z0-9./_-]+', 'AWS_SSO_REGION': r'[a-z0-9-]+',
            'AWS_SSO_ACCOUNT_ID': r'[0-9]{12}', 'AWS_SSO_ROLE_NAME': r'[A-Za-z0-9_+=,.@-]+'}
CLI_CONFIG_NAME = '.env.aws.cli-config'


def cli_config_name(env=None):
    """Env có account riêng giữ cli-config riêng, để không ghi đè `[default]` của account chung."""
    return f'.env.aws.{env.lower()}.cli-config' if env else CLI_CONFIG_NAME


def env_values(text):
    found = re.findall(r'^(?:export\s+)?([A-Z_]+)\s*=(.*?)$', text, re.M)
    return {key: value.strip().strip('"\'') for key, value in found}


def sso_cli_config(profile, values, path, env=None):
    """Có AWS_SSO_START_URL thì sinh file cấu hình AWS CLI cạnh .env.aws; không thì None."""
    if not values.get('AWS_SSO_START_URL'):
        return None
    bad = [key for key, pattern in SSO_KEYS.items() if not re.fullmatch(pattern, values.get(key, ''))]
    if bad:
        raise ValueError('Thiếu hoặc sai cấu hình SSO trong .env.aws: ' + ', '.join(bad))
    region = values.get('AWS_DEFAULT_REGION', '')
    if not re.fullmatch(r'[a-z0-9-]*', region):
        raise ValueError('AWS_DEFAULT_REGION không hợp lệ.')
    lines = ['[default]' if profile == 'default' else '[profile ' + profile + ']',
             'sso_start_url = ' + values['AWS_SSO_START_URL'], 'sso_region = ' + values['AWS_SSO_REGION'],
             'sso_account_id = ' + values['AWS_SSO_ACCOUNT_ID'], 'sso_role_name = ' + values['AWS_SSO_ROLE_NAME']]
    if region:
        lines.append('region = ' + region)
    target = path.parent / cli_config_name(env)
    private_write(target, '\n'.join(lines) + '\n')
    return target


def refresh(root, profile, method='auto', runner=subprocess.run, env=None):
    path = root / '.local/env' / aws_file(env)
    untracked(root, path)
    cli_env = {k: v for k, v in os.environ.items() if k not in KEYS}
    old = path.read_text() if path.exists() else (root / f'.local/templates/{path.name}.example').read_text()
    raw = env_values(old)
    # Env có account riêng (AWS_SSO_ACCOUNT_ID_<ENV>) đọc/ghi khóa hậu tố _<ENV>; env khác dùng khóa chung.
    own = own_aws_account(raw, env)
    values = aws_env_cfg(raw, env)
    suffix = '_' + env.upper() if own else ''
    cli_config = sso_cli_config(profile, values, path, env if own or has_own_aws_file(env) else None)
    export = ['aws', 'configure', 'export-credentials', '--profile', profile, '--format', 'process']
    if cli_config:
        cli_env['AWS_CONFIG_FILE'] = str(cli_config)
        # Phiên SSO còn hạn thì lấy khóa tạm luôn; hết hạn mới phải duyệt web. Máy dùng chung:
        # không mở trình duyệt tại chỗ, URL + mã thiết bị in ra terminal.
        if runner(export, env=cli_env, capture_output=True, text=True).returncode:
            runner(['aws', 'sso', 'login', '--profile', profile, '--no-browser'], env=cli_env, check=True)
    elif method == 'auto':
        cfg = configparser.RawConfigParser()
        cfg.read(Path.home() / '.aws/config')
        section = 'default' if profile == 'default' else 'profile ' + profile
        sso = cfg.has_section(section) and any(k.startswith('sso_') for k in cfg[section])
        method = 'sso' if sso else 'login'
    if not cli_config:
        login = ['aws', 'sso', 'login'] if method == 'sso' else ['aws', 'login']
        runner(login + ['--profile', profile], env=cli_env, check=True)
    reply = runner(export, env=cli_env, check=True, capture_output=True, text=True)
    data = json.loads(reply.stdout)
    creds = dict(zip(KEYS, (data.get('AccessKeyId'), data.get('SecretAccessKey'), data.get('SessionToken'))))
    if not all(creds.values()):
        raise ValueError('Profile không trả đủ ba khóa STS.')
    region = values.get('AWS_DEFAULT_REGION') or os.environ.get('AWS_DEFAULT_REGION')
    if not region:
        raise ValueError('Chưa khai AWS_DEFAULT_REGION ở .local/env/.env.aws.')
    identity = runner(['aws', 'sts', 'get-caller-identity', '--region', region, '--output', 'json'],
                      env={**cli_env, **creds}, check=True, capture_output=True, text=True)
    account = json.loads(identity.stdout).get('Account')
    if not account:
        raise ValueError('Không xác nhận được AWS identity.')
    declared = values.get('AWS_SSO_ACCOUNT_ID')
    if declared and account != declared:
        # Khóa của account này mà vào ô của account kia là lỗi đắt nhất của bộ tool: dừng trước khi ghi.
        raise ValueError(f'Account STS {account} khác account khai trong .env.aws ({declared}); chưa ghi gì.')
    keep = {k + suffix for k in KEYS}
    lines = [line for line in old.splitlines() if line.removeprefix('export ').split('=', 1)[0].strip() not in keep]
    private_write(path, '\n'.join(lines) + '\n' + ''.join(k + suffix + '=' + creds[k] + '\n' for k in KEYS))
    print(f'✓ AWS STS hợp lệ, account {account}' + (f' ({env.upper()})' if own or has_own_aws_file(env) else '')
          + '; đã cập nhật ba khóa, không in giá trị.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', required=True)
    p.add_argument('--method', choices=('auto', 'sso', 'login'), default='auto')
    p.add_argument('--env', help='môi trường (vd PREPROD): env có account AWS riêng thì làm mới đúng khóa *_<ENV>')
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    a = p.parse_args()
    try:
        refresh(a.root.resolve(), a.profile, a.method, env=a.env)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        detail = f' {exc}' if isinstance(exc, ValueError) else ''  # ValueError ở đây chỉ chứa tên khóa/số account, không có giá trị bí mật
        print(f'✗ AWS login/STS thất bại;{detail} kiểm CLI/profile/quyền. Chưa đọc Secrets Manager.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
