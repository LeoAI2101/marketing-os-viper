#!/usr/bin/env python3
"""Kiểm sổ lifecycle trên source hiện tại, không cần DB/dependency Node."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from lib.data_lifecycle import SCHEMA, REGISTRY, audit
from lib.ops_env import discover_targets

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file')
    parser.add_argument('--stdin', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if args.stdin and not args.file:
        parser.error('--stdin cần --file')
    names = discover_targets(('boundaries',))
    schemas = {}
    for name in names:
        path = f'srcroot/boundaries/{name}/prisma/schema.prisma'
        if (ROOT/path).is_file():
            schemas[path] = (ROOT/path).read_text()
    if args.file:
        path = Path(args.file)
        path = (path if path.is_absolute() else ROOT/path).resolve().relative_to(ROOT).as_posix()
        match = SCHEMA.fullmatch(path)
        if not match or match.group(1) not in names:
            raise ValueError('File ngoài schema của boundary đã khai')
        schemas[path] = sys.stdin.read() if args.stdin else (ROOT/path).read_text()
    if not schemas:
        raise ValueError('Không có schema để đối chiếu')
    registry = (ROOT/REGISTRY).read_text()
    report = audit(schemas, registry)
    report.update(kind='data-lifecycle-static', schema_files=len(schemas),
                  input_sha256=hashlib.sha256(json.dumps([schemas, registry], sort_keys=True).encode()).hexdigest(),
                  scope='--file thay source một schema, vẫn đối chiếu toàn bộ sổ để không báo dòng target khác là thừa',
                  limits=['Parser lexical chỉ kiểm kê model, không thay prisma validate.',
                          'Confirmed là khai báo có nội dung, không chứng minh purge/hold đã chạy hoặc policy đúng nghiệp vụ.'])
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for finding in report['findings']:
            print(f"✗ {finding['model']} {finding['rule']}: {finding['message']}")
        print(f"Lifecycle: {len(report['models'])} model, {report['documented']} có dòng, {report['confirmed']} confirmed, {report['errors']} lỗi")
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit as stop:
        # ops_env thoát bằng thông điệp khi repo chưa có target nào: đó là CHƯA KIỂM (2), không phải đỏ (1).
        if isinstance(stop.code, str):
            print(stop.code, file=sys.stderr)
            sys.exit(2)
        raise
    except (OSError, ValueError, KeyError):
        print('Chưa kiểm được lifecycle: kiểm schema, sổ và tham số; không xuất nội dung private.', file=sys.stderr)
        sys.exit(2)
