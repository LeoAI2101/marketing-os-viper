"""Đọc literal và ghi nguyên tử file riêng tư, không thực thi nội dung Python."""
import ast
import io
import tokenize
import os
import re
import subprocess
import tempfile
from pathlib import Path


def untracked(root, path):
    result = subprocess.run(['git', 'ls-files', '--error-unmatch', str(path.resolve())],
                            cwd=root, capture_output=True)
    if result.returncode == 0:
        raise ValueError('File cấu hình thật đang được Git theo dõi.')


def declarations(path):
    out = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign):
            for name in node.targets:
                if isinstance(name, ast.Name) and name.id in ('SM_CONFIGS', 'MUST_MATCH'):
                    out[name.id] = ast.literal_eval(node.value)
    if not isinstance(out.get('SM_CONFIGS'), dict):
        raise ValueError('Thiếu SM_CONFIGS dạng dict literal.')
    return out['SM_CONFIGS'], set(out.get('MUST_MATCH', ()))


def private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix='.private-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_sm(path, data, must_match, injected=()):
    # Giữ comment của từng key; comment DevOps inject là nguồn phân loại global.
    comments = {}
    if path.exists():
        source = path.read_text()
        tokens = {t.start[0]: t.string for t in tokenize.generate_tokens(io.StringIO(source).readline)
                  if t.type == tokenize.COMMENT}
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Dict):
                continue
            for target, block in zip(node.keys, node.values):
                if not isinstance(target, ast.Constant) or not isinstance(block, ast.Dict):
                    continue
                for key in block.keys:
                    if isinstance(key, ast.Constant) and key.lineno in tokens:
                        comments[(target.value, key.value)] = tokens[key.lineno]
    lines = ['# Cấu hình; file thật KHÔNG commit, KHÔNG in giá trị.', 'SM_CONFIGS = {']
    for target, block in sorted(data.items()):
        lines.append(f'    {target!r}: {{')
        for key, value in sorted(block.items()):
            comment = comments.get((target, key), '')
            if key in injected and not re.search('devops.*inject', comment, re.I):
                comment = '# DevOps inject bổ sung (Redis tooling)' if key.startswith('REDIS_TOOLING_') else '# DevOps inject global mọi pod'
            lines.append(f'        {key!r}: {value!r},' + ('  ' + comment if comment else ''))
        lines.append('    },')
    lines.extend(['}', '', 'MUST_MATCH = ' + repr(sorted(must_match)), ''])
    private_write(path, '\n'.join(lines))


def secret_value(sm, name):
    """(SecretString | None, VersionId | None). Vỏ có mà chưa từng ghi giá trị → ('{}', None); vỏ không có thì ném."""
    try:
        reply = sm.get_secret_value(SecretId=name)
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') != 'ResourceNotFoundException':
            raise
        sm.describe_secret(SecretId=name)  # vỏ chưa tồn tại thì ném tiếp: DevOps phải tạo, tool không tự tạo
        return '{}', None
    return reply.get('SecretString'), reply.get('VersionId')


def pull(sm, cfg, schema, names, destination, secret_name, root, prefer_remote=False):
    """GET toàn bộ namespace repo; lỗi bất kỳ thì không ghi file nửa vời."""
    untracked(root, destination)
    existing, _ = declarations(destination) if destination.exists() else ({}, set())
    result, versions, conflicts, remote_keys = dict(existing), {}, [], {}
    for target, keys in schema[0].items():
        if target not in names:
            raise ValueError(f'Khuôn khai target không có trong manifest: {target}')
        payload, version = secret_value(sm, secret_name(cfg, target))
        remote = __import__('json').loads(payload) if payload is not None else None
        if not isinstance(remote, dict):
            raise ValueError(f'{target}: secret không phải JSON object.')
        local = existing.get(target, {})
        merged = {k: '' for k in keys}
        merged.update({k: '' if v is None else str(v) for k, v in remote.items()})
        for key, value in local.items():
            if value != '' and (not prefer_remote or key not in remote):
                if key in remote and str(remote[key]) != str(value):
                    conflicts.append({'target': target, 'key': key})
                merged[key] = value
        result[target] = merged
        versions[target] = version
        remote_keys[target] = sorted(remote)
    write_sm(destination, result, schema[1])
    return {'versions': versions, 'conflicts': conflicts, 'remote_keys': remote_keys}
