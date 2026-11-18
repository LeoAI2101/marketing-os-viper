"""Chặn lỗi data-security mới trên cùng payload ghi; không miễn lỗi cũ trong gate."""
from collections import Counter
import json
import subprocess
import sys
from lib import write_change, query_check


def inspect_source(path, source):
    result = subprocess.run(['node', str(write_change.ROOT/'scripts/check-data-security.mjs'),
                             '--file', path, '--stdin', '--json'], input=source, text=True,
                            capture_output=True, cwd=write_change.ROOT, timeout=35)
    if result.returncode not in (0, 1):
        raise ValueError('Checker chưa chạy được')
    return [f for f in json.loads(result.stdout)['findings'] if f['level'] == 'error']


def check(payload):
    value = payload.get('tool_input') or {}
    command = value.get('command')
    if isinstance(command, str) and '*** Begin Patch' in command and 'srcroot/boundaries/' in command:
        if write_change.parse_apply_patch(command) is None:
            print('[VIPER guard_write/data-security] CHẶN: không dựng được toàn bộ patch boundary.', file=sys.stderr)
            return 2
    for change in query_check.changes_from(payload):
        path = change.rel
        if not path.startswith('srcroot/boundaries/') or not '/src/' in path or not path.endswith('.ts'):
            continue
        if path.endswith(('.spec.ts', '.test.ts', '.d.ts')) or '/shared/contracts/' in path:
            continue
        if change.after is None or change.after == change.before:
            continue
        try:
            before = Counter(f['fingerprint'] for f in inspect_source(path, change.before or ''))
            for f in inspect_source(path, change.after):
                key = f['fingerprint']
                if before[key]:
                    before[key] -= 1
                else:
                    print(f'[VIPER guard_write/data-security] CHẶN {path}:{f["line"]} {f["rule"]}: {f["message"]}', file=sys.stderr)
                    return 2
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            print('[VIPER guard_write/data-security] CHẶN: chưa kiểm được source, không giả kết quả an toàn.', file=sys.stderr)
            return 2
    return 0
