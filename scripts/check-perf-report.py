#!/usr/bin/env python3
"""So hồi quy ba lượt perf, hoặc canh gói bằng chứng trước deploy. Không phát tải.

  --baseline A.json --current B.json   mỗi file là mảng đúng ba báo cáo
  --gate --env SIT [--current gói.json] mặc định context/perf/reports/accepted-<env>.json

Exit 0 đạt · 1 đỏ · 2 chưa kiểm được (thiếu/hỏng báo cáo) — 2 không bao giờ là xanh.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from lib.perf_report import gate, load_workload, regression, source_hash

ROOT = Path(__file__).resolve().parent.parent


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--baseline', type=Path)
    p.add_argument('--current', type=Path)
    p.add_argument('--env')
    p.add_argument('--gate', action='store_true')
    a = p.parse_args()
    workload = load_workload(ROOT)
    if a.gate:
        if not a.env:
            p.error('--gate cần --env <môi trường deploy>')
        path = a.current or ROOT / 'context/perf/reports' / f'accepted-{a.env.lower()}.json'
        result = {'errors': gate(json.loads(path.read_text()), source_hash(ROOT), a.env, workload)}
    else:
        if not a.baseline or not a.current:
            p.error('Cần --baseline và --current, mỗi file một bộ ba lượt')
        result = regression(json.loads(a.baseline.read_text()), json.loads(a.current.read_text()),
                            workload.get('max_server_error_rate', .001))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f'✗ Báo cáo perf thiếu/hỏng hoặc điều kiện đo không so sánh được: {exc}', file=sys.stderr)
        sys.exit(2)
