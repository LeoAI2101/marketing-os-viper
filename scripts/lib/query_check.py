"""Cảnh báo trước ghi: dùng cùng AST checker với make, so lỗi mới bằng đa tập."""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
from lib import write_change


def inspect_source(path, source):
    checker = write_change.ROOT / 'scripts/check-query-safety.mjs'
    result = subprocess.run(['node', str(checker), '--file', path, '--stdin', '--json'],
                            input=source, text=True, capture_output=True, cwd=write_change.ROOT,
                            timeout=35)
    if result.returncode not in (0, 1):
        raise ValueError(result.stderr.strip() or 'checker không chạy được')
    return [f for f in json.loads(result.stdout)['findings'] if f['rule'] == 'QS001']


def changes_from(payload):
    value = payload.get('tool_input') or {}
    command = value.get('command')
    if isinstance(command, str) and '*** Begin Patch' in command:
        return write_change.parse_apply_patch(command) or []
    change = write_change.legacy_change(value)
    return [change] if change else []


def check(payload):
    messages = []
    for change in changes_from(payload):
        if not change.rel.startswith('srcroot/boundaries/') or not change.rel.endswith('.repository.ts'):
            continue
        if change.after is None or change.after == change.before:
            continue
        try:
            before = Counter(f['fingerprint'] for f in inspect_source(change.rel, change.before or ''))
            for finding in inspect_source(change.rel, change.after):
                key = finding['fingerprint']
                if before[key]:
                    before[key] -= 1
                else:
                    messages.append(f"{change.rel}:{finding['line']} {finding['rule']}: {finding['message']}")
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
            messages.append(f"{change.rel}: CHƯA KIỂM ĐƯỢC query-safety: {error}")
    if messages:
        context = '[VIPER guard_write/query-safety] CẢNH BÁO, không chặn ghi:\n' + '\n'.join(messages)
        print(context, file=sys.stderr)
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': context}}, ensure_ascii=False))
    return 0
