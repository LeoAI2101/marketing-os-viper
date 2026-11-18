"""Manifest công khai cho bộ deploy; không chứa giá trị env/credential."""
import json
import ast
import io
import tokenize
import re
from pathlib import Path

PLATFORM = ('DB_HOST', 'DB_PORT', 'DB_USERNAME', 'DB_PASSWORD', 'REDIS_HOST', 'REDIS_PORT', 'REDIS_AUTH_TOKEN',
            'KAFKA_SERVERS', 'KAFKA_USERNAME', 'KAFKA_PASSWORD')
TOOLING = ('REDIS_TOOLING_HOST', 'REDIS_TOOLING_PORT', 'REDIS_TOOLING_AUTH_TOKEN')
NAME = re.compile(r'[a-z0-9][a-z0-9-]*')


def inside(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Đường dẫn manifest vượt ra ngoài repo.')
    return path


def read_manifest(root):
    path = root / 'deployment/deploy-manifest.json'
    data = json.loads(path.read_text())
    if data.get('version') != 1:
        raise ValueError('Manifest deploy phải có version=1.')
    return data


def injected_annotations(path):
    """Chỉ đọc tên key có comment DevOps inject; không thực thi hoặc trả giá trị."""
    if not path.exists():
        return set()
    source = path.read_text()
    keys = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str) and re.fullmatch(r'[A-Z][A-Z0-9_]*', key.value):
                    keys.setdefault(key.lineno, set()).add(key.value)
    annotated = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and re.search(r'devops.*inject', token.string, re.I):
            annotated.update(keys.get(token.start[0], ()))
    return annotated


def platform_contract(root, manifest=None):
    manifest = manifest or (read_manifest(root) if (root / 'deployment/deploy-manifest.json').exists() else {})
    paths = list((root / '.local/sm').glob('sm_configs.*.py'))
    paths.append(inside(root, manifest.get('schema', '.local/templates/sm_configs.example.py')))
    annotated = set().union(*(injected_annotations(path) for path in paths))
    names = set(manifest.get('devops_injected_env', ())) | annotated
    return tuple(sorted((names or set(PLATFORM)) - set(TOOLING)))


def targets(root, manifest, require_any=True):
    """Explicit targets hoặc glob theo nhóm; không suy service/job khi mapping thiếu."""
    result = {}
    for group, pattern in manifest.get('discovery', {}).items():
        for path in sorted(root.glob(pattern)):
            if '{{' in path.name or not any((path / m).exists() for m in ('package.json', 'pubspec.yaml', 'pyproject.toml', 'go.mod', 'pom.xml')):
                continue
            inside(root, path.relative_to(root))
            result[path.name] = {'group': group, 'path': str(path.relative_to(root))}
    for name, row in manifest.get('targets', {}).items():
        if not NAME.fullmatch(name) or not inside(root, row['path']).is_dir():
            raise ValueError('Target trong manifest không hợp lệ.')
        result[name] = dict(row)
    mapping = manifest.get('mapping_file')
    if mapping:
        for line in inside(root, mapping).read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            source, remote = line.split()
            name = source.rsplit('/', 1)[-1]
            if name in result:
                result[name]['service'] = remote.rsplit('/', 1)[-1]
    for name, row in result.items():
        if not NAME.fullmatch(name) or not NAME.fullmatch(row.get('service', '')):
            raise ValueError(f'Target {name} thiếu mapping service hợp lệ.')
    if not result and require_any:
        raise ValueError('Manifest không khám phá được target nào.')
    return result


def params(manifest, env, group, deploy, source):
    rules = manifest['environments'][env]['parameters'][group]
    context = {'source_env': (source or '').lower(), 'rc': deploy.get('rc_tag') or '',
               'commit': deploy.get('image_tag') or '', 'release_tag': deploy.get('release_tag') or ''}
    return {key: value.format(**context) for key, value in rules.items()}
