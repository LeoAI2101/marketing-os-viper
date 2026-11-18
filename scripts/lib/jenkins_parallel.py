"""Gửi toàn bộ job đồng thời; chờ và cập nhật ledger độc lập, không fail-fast."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from release_ledger import OK_RESULTS


def run_parallel(api, cfg, env, run, rows, args):
    path = args.ledger or api.ledger_path(env)
    failures = []

    def trigger(row):
        target = row['target']
        url = api.job_url(cfg, target, env, row['service'])
        params = api.deploy_params(row['group'], row['deploy'], run.get('source_env'), env)
        try:
            queue = api.trigger_build(cfg, url, params)
            api.save_deploy(path, env, run['id'], target, status='queued',
                            jenkins={'job_url': url, 'queue_url': queue, 'triggered_at': api.now()})
            return row, queue
        except (Exception, SystemExit):
            api.save_deploy(path, env, run['id'], target, status='unknown')
            print(f'✗ {target}: chưa rõ Jenkins đã nhận POST; đối chiếu job trước khi retry.')
            failures.append(target)
            return None

    def wait(item):
        row, queue = item
        target = row['target']
        try:
            url = api.wait_for_build_start(cfg, queue)
            api.save_deploy(path, env, run['id'], target, build_url=url)
            if getattr(args, 'auto_approve', False):
                result = api.wait_for_build_finish(cfg, url, auto_approve=True)
            else:
                result = api.wait_for_build_finish(cfg, url)
            # Build xong mà vàng (UNSTABLE: Trivy/Sonar) vẫn đã lên ArgoCD: tính là deploy xong.
            ok = result in OK_RESULTS
            api.save_deploy(path, env, run['id'], target, status='success' if ok else 'failed',
                            result=result, finished_at=api.now())
            print(f'{target}: {result} — {url}')
            if not ok:
                failures.append(target)
        except (Exception, SystemExit):
            print(f'✗ {target}: còn queued/running/chờ duyệt; sync để xác định kết quả.')
            failures.append(target)

    with ThreadPoolExecutor(max_workers=len(rows)) as pool:
        futures = [pool.submit(trigger, row) for row in rows]
        queued = [value for f in as_completed(futures) if (value := f.result())]
        if args.wait:
            for f in as_completed([pool.submit(wait, item) for item in queued]):
                f.result()
    if args.wait:
        api.sync_ledger(env, [r['target'] for r in rows], args.ledger)
    if failures:
        raise SystemExit(1)
