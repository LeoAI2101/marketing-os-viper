#!/usr/bin/env python3
"""Xác minh gói bằng chứng (bảy tiêu chí), một lượt stress hoặc một chuỗi recovery; không phát tải, không tiêm lỗi.

Exit 0 đạt · 1 đỏ · 2 chưa kiểm được.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.perf_evidence import assess, recovery_time, stress_result  # noqa: E402
from lib.perf_report import load_workload  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--kind', choices=['suite', 'recovery', 'stress'], default='suite')
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    wl = load_workload(ROOT)
    data = json.loads(args.input.read_text())
    if args.kind == 'suite':
        result = assess(data, wl)
    elif args.kind == 'stress':
        result = stress_result(data, wl)
        need = wl['stress']['headroom']
        result['errors'] = [] if result['headroom'] >= need else [f'Headroom <{need}']
    else:
        seconds = recovery_time(data, wl)
        limit = wl['recovery']['max_seconds']
        result = dict(recovery_seconds=seconds,
                      errors=[] if seconds is not None and seconds <= limit else [f'Chưa phục hồi SLO ≤{limit} giây'])
    output = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        with args.output.open('x') as out:
            out.write(output)
    print(output, end='')
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'✗ Thiếu hoặc sai bằng chứng ({type(exc).__name__}); không in dữ liệu có thể chứa bí mật.', file=sys.stderr)
        sys.exit(2)
