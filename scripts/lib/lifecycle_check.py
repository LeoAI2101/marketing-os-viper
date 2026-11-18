"""Chặn lỗi khai báo lifecycle mới; dựng đồng thời schema/sổ từ cùng payload."""
from collections import Counter
import sys
from lib import write_change, query_check
from lib.data_lifecycle import audit, REGISTRY, SCHEMA


def snapshot():
    return {p.relative_to(write_change.ROOT).as_posix():p.read_text()
            for p in (write_change.ROOT/'srcroot/boundaries').glob('*/prisma/schema.prisma')}


def check(payload):
    value = payload.get('tool_input') or {}
    changes = query_check.changes_from(payload)
    relevant = [c for c in changes if SCHEMA.fullmatch(c.rel) or c.rel == REGISTRY]
    if not relevant:
        command = str(value.get('command') or '')
        if '*** Begin Patch' in command and ('schema.prisma' in command or REGISTRY in command) and not changes:
            print('[VIPER lifecycle] CHẶN: chưa đọc được patch schema/sổ.', file=sys.stderr)
            return 2
        return 0
    try:
        before = snapshot()
        after = dict(before)
        registry_before = write_change.disk_text(REGISTRY) or ''
        registry_after = registry_before
        for change in relevant:
            if change.rel == REGISTRY:
                registry_before = change.before or ''
                registry_after = change.after or ''
            else:
                if change.before is None:
                    before.pop(change.rel, None)
                else:
                    before[change.rel] = change.before
                if change.after is None:
                    after.pop(change.rel, None)
                else:
                    after[change.rel] = change.after
        # Lần đầu tạo sổ: bản trước chưa có vùng bảng, coi là không có dòng khai báo.
        empty_registry = '<!-- DATA-LIFECYCLE:BEGIN -->\n| Target | Model | Retention | Archive | Purge | Legal hold | Trạng thái | Bằng chứng |\n<!-- DATA-LIFECYCLE:END -->'
        # Bản sau phải đọc được trước. Baseline hỏng không được khoá đường sửa
        # về một sổ/schema hoàn chỉnh; cũng không được miễn nợ mới khi không so được.
        proposed = audit(after, registry_after or empty_registry)
        if not proposed['findings']:
            return 0
        baseline = audit(before, registry_before or empty_registry)
        key = lambda f:(f['file'], f['model'], f['rule'])
        old = Counter(key(f) for f in baseline['findings'])
        for finding in proposed['findings']:
            identity = key(finding)
            if old[identity]:
                old[identity] -= 1
                continue
            print(f"[VIPER lifecycle] CHẶN {finding['model']} {finding['rule']}: {finding['message']}", file=sys.stderr)
            return 2
        return 0
    except (OSError, ValueError, KeyError):
        print('[VIPER lifecycle] CHẶN: chưa kiểm được schema/sổ dự kiến.', file=sys.stderr)
        return 2
