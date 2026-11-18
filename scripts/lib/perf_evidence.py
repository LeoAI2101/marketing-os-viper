"""Đọc gói bằng chứng hiệu năng theo bảy tiêu chí; thiếu dữ liệu khác với đo được mà không đạt.

Mọi ngưỡng lấy từ `workload` (khối ```json của context/perf/WORKLOAD.md):

  latency     p95/p99/lỗi từng nhóm ở lượt load
  throughput  load + peak giữ đủ nhịp, đủ thời gian, đủ người, đúng cơ cấu nhóm
  soak        giữ lâu: RSS phẳng, không restart, hàng đợi về 0 mỗi cửa sổ, stream không phình
  headroom    stress tới điểm gãy, trần ≥ headroom × reference_rps
  recovery    spike đạt đỉnh kịp + mỗi fault trở lại SLO trong max_seconds
  plans       EXPLAIN đường nóng không Seq Scan trên bảng lớn
  regression  ba lượt nền vs ba lượt mới (lib.perf_report.regression)
"""
import math
import statistics
from lib.perf_report import regression

CRITERIA = ('latency', 'throughput', 'soak', 'headroom', 'recovery', 'plans', 'regression')


def number(value, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError('Số đo thiếu, không hữu hạn hoặc sai kiểu')
    return value


def integer(value, minimum=0):
    if number(value, minimum) != int(value):
        raise ValueError('Số đếm phải là số nguyên')
    return value


def metric_errors(name, group, limit, wl):
    integer(group['requests'], 1)
    p95, p99 = number(group['p95_ms'], .000001), number(group['p99_ms'])
    errors = []
    if p95 > limit or p99 > wl.get('p99_factor', 2.5) * p95 or p99 < p95:
        errors.append(f'{name}: p95/p99 ngoài ngưỡng')
    semantic, server = number(group['semantic_error_rate']), number(group['server_error_rate'])
    if semantic > 1 or server > 1:
        raise ValueError('Tỷ lệ lỗi ngoài [0,1]')
    if semantic != 0 or server >= wl.get('max_server_error_rate', .001):
        errors.append(f'{name}: lỗi nghiệp vụ/5xx')
    return errors


def latency(groups, wl, full=True):
    spec = wl['groups']
    if not isinstance(groups, dict) or not groups or set(groups) - set(spec):
        raise ValueError('Thiếu nhóm hoặc nhóm không thuộc WORKLOAD')
    errors = []
    for name in (spec if full else groups):
        errors.extend(metric_errors(name, groups[name], number(spec[name]['p95_ms'], .000001), wl))
    return errors


def load_contract(run, limits, wl):
    errors = []
    for key, limit in [('duration_seconds', limits['duration_seconds']), ('warmup_seconds', limits['warmup_seconds']),
                       ('achieved_rps', limits['rps']), ('active_users', limits.get('active_users', 0))]:
        if number(run[key]) < limit:
            errors.append(f'{key} < {limit}')
    if run['scope'] != 'full' or number(run['dropped_iterations']) != 0:
        errors.append('Phạm vi thiếu hoặc có rơi lượt')
    total = integer(run['requests'], 1)
    if abs(total / number(run['duration_seconds'], 1) - run['achieved_rps']) > .01 * run['achieved_rps']:
        errors.append('RPS không khớp số request/thời gian')
    spec = wl['groups']
    counts = {k: integer(run['groups'][k]['requests']) for k in spec}
    if sum(counts.values()) != total:
        errors.append('Tổng request theo nhóm không khớp')
    weight_sum = sum(number(g['weight'], .000001) for g in spec.values())
    for key, g in spec.items():
        if abs(counts[key] / total * 100 - g['weight'] / weight_sum * 100) > wl.get('mix_tolerance_pct', 1):
            errors.append(f'{key}: cơ cấu thực lệch quá {wl.get("mix_tolerance_pct", 1)} điểm phần trăm')
    return errors


def throughput(bundle, wl):
    return load_contract(bundle['load'], wl['load'], wl) + load_contract(bundle['peak'], wl['peak'], wl)


def soak_series(run, wl):
    cfg = wl['soak']
    step = cfg.get('sample_interval_seconds', 300)
    samples = run['samples']
    if len(samples) < cfg['duration_seconds'] // step + 1:
        raise ValueError('Thiếu chuỗi mẫu phủ cả lượt soak')
    times = [number(s['elapsed_seconds']) for s in samples]
    errors = []
    if times[0] != 0 or times[-1] < run['duration_seconds']:
        errors.append('Chuỗi mẫu không phủ toàn cửa sổ')
    if any(not 0 < b - a <= step for a, b in zip(times, times[1:])):
        errors.append(f'Chuỗi mẫu đảo thời gian hoặc mất cửa sổ {step} giây')
    kinds = ('units', 'queues', 'streams')
    inventory = {k: set(samples[0].get(k, {})) for k in kinds}
    for k in cfg.get('inventory', ['units']):
        if not inventory[k]:
            raise ValueError(f'Thiếu inventory {k}')
    for sample in samples:
        if any(set(sample.get(k, {})) != inventory[k] for k in kinds):
            raise ValueError('Inventory thay đổi trong lượt soak')
        if sample['pool_error_coverage'] is not True:
            raise ValueError('Không có bằng chứng bao phủ mã lỗi pool')
        for code, count in sample['pool_errors'].items():
            if integer(count) > 0:
                errors.append(f'Có lỗi pool {code}')
        for value in sample.get('queues', {}).values():
            integer(value)
    for start in range(0, int(run['duration_seconds']), step):
        window = [s for t, s in zip(times, samples) if start <= t <= start + step]
        if not window or any(not any(s['queues'][q] == 0 for s in window) for q in inventory['queues']):
            errors.append(f'Hàng đợi chưa về 0 trong cửa sổ {start}–{start + step}s')
    for name in inventory['streams']:
        lengths = [number(s['streams'][name]['length']) for s in samples]
        if all(b >= a for a, b in zip(lengths, lengths[1:])) and lengths[-1] > lengths[0]:
            errors.append(f'{name}: độ dài stream tăng đơn điệu; cần đối chiếu retention')
        for s in samples:
            number(s['streams'][name]['pending'])
    growth = cfg.get('rss_growth', .05)
    for name in inventory['units']:
        rss = [number(s['units'][name]['rss_bytes'], 1) for s in samples]
        n = max(1, len(rss) // 5)
        if statistics.mean(rss[-n:]) > statistics.mean(rss[:n]) * (1 + growth):
            errors.append(f'{name}: RSS trung bình cuối tăng >{growth:.0%}')
        pids = [integer(s['units'][name]['pid'], 1) for s in samples]
        restarts = [integer(s['units'][name]['restarts']) for s in samples]
        if len(set(pids)) != 1:
            errors.append(f'{name}: PID thay đổi, không kết luận RSS phẳng')
        if any(value != restarts[0] for value in restarts[1:]):
            errors.append(f'{name}: có restart')
    return errors


def soak(bundle, wl):
    run = bundle['soak']
    return load_contract(run, wl['soak'], wl) + latency(run['groups'], wl) + soak_series(run, wl)


def stage_errors(stage, wl, full=False):
    errors = latency(stage['groups'], wl, full=full)
    if type(stage['success']) is not bool:
        raise ValueError('Thiếu kết quả k6')
    if not stage['success'] or number(stage['dropped_iterations']) != 0:
        errors.append('Nấc k6 lỗi hoặc rơi lượt')
    if number(stage['semantic_error_rate']) != 0 or number(stage['server_error_rate']) >= wl.get('max_server_error_rate', .001):
        errors.append('Nấc có lỗi nghiệp vụ/5xx')
    total = integer(stage['requests'], 1)
    rate = number(stage['achieved_rps'], .000001)
    if sum(integer(g['requests'], 1) for g in stage['groups'].values()) != total:
        raise ValueError('Tổng request nấc không khớp nhóm')
    if abs(total / number(stage['duration_seconds'], .000001) - rate) > .01 * rate:
        raise ValueError('RPS nấc không khớp request/thời gian')
    if rate < .99 * number(stage['requested_rps'], .000001):
        errors.append('Nấc không đạt nhịp yêu cầu')
    return errors


def stress_result(stress, wl):
    cfg = wl['stress']
    if stress['scope'] != 'full' or stress['surface'] != cfg['surface']:
        raise ValueError(f'Headroom toàn hệ cần phép đo full qua {cfg["surface"]}')
    previous, saturation, last_pass = 0, None, None
    for stage in stress['stages']:
        if stage['scope'] != 'full':
            raise ValueError('Nấc probe không chứng minh stress full')
        requested = number(stage['requested_rps'], .000001)
        if requested <= previous:
            raise ValueError('Nấc stress yêu cầu không tăng')
        previous = requested
        rate = number(stage['achieved_rps'], .000001)
        if type(stage['slo_met']) is not bool:
            raise ValueError('Thiếu kết quả SLO của nấc tải')
        errors = stage_errors(stage, wl, full=True)
        if stage['slo_met'] != (not errors):
            raise ValueError('Cờ SLO không khớp số đo độc lập')
        if errors:
            saturation = last_pass
            break
        if last_pass is not None and rate <= last_pass:
            raise ValueError('Thông lượng nấc đạt không tăng')
        last_pass = rate
    if saturation is None:
        raise ValueError('Chưa đo điểm gãy sau một nấc đạt SLO')
    if not isinstance(stress.get('bottleneck'), str) or not stress['bottleneck'].strip():
        raise ValueError('Thiếu kết luận nút thắt')
    reference = cfg.get('reference_rps', wl['peak']['rps'])
    return dict(saturation_rps=saturation, headroom=saturation / reference)


def headroom(bundle, wl):
    need = wl['stress']['headroom']
    if stress_result(bundle['stress'], wl)['headroom'] >= need:
        return []
    return [f'Điểm bão hoà thấp hơn {need}× reference_rps']


def recovery_time(drill, wl):
    cfg = wl['recovery']
    fault = number(drill['fault_at'])
    if drill['restored'] is not True:
        return None
    count, previous = 0, -1
    for sample in drill['samples']:
        t = number(sample['elapsed_seconds'])
        if t <= previous:
            raise ValueError('Mẫu recovery không tăng thời gian')
        previous = t
        ok = (integer(sample['requests'], 1) > 0 and number(sample['p95_ms']) <= cfg['p95_ms']
              and number(sample['server_error_rate']) < wl.get('max_server_error_rate', .001)
              and number(sample['semantic_error_rate']) == 0 and t >= fault)
        count = count + 1 if ok else 0
        if count >= cfg.get('consecutive', 3):
            return t - fault
    return None


def recovery(bundle, wl):
    cfg, spike_cfg = wl['recovery'], wl['spike']
    section = bundle['recovery']
    spike = section['spike']
    errors = []
    if (number(spike['start_rps']) != 0 or number(spike['peak_rps']) < spike_cfg['peak_rps']
            or number(spike['ramp_seconds']) > spike_cfg['ramp_seconds']):
        errors.append(f'Spike chưa đạt 0→{spike_cfg["peak_rps"]} RPS trong {spike_cfg["ramp_seconds"]} giây')
    drills = {d['fault']: d for d in section['drills']}
    for fault in cfg['faults']:
        duration = recovery_time(drills[fault], wl)
        if duration is None or duration > cfg['max_seconds']:
            errors.append(f'{fault}: chưa xác nhận trở lại SLO trong {cfg["max_seconds"]} giây')
    return errors


def plans(bundle, wl):
    limit = wl.get('plans', {}).get('seq_scan_rows', 100000)
    section = bundle['plans']
    errors = []
    if not section['queries']:
        raise ValueError('Thiếu execution plan đường nóng')

    def visit(node, rows, name):
        if not isinstance(node, dict) or not isinstance(node.get('Node Type'), str) or not node['Node Type'].strip():
            raise ValueError('Node EXPLAIN thiếu loại thực thi')
        if not isinstance(node.get('Plans', []), list):
            raise ValueError('Nhánh EXPLAIN không phải danh sách')
        if node['Node Type'] == 'Seq Scan':
            table = node.get('Schema', 'public') + '.' + node['Relation Name']
            if number(rows[table]) >= limit:
                errors.append(f'{name}: Seq Scan trên {table} ≥{limit} dòng')
        for child in node.get('Plans', []):
            visit(child, rows, name)

    for query in section['queries']:
        if not isinstance(query['name'], str) or not query['name'].strip():
            raise ValueError('Thiếu tên query')
        if not isinstance(query['plan'], list) or len(query['plan']) != 1 or not isinstance(query['relation_rows'], dict):
            raise ValueError('EXPLAIN cần một plan JSON và inventory dòng')
        for value in query['relation_rows'].values():
            integer(value)
        visit(query['plan'][0]['Plan'], query['relation_rows'], query['name'])
    return errors


def regression_errors(bundle, wl):
    section = bundle['regression']
    result = regression(section['baseline'], section['current'], wl.get('max_server_error_rate', .001))
    if any(run['source_hash'] != bundle['source_hash'] or run['environment'] != bundle['environment']
           for run in section['current']):
        raise ValueError('Bộ hiện tại không thuộc source/môi trường của gói')
    return result['errors']


def assess(bundle, wl):
    checks = {
        'latency': lambda: latency(bundle['load']['groups'], wl),
        'throughput': lambda: throughput(bundle, wl),
        'soak': lambda: soak(bundle, wl),
        'headroom': lambda: headroom(bundle, wl),
        'recovery': lambda: recovery(bundle, wl),
        'plans': lambda: plans(bundle, wl),
        'regression': lambda: regression_errors(bundle, wl),
    }
    results = {}
    for key, check in checks.items():
        try:
            reasons = check()
            results[key] = dict(status='fail' if reasons else 'pass', reasons=reasons)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError, ZeroDivisionError):
            results[key] = dict(status='missing', reasons=['Thiếu bằng chứng hợp lệ để xác minh tiêu chí'])
    return dict(results=results, errors=[f'{k}: {v["status"]}' for k, v in results.items() if v['status'] != 'pass'])
