#!/usr/bin/env python3
"""Kiểm ngân sách khai báo; exit 0 đủ bằng chứng, 1 vi phạm/thiếu bằng chứng, 2 đầu vào hỏng."""
import argparse
import json
from pathlib import Path
import sys
from lib.ops_env import discover_targets
from lib.connection_budget import evaluate, provider_candidates, read_pools, source_hash, validate_observation

ROOT = Path(__file__).resolve().parent.parent
ENVS = ('SIT', 'UAT', 'PREPROD', 'PROD', 'PT', 'LOCAL')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=str.upper, choices=ENVS, default='SIT')
    parser.add_argument('--file', type=Path, default=ROOT / 'context/perf/CONNECTION-BUDGET.json')
    parser.add_argument('--observation', type=Path, help='Snapshot PT chỉ có số đã lọc; không phải env chứa secret')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--expected-host', help='Host PT đã biết độc lập, không sao từ snapshot')
    parser.add_argument('--expected-project', help='Compose project PT đã biết độc lập')
    parser.add_argument('--expected-db-instance', help='Postgres system_identifier đã biết độc lập')
    args = parser.parse_args()
    plan = json.loads(args.file.read_text())
    profile = plan['environments'][args.env]
    targets = [name for name in discover_targets(('boundaries',))
               if (ROOT / 'srcroot/boundaries' / name / 'prisma/schema.prisma').exists()]
    if not targets:
        raise ValueError('Không có boundary Prisma để kiểm')
    candidates = provider_candidates(ROOT, targets)
    pools = read_pools(ROOT / f'.local/sm/sm_configs.{args.env.lower()}.py', targets)
    observation_errors = []
    if args.observation:
        if args.env not in ('PT', 'LOCAL'):
            raise ValueError('Observation chỉ áp dụng PT/local, không thay HPA môi trường deploy')
        observed = json.loads(args.observation.read_text())
        expected = dict(host=args.expected_host, project_id=args.expected_project,
                        db_instance_id=args.expected_db_instance)
        observation_errors = validate_observation(observed, args.env, targets, expected,
                                                  source_hash(ROOT, targets))
        if not observation_errors:
            pools = observed['pool_limits']
            profile['max_connections'] = observed['max_connections']
            profile['max_connections_confirmed'] = True
            profile['extras']['keycloak'] = observed['keycloak_connections']
            profile['extras']['reserved'] = observed['reserved_connections']
    result = evaluate(profile, targets, pools, candidates)
    result['errors'].extend(observation_errors)
    result.update(environment=args.env, source=str(args.file.relative_to(ROOT) if args.file.is_relative_to(ROOT) else args.file.name),
                  evidence=('snapshot PT được đối chiếu identity/source' if not observation_errors else 'snapshot không đủ bằng chứng; chưa áp số quan sát') if args.observation else 'cấu hình khai báo, chưa truy vấn môi trường',
                  source_sha256=source_hash(ROOT, targets),
                  limits=['Quét provider bảo thủ, không chứng minh graph DI/runtime; alias/factory/module động phải rà riêng.',
                          'Expected identity phải lấy độc lập; snapshot không có chữ ký và không chứng minh runtime đang chạy đúng source.',
                          'Không suy Keycloak pool từ default hoặc số session đang mở.'])
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for row in result['rows']:
            print(f"{row['target']}: {row['pods']} pod × {row['pool_instances']} pool × {row['pool']} = {row['connections']}")
        for error in result['errors']:
            print('✗ ' + error)
        print(f"{'✗' if result['errors'] else '✓'} {args.env}: tổng {result['total']}; phần biết {result['known_total']}; trần {result['capacity']}")
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit as stop:
        # ops_env thoát bằng thông điệp khi repo chưa có target nào: đó là CHƯA KIỂM (2), không phải đỏ (1).
        if isinstance(stop.code, str):
            print(stop.code, file=sys.stderr)
            sys.exit(2)
        raise
    except (OSError, ValueError, KeyError, TypeError, SyntaxError):
        print('✗ connection-budget: đầu vào thiếu/hỏng; kiểm cấu trúc JSON và số pool, không in cấu hình bí mật', file=sys.stderr)
        sys.exit(2)
