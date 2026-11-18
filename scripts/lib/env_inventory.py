"""Kiểm kê env tĩnh nhiều stack; báo riêng truy cập động cần rà."""
import ast
import os
import re
from pathlib import Path

KEY = r'[A-Z][A-Z0-9_]*'
SKIP = {'node_modules', '.git', 'build', 'dist', 'coverage', '.dart_tool', '.venv', 'generated', '__pycache__'}
SUFFIXES = {'.ts', '.tsx', '.js', '.mjs', '.cjs', '.py', '.go', '.java', '.dart'}


def source_files(path, skip=SKIP):
    if path.is_file():
        yield path
        return
    for directory, dirs, names in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in skip and not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            file = Path(directory) / name
            if not file.is_symlink():
                yield file


def scalar(text):
    text = text.strip()
    if text in ('true', 'false'):
        return text
    if re.fullmatch(r'\d+', text):
        return text
    try:
        value = ast.literal_eval(text)
        return str(value) if isinstance(value, (str, int, float)) else None
    except (ValueError, SyntaxError):
        return None


def without_comments(source):
    """Giữ số dòng và literal; bỏ comment JS/Python."""
    pattern = r'''("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(/\*[\s\S]*?\*/|//[^\n]*|\#[^\n]*)'''
    return re.sub(pattern, lambda m: m.group(1) or re.sub(r'[^\n]', ' ', m.group(2)), source)


def scan_text(source, file, helpers):
    text = without_comments(source)
    constants = dict(re.findall(r'\b(?:const|final|let)\s+(\w+)[^=\n]*=\s*[\'"](' + KEY + r')[\'"]', text))
    found, unresolved = {}, []
    patterns = [r'\b(?:process\.env|import\.meta\.env|env)\.(' + KEY + r')',
                r'\b(?:process\.env|import\.meta\.env|env|os\.environ)\[\s*[\'"](' + KEY + r')[\'"]\s*\]',
                r'\b(?:getenv|Getenv|System\.getenv|environ\.get|os\.getenv|String\.fromEnvironment)\(\s*[\'"](' + KEY + r')[\'"]',
                r'[\'"]?(' + KEY + r')[\'"]?\s*:\s*z\.',
                r'\b(?:get|requireEnv|requireBaseUrl|' + '|'.join(re.escape(h) for h in helpers) + r')\([^\n;]*?[\'"](' + KEY + r')[\'"]']
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            key = match.group(1)
            line = text.count('\n', 0, match.start()) + 1
            tail = text[match.end():].split('\n', 1)[0].split(';', 1)[0]
            if re.match(r'[\'"]?\s*:\s*z\.', text[match.start() + len(key):match.end()]):
                tail = tail.split(',', 1)[0]
            # Gán vào process.env là giá trị tự dựng, không phải đầu vào bên ngoài.
            if re.match(r'\s*=(?!=)', tail):
                continue
            default = None
            fallback = re.search(r'''(?:\?\?|\|\||\.default\()(\s*(?:'[^']*'|"[^"]*"|true|false|\d+))''', tail)
            if fallback is None and not re.search(r'\brequire\w*\(', match.group(0)):
                fallback = re.match(r'''\s*,(\s*(?:'[^']*'|"[^"]*"|true|false|\d+))''', tail)
            if fallback:
                default = scalar(fallback.group(1))
            row = found.setdefault(key, {'locations': [], 'defaults': [], 'optional': False, 'required': False})
            row['locations'].append(f'{file}:{line}')
            if default is not None:
                row['defaults'].append(default)
            if '.optional(' in tail or fallback:
                row['optional'] = True
            else:
                row['required'] = True
    for match in re.finditer(r'\b(?:process\.env|import\.meta\.env|env|os\.environ)\[\s*(\w+)\s*\]', text):
        name = match.group(1)
        if name in constants:
            row = found.setdefault(constants[name], {'locations': [], 'defaults': [], 'optional': False, 'required': True})
            row['locations'].append(f'{file}:{text.count(chr(10), 0, match.start()) + 1}')
        else:
            unresolved.append(f'{file}:{text.count(chr(10), 0, match.start()) + 1}')
    for match in re.finditer(r'\b(?:const|let|var)\s*\{([^}]+)\}\s*=\s*(?:process\.env|env)', text):
        for part in match.group(1).split(','):
            key = re.match(r'\s*(' + KEY + r')\b', part)
            if key:
                row = found.setdefault(key.group(1), {'locations': [], 'defaults': [], 'optional': False, 'required': True})
                row['locations'].append(f'{file}:{text.count(chr(10), 0, match.start()) + 1}')
            else:
                unresolved.append(f'{file}:{text.count(chr(10), 0, match.start()) + 1}')
    # Wrapper parameter accesses are resolved by literal calls above, but still need review.
    return found, unresolved


def inventory(root, target, helpers):
    result, dynamic = {}, []
    path = root / target['path']
    for file in source_files(path):
        if not file.is_file() or file.suffix not in SUFFIXES or SKIP.intersection(file.parts):
            continue
        if any(mark in file.name for mark in ('.spec.', '.test.', '_test.', '.g.dart')) or 'tests' in file.parts or '__tests__' in file.parts:
            continue
        rows, unknown = scan_text(file.read_text(), file.relative_to(root), helpers)
        dynamic.extend(unknown)
        for key, row in rows.items():
            current = result.setdefault(key, {'locations': [], 'defaults': [], 'optional': False, 'required': False})
            current['locations'].extend(row['locations'])
            current['defaults'].extend(row['defaults'])
            current['optional'] |= row['optional']
            current['required'] |= row['required']
    return result, dynamic
