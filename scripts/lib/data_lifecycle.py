"""Kiểm kê model Prisma và sổ policy; không đọc DB hoặc thực thi lifecycle."""
import re

REGISTRY = 'context/DATA-LIFECYCLE.md'
SCHEMA = re.compile(r'^srcroot/boundaries/([a-z0-9-]+)/prisma/schema\.prisma$')
TOKEN = re.compile(r'(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)|(?P<string>"(?:\\.|[^"\\])*")|(?P<word>[A-Za-z_][A-Za-z_0-9]*)|(?P<other>\S)')
COLUMNS = ['Target', 'Model', 'Retention', 'Archive', 'Purge', 'Legal hold', 'Trạng thái', 'Bằng chứng']


def parse_models(source):
    if not isinstance(source, str):
        raise ValueError('Source Prisma phải là chuỗi')
    tokens = []
    for match in TOKEN.finditer(source):
        if match.lastgroup == 'comment':
            continue
        if match.lastgroup == 'other' and (match.group() == '"' or source[match.start():].startswith('/*')):
            raise ValueError('Chuỗi/comment Prisma chưa đóng')
        tokens.append((match.group(), match.lastgroup, source.count('\n', 0, match.start()) + 1))
    depth = 0
    models = []
    seen = set()
    for index, (value, kind, line) in enumerate(tokens):
        if depth == 0 and kind == 'word' and value == 'model':
            following = tokens[index+1:index+3]
            if len(following) != 2 or following[0][1] != 'word' or following[1][0] != '{':
                raise ValueError('Khai báo model chưa đọc được')
            name = following[0][0]
            if name in seen:
                raise ValueError('Trùng tên model trong schema')
            seen.add(name)
            models.append({'name': name, 'line': line})
        if kind == 'other' and value in ('{', '}'):
            depth += 1 if value == '{' else -1
            if depth < 0:
                raise ValueError('Ngoặc schema lệch')
    if depth:
        raise ValueError('Schema chưa đóng ngoặc')
    return models


def visible_lines(text):
    """Bỏ ví dụ/comment; marker chỉ có nghĩa khi đứng riêng ngoài khối ẩn."""
    if not isinstance(text, str):
        raise ValueError('Sổ lifecycle phải là chuỗi')
    fence = None
    comment = False
    markers = {'<!-- DATA-LIFECYCLE:BEGIN -->', '<!-- DATA-LIFECYCLE:END -->'}
    for number, line in enumerate(text.splitlines(), 1):
        if fence:
            character, length = fence
            if re.fullmatch(r' {0,3}' + re.escape(character) + '{' + str(length) + r',}[ \t]*', line):
                fence = None
            continue
        if not comment:
            mark = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
            if mark and (mark[1][0] != '`' or '`' not in mark[2]):
                fence = (mark[1][0], len(mark[1]))
                continue
            # Bốn khoảng trắng/tab là code thụt dòng, không phải bảng policy.
            if line.startswith(('    ', '\t')):
                continue
            if line.strip() in markers:
                yield number, line.strip()
                continue
        visible = ''
        position = 0
        while position < len(line):
            if comment:
                closing = line.find('-->', position)
                nested = line.find('<!--', position)
                if nested >= 0 and (closing < 0 or nested < closing):
                    raise ValueError('HTML comment lồng nhau chưa đọc được an toàn')
                if closing < 0:
                    break
                comment, position = False, closing + 3
            else:
                opening = line.find('<!--', position)
                if opening < 0:
                    visible += line[position:]
                    break
                visible += line[position:opening]
                comment, position = True, opening + 4
        yield number, visible.strip()
    if comment:
        raise ValueError('HTML comment trong sổ chưa đóng')


def cell_text(value):
    """Bỏ cách trình bày inline để chữ placeholder không được che bằng Markdown."""
    value = re.sub(r'!?\[([^]\n]*)\]\([^\n)]*\)', r'\1', value.strip())
    value = re.sub(r'<[^>\n]*>', '', value)
    value = re.sub(r'\\([*_`~])', r'\1', value)
    # Chỉ bỏ cặp delimiter ở hai đầu/từng từ; giữ dấu _ bên trong identifier.
    previous = None
    while value != previous:
        previous = value
        value = re.sub(r'(`+|\*{1,3}|~{2})(\S(?:.*?\S)?)\1', r'\2', value).strip()
        value = re.sub(r'(?<!\w)(_{1,3})(\S(?:.*?\S)?)\1(?!\w)', r'\2', value).strip()
    return value


