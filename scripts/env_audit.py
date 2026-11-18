#!/usr/bin/env python3
"""Đối chiếu code ↔ file SM ↔ khai báo DevOps inject; chỉ báo tên biến và vị trí đọc."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
from deploy_manifest import platform_contract, TOOLING, inside, read_manifest, targets
from env_inventory import inventory, source_files
from private_config import declarations, untracked, write_sm


def source_hash(root, entries):
    root = root.resolve()
    digest = hashlib.sha256()
    paths = [row['path'] for row in entries.values()]
    manifest = read_manifest(root)
    paths.extend(manifest.get('source_inputs', []))
    skip = {'node_modules', 'build', 'dist', '.dart_tool', 'generated', '.git', '.venv',
            '__pycache__', '.scannerwork', 'coverage', '.next', '.turbo', '.local'}
    for name in sorted(set(paths)):
        base = inside(root, name)
        for file in source_files(base, skip):
            if file.is_file() and not skip.intersection(file.relative_to(root).parts):
                digest.update(str(file.relative_to(root)).encode())
                digest.update(file.read_bytes())
    return digest.hexdigest()


def audit(root, env, add_missing=False):
    """Giá trị do người điền chịu trách nhiệm: phát hiện về giá trị chỉ là lưu ý, không chặn deploy."""
    root = root.resolve()
    manifest = read_manifest(root)
    entries = targets(root, manifest)
    platform = platform_contract(root, manifest)
    schema, must_match = declarations(inside(root, manifest['schema']))
    file = root / f'.local/sm/sm_configs.{env.lower()}.py'
    untracked(root, file)
    values, _ = declarations(file)
    warnings, rows, added, dynamic = [], [], [], {}
    derived = set(manifest.get('derived_env', ()))
    local_only = set(manifest.get('local_only_env', ()))
    for target, entry in entries.items():
        if entry['group'] == 'mobile-experiences':
            continue
        build = {}
        build_rule = manifest.get('build_env', {}).get(entry['group'])
        if build_rule:
            build_path = inside(root, build_rule.format(target_path=entry['path'], env=env.lower()))
            if not build_path.exists():
                warnings.append(f'{target}: thiếu file build env {build_path.relative_to(root)}')
            else:
                build = json.loads(build_path.read_text())
                if not isinstance(build, dict):
                    raise ValueError('File build env phải là JSON object.')
        used, unknown = inventory(root, entry, manifest.get('env_helpers', ()))
        dynamic[target] = unknown
        warnings.extend(f'{target}: truy cập env động chưa xác định tại {where}' for where in unknown)
        local = values.setdefault(target, {}) if target in schema else values.get(target, {})
        for key, detail in used.items():
            kind = 'sm'
            state = 'đã điền' if local.get(key) not in (None, '') else 'thiếu'
            if build_rule and key.startswith('VITE_'):
                kind = 'build'
                state = 'build env — đã điền' if build.get(key) not in (None, '') else 'build env — thiếu'
                if build.get(key) in (None, ''):
                    warnings.append(f'{target}: {key} thiếu trong file build env')
            elif key in derived | local_only:
                kind = 'derived'
                state = 'code tự dựng/local hoặc build; kiểm riêng'
            elif key in platform or key in TOOLING:
                # DevOps inject tin theo khai báo; luồng deploy không đọc cụm.
                kind = 'inject'
                state = 'DevOps inject (khai báo)'
            else:
                if add_missing and target in schema and key not in local:
                    # Key code đọc mà file chưa có: thêm dòng TRỐNG, không lấy mặc định của code.
                    local[key] = ''
                    added.append(f'{target}:{key}')
                if state == 'thiếu' and detail['optional'] and not detail['required']:
                    state = 'rỗng/thiếu tùy chọn — code tự dùng mặc định'
                elif state == 'thiếu':
                    warnings.append(f'{target}: {key} chưa có giá trị')
                if '<' in str(local.get(key, '')) and '>' in str(local.get(key, '')):
                    warnings.append(f'{target}: {key} còn placeholder')
            rows.append({'target': target, 'key': key, 'kind': kind, 'state': state, 'locations': detail['locations']})
        for key in sorted(set(local) - set(used)):
            rows.append({'target': target, 'key': key, 'kind': 'extra',
                         'state': 'SM/file có, chưa thấy code đọc — giữ nguyên', 'locations': []})
    for key in must_match:
        if len({row[key] for row in values.values() if row.get(key)}) > 1:
            warnings.append(f'{key}: MUST_MATCH lệch giữa target')
    if added:
        write_sm(file, values, must_match)
    return {'environment': env, 'rows': rows, 'warnings': warnings, 'added': added,
            'dynamic_reads': dynamic, 'source_sha256': source_hash(root, entries),
            'platform_contract': list(platform), 'tooling_inject': list(TOOLING)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env', required=True, choices=('SIT', 'UAT', 'PT', 'PREPROD', 'PROD'))
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    try:
        result = audit(a.root.resolve(), a.env)
        if a.output:
            a.output.parent.mkdir(parents=True, exist_ok=True)
            a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        for row in result['rows']:
            print(f"{row['target']}: {row['key']} — {row['state']}")
        for warning in result['warnings']:
            print('LƯU Ý: ' + warning)
        return 0
    except (OSError, ValueError, KeyError, SyntaxError):
        print('✗ audit: thiếu/hỏng manifest hoặc file cấu hình; không in giá trị.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
