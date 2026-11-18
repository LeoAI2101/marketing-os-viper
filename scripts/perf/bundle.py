#!/usr/bin/env python3
"""Gom artifact đo thật thành một gói bằng chứng, giữ hash từng file và phần còn thiếu.

Không tự tạo `accepted-<env>.json` — gói đạt rồi người chạy mới đổi tên, sau khi
Authority đọc báo cáo. Exit 0 đạt · 1 đỏ/thiếu · 2 artifact hỏng hoặc output đã có.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.perf_evidence import assess  # noqa: E402
from lib.perf_report import load_workload  # noqa: E402

ARTIFACTS = ('load', 'peak', 'soak', 'stress', 'recovery', 'plans', 'baseline', 'current', 'telemetry')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ARTIFACTS:
        parser.add_argument('--' + key, type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    wl = load_workload(ROOT)
    bundle = dict(schema_version=1, collected_at=datetime.now(timezone.utc).isoformat(), artifacts={},
                  scope='probe', environment=None, source_hash=None, workload_hash=None)
    sources = []
    for key in ARTIFACTS:
        path = getattr(args, key)
        if path is None:
            continue
        raw = path.read_bytes()
        bundle['artifacts'][key] = dict(file=path.name, sha256=hashlib.sha256(raw).hexdigest())
        if key == 'telemetry':  # JSONL, mỗi dòng một mẫu soak
            bundle.setdefault('soak', {})['samples'] = [json.loads(line) for line in raw.splitlines() if line.strip()]
        elif key in ('baseline', 'current'):
            bundle.setdefault('regression', {})[key] = json.loads(raw)
        else:
            bundle.setdefault(key, {}).update(json.loads(raw))
            if key in ('load', 'peak', 'soak'):
                sources.append(bundle[key])
    if sources:
        for field in ('source_hash', 'environment', 'workload_hash'):
            bundle[field] = sources[0].get(field)
        if all(s.get('scope') == 'full' for s in sources):
            bundle['scope'] = 'full'
    bundle['assessment'] = assess(bundle, wl)
    bundle['success'] = not bundle['assessment']['errors']
    if any(s.get(f) != bundle[f] for s in sources for f in ('source_hash', 'environment', 'workload_hash')):
        bundle['success'] = False
        bundle['assessment']['errors'].append('Các lượt đo không cùng source/môi trường/workload')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as out:
        json.dump(bundle, out, ensure_ascii=False, indent=2)
    print(json.dumps(bundle['assessment'], ensure_ascii=False, indent=2))
    return 0 if bundle['success'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'✗ Artifact thiếu/hỏng hoặc output đã tồn tại ({type(exc).__name__}).', file=sys.stderr)
        sys.exit(2)