def table_cells(line):
    """Pipe được escape bởi số backslash lẻ là nội dung ô, không phải dấu cột."""
    cells, start, slashes = [], 0, 0
    for position, character in enumerate(line):
        if character == '|' and slashes % 2 == 0:
            cells.append(line[start:position])
            start = position + 1
        slashes = slashes + 1 if character == '\\' else 0
    cells.append(line[start:])
    if cells[0] or cells[-1]:
        raise ValueError('Bảng lifecycle cần dấu | ở hai đầu dòng')
    return [cell_text(cell) for cell in cells[1:-1]]


def parse_registry(text):
    lines = list(visible_lines(text))
    begins = [i for i, (_, line) in enumerate(lines) if line == '<!-- DATA-LIFECYCLE:BEGIN -->']
    ends = [i for i, (_, line) in enumerate(lines) if line == '<!-- DATA-LIFECYCLE:END -->']
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        raise ValueError('Sổ cần đúng một vùng DATA-LIFECYCLE')
    rows = []
    header = False
    for line_number, line in lines[begins[0]+1:ends[0]]:
        if not line.startswith('|'):
            continue
        cells = table_cells(line)
        if cells == COLUMNS:
            header = True
            continue
        if not header or len(cells) != len(COLUMNS):
            raise ValueError('Bảng lifecycle sai cột')
        if all(re.fullmatch(r':?-+:?', cell) for cell in cells):
            continue
        rows.append(dict(zip(COLUMNS, cells), line=line_number))
    if not header:
        raise ValueError('Thiếu header sổ lifecycle')
    return rows


def audit(schemas, registry):
    if not isinstance(schemas, dict):
        raise ValueError('Danh sách schema phải là ánh xạ đường dẫn/source')
    models = []
    if any(not isinstance(path, str) for path in schemas):
        raise ValueError('Đường dẫn schema phải là chuỗi')
    for path, source in sorted(schemas.items()):
        match = SCHEMA.fullmatch(path) if isinstance(path, str) else None
        if not match:
            raise ValueError('Schema ngoài phạm vi')
        models.extend(dict(item, file=path, target=match.group(1)) for item in parse_models(source))
    rows = parse_registry(registry)
    findings = []
    def add(file, model, rule, message):
        findings.append({'file': file, 'model': model, 'rule': rule, 'level': 'error', 'message': message})
    by_key = {}
    duplicates = set()
    confirmed = set()
    for row in rows:
        key = (row['Target'], row['Model'])
        if key in by_key:
            duplicates.add(key)
            add(REGISTRY, '.'.join(key), 'DL004', 'Model được khai nhiều dòng trong sổ')
        by_key[key] = row
        if not re.fullmatch(r'[a-z0-9-]+', key[0]) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key[1]):
            add(REGISTRY, '.'.join(key), 'DL003', 'Khóa target.Model không hợp lệ')
    expected = {(m['target'], m['name']) for m in models}
    for model in models:
        key = (model['target'], model['name'])
        identity = '.'.join(key)
        row = by_key.get(key)
        if row is None:
            add(model['file'], identity, 'DL001', 'Model chưa có dòng trong DATA-LIFECYCLE')
            continue
        status = row['Trạng thái']
        if status == 'pending':
            add(model['file'], identity, 'DL002', 'Đã kiểm kê nhưng chính sách vòng đời chưa chốt đủ')
        fields = [row[name] for name in COLUMNS[2:6]] + [row['Bằng chứng']]
        placeholder = any(re.match(r'^(unknown|pending|chưa)\b', field, re.I) or field.lower() == 'n/a' for field in fields)
        if status not in ('confirmed', 'pending') or any(not field for field in fields) or (status == 'confirmed' and placeholder):
            add(model['file'], identity, 'DL003', 'Trạng thái/nội dung policy chưa hợp lệ hoặc chỉ là placeholder')
        elif status == 'confirmed' and key not in duplicates:
            confirmed.add(key)
    for key in by_key.keys() - expected:
        add(REGISTRY, '.'.join(key), 'DL005', 'Dòng trong sổ không có model tương ứng ở schema đang kiểm')
    return {'models': models, 'findings': findings, 'errors': len(findings), 'warnings': 0,
            'documented': sum((m['target'], m['name']) in by_key for m in models),
            'confirmed': len(confirmed)}
