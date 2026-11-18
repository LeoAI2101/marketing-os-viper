"""Tính trần kết nối; không mở DB, không chỉnh pool hoặc suy RPS từ phép cộng."""
import ast
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path

EXTRAS = ('keycloak', 'migration', 'operations', 'reserved')


def integer(value, label, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{label}: cần số nguyên >= {minimum}')
    return value


def pod_count(replicas, surge):
    replicas = integer(replicas, 'max_replicas')
    if isinstance(surge, str) and re.fullmatch(r'\d+%', surge):
        extra = (replicas * int(surge[:-1]) + 99) // 100
    else:
        extra = integer(surge, 'surge', 0)
    return replicas + extra


def evaluate(profile, targets, pools, candidates):
    errors, unconfirmed, rows = [], [], []
    capacity = None
    if profile.get('max_connections') is None:
        errors.append('Thiếu max_connections')
    else:
        capacity = integer(profile['max_connections'], 'max_connections') * 80 // 100
    for field in ('max_connections_confirmed', 'topology_confirmed', 'extras_confirmed'):
        if profile.get(field) is not True:
            unconfirmed.append(field)
            errors.append(f'Chưa xác nhận {field}; phép tính chưa đủ chứng minh deploy')
    boundaries = profile['boundaries']
    if set(boundaries) != set(targets):
        errors.append('Bảng boundary lệch manifest/Prisma: ' + ', '.join(sorted(set(boundaries) ^ set(targets))))
    complete, known = True, 0
    for target in targets:
        row = boundaries.get(target)
        if row is None:
            complete = False
            continue
        pods = pod_count(row['max_replicas'], row['surge'])
        instances = integer(row['pool_instances'], f'{target}.pool_instances')
        modules = candidates.get(target, [])
        for reason in getattr(candidates, 'uncertainties', {}).get(target, []):
            errors.append(f'{target}: chưa chứng minh số pool DI: {reason}')
        if not modules or len(modules) > instances:
            errors.append(f'{target}: khai {instances} pool/pod nhưng thấy {len(modules)} module provider; cần rà DI')
        pool = pools.get(target)
        connections = None
        if pool is None:
            errors.append(f'{target}: thiếu DB_CONNECTION_LIMIT xác định')
            complete = False
        else:
            pool = integer(pool, f'{target}.DB_CONNECTION_LIMIT')
            connections = pods * instances * pool
            known += connections
        rows.append(dict(target=target, pods=pods, pool_instances=instances, pool=pool,
                         connections=connections, provider_modules=modules))
    extras = profile['extras']
    if set(extras) != set(EXTRAS):
        raise ValueError('extras phải khai đủ keycloak/migration/operations/reserved, không có khoá khác')
    for name in EXTRAS:
        value = extras[name]
        if value is None:
            complete = False
            errors.append(f'Thiếu ngân sách {name}')
        else:
            known += integer(value, name, 0)
    total = known if complete else None
    if capacity is not None and known > capacity:
        errors.append(f'Vượt trần: ít nhất {known} > {capacity}')
    return dict(rows=rows, total=total, known_total=known, capacity=capacity,
                errors=errors, unconfirmed=unconfirmed)


# Lexer bảo thủ, không thay TypeScript/Nest graph resolver. String/comment không
# được tạo PrismaService giả; template interpolation phải rà vì có thể khởi tạo pool.
TS_TOKEN = re.compile(r"(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)|(?P<string>'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`)|(?P<word>[A-Za-z_$][A-Za-z_$0-9]*)|(?P<symbol>\.\.\.|\S)")


class ProviderCandidates(dict):
    def __init__(self):
        super().__init__()
        self.uncertainties = {}


def source_files(root, targets):
    paths = set()
    for target in targets:
        base = root / 'srcroot/boundaries' / target
        for name in ('package.json', 'package-lock.json', 'prisma/schema.prisma'):
            if (base / name).is_file():
                paths.add(base / name)
        paths.update(path for path in (base / 'src').rglob('*.ts')
                     if not re.search(r'\.(spec|test|d)\.ts$', path.name))
    for path in sorted(paths):
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Source của boundary nằm ngoài repo')
        yield path


def source_hash(root, targets):
    """Hash nội dung source/schema/manifest, độc lập đường dẫn máy và không đọc env."""
    digest = hashlib.sha256()
    digest.update(json.dumps(sorted(targets)).encode())
    for path in source_files(root, targets):
        data = path.read_bytes()
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        digest.update(str(len(data)).encode() + b'\0' + data)
    return digest.hexdigest()


def _tokens(source):
    return [(match.group(), match.lastgroup) for match in TS_TOKEN.finditer(source)
            if match.lastgroup != 'comment']


def _aliases(sources):
    aliases = {'PrismaService', 'PrismaClient'}
    changed = True
    while changed:
        before = set(aliases)
        for tokens in sources.values():
            for index, (name, kind) in enumerate(tokens):
                if kind != 'word':
                    continue
                following = [value for value, _ in tokens[index+1:index+3]]
                if name in aliases and len(following) == 2 and following[0] == 'as':
                    aliases.add(following[1])
                if len(following) == 2 and following[0] in ('=', 'extends') and following[1] in aliases:
                    aliases.add(name)
        changed = aliases != before
    return aliases


def _provider_list(tokens, start):
    if start >= len(tokens) or tokens[start][0] != '[':
        return None
    depth, body = 0, []
    for value, kind in tokens[start:]:
        if kind == 'symbol' and value in ('[', ']'):
            depth += 1 if value == '[' else -1
            if depth == 0:
                return body
        body.append((value, kind))
    return None


# Gói Nest không biết `PrismaService`/`PrismaClient` của boundary nên module động của chúng không thể
# tạo thêm pool. Chỉ miễn khi tên được import ĐÍCH DANH từ gói này (2026-10-05: `ConfigModule.forRoot`
# ở mọi boundary là nguồn duy nhất của dòng "module động"); module tự viết hay từ gói khác vẫn phải rà.
_EXTERNAL_DYNAMIC_MODULE_PACKAGES = ('@nestjs/config', '@nestjs/throttler', '@nestjs/schedule', '@nestjs/terminus',
                          '@nestjs/event-emitter', '@nestjs/axios')


def _external_dynamic_modules(text):
    names = set()
    for spec, package in re.findall(r"import\s*\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]", text):
        if package in _EXTERNAL_DYNAMIC_MODULE_PACKAGES:
            for item in spec.split(','):
                parts = item.split()
                if len(parts) == 1:
                    names.add(parts[0])
                elif len(parts) == 3 and parts[1] == 'as':
                    names.add(parts[2])
    return names


def provider_candidates(root, targets):
    result = ProviderCandidates()
    for target in targets:
        texts = {path.relative_to(root).as_posix(): path.read_text()
                 for path in source_files(root, [target])
                 if path.suffix == '.ts' and '/contracts/' not in path.as_posix()}
        sources = {path: _tokens(text) for path, text in texts.items()}
        aliases = _aliases(sources)
        modules, uncertain = [], set()
        for path, tokens in sources.items():
            words = [value if kind != 'string' else '' for value, kind in tokens]
            for index, (value, kind) in enumerate(tokens):
                if kind == 'string' and value.startswith('`') and '${' in value:
                    # Chưa phân tích expression trong template; không đoán nó vô hại.
                    if any(alias in value for alias in aliases):
                        uncertain.add(path + ': Prisma trong template expression')
                if kind == 'string' and value in ("'providers'", '"providers"'):
                    value, kind = 'providers', 'word'
                    if words[index+1:index+2] == [']']:
                        uncertain.add(path + ': khóa providers tính động cần rà')
                if kind != 'word':
                    continue
                if value == 'new':
                    constructor = []
                    for token in words[index+1:index+8]:
                        if token == '(':
                            break
                        constructor.append(token)
                    if any(token in aliases for token in constructor):
                        uncertain.add(path + ': khởi tạo Prisma ngoài danh sách provider')
                if value in ('forRoot', 'forRootAsync', 'forFeature', 'forFeatureAsync', 'register', 'registerAsync') and words[index+1:index+2] == ['(']:
                    receiver = words[index-2] if index >= 2 and words[index-1] == '.' else None
                    if receiver not in _external_dynamic_modules(texts[path]):
                        uncertain.add(path + ': đăng ký module động cần rà graph')
                if value == 'providers' and words[index+1:index+2] in ([','], ['}']):
                    uncertain.add(path + ': providers shorthand chưa được giải quyết')
                if value != 'providers' or words[index+1:index+2] != [':']:
                    continue
                body = _provider_list(tokens, index + 2)
                if body is None:
                    uncertain.add(path + ': providers không phải mảng literal đọc được')
                    continue
                identifiers = [item for item, token_kind in body if token_kind == 'word']
                if any(alias in identifiers for alias in aliases):
                    modules.append(path)
                if any(item in ('useFactory', 'useClass', 'useExisting', 'useValue') for item in identifiers) or any(item in ('...', '(', '?') for item, kind in body if kind == 'symbol'):
                    uncertain.add(path + ': factory/alias/spread provider chưa chứng minh số instance')
        result[target] = modules
        result.uncertainties[target] = sorted(uncertain)
    return result


def validate_observation(observed, environment, targets, expected, current_hash, now=None):
    """Identity mong đợi do người gọi cung cấp độc lập; không tự lấy từ snapshot."""
    if not isinstance(observed, dict) or not isinstance(expected, dict):
        raise ValueError('Snapshot/identity phải là object')
    errors = []
    for field in ('host', 'project_id', 'db_instance_id'):
        actual, wanted = observed.get(field), expected.get(field)
        if not isinstance(actual, str) or not actual.strip():
            errors.append(f'Snapshot thiếu {field}')
        if not isinstance(wanted, str) or not wanted.strip():
            errors.append(f'Thiếu mốc expected độc lập cho {field}')
        elif actual != wanted:
            errors.append(f'Snapshot không khớp expected {field}')
    if observed.get('source_sha256') != current_hash:
        errors.append('Snapshot thiếu/lệch source_sha256 so với source hiện tại')
    if observed.get('environment') != environment:
        errors.append('Snapshot không đúng môi trường')
    stamp = datetime.fromisoformat(observed['observed_at'])
    if stamp.tzinfo is None:
        errors.append('Snapshot observed_at thiếu múi giờ')
    else:
        age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
        if age < 0 or age > 86400:
            errors.append('Snapshot PT quá hạn 24 giờ hoặc ở tương lai')
    pools = observed.get('pool_limits')
    if not isinstance(pools, dict):
        raise ValueError('Snapshot thiếu bảng pool_limits')
    if set(pools) != set(targets):
        errors.append('Snapshot pool không khớp đủ target')
    for target, pool in pools.items():
        if pool is not None:
            integer(pool, target + '.pool', 1)
    integer(observed['max_connections'], 'max_connections')
    for field in ('keycloak_connections', 'reserved_connections'):
        value = observed[field]
        if value is not None:
            integer(value, field, 0)
    return errors


def read_pools(path, targets):
    """Chỉ trích số pool, không import/thực thi Python cấu hình hoặc in secret."""
    if not path.exists():
        return {name: None for name in targets}
    tree = ast.parse(path.read_text())
    assignments = [n.value for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'SM_CONFIGS' for t in n.targets)]
    if len(assignments) != 1:
        raise ValueError('Cấu hình cần đúng một SM_CONFIGS hằng')
    data = ast.literal_eval(assignments[0])
    values = {}
    for name in targets:
        raw = data.get(name, {}).get('DB_CONNECTION_LIMIT')
        if raw in (None, ''):
            values[name] = None
        elif isinstance(raw, str) and re.fullmatch(r'[1-9]\d*', raw):
            values[name] = int(raw)
        else:
            values[name] = integer(raw, f'{name}.DB_CONNECTION_LIMIT')
    return values
