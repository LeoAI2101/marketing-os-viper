"""Hợp đồng báo cáo perf (skill perf-testing): so hồi quy ba lượt, canh bằng chứng trước deploy.

Không phát tải. Probe không chứng minh nghiệm thu. Ngưỡng đọc từ khối ```json của
context/perf/WORKLOAD.md — Authority chốt (`"confirmed": true`), không gõ cứng ở đây.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess

WORKLOAD = Path('context/perf/WORKLOAD.md')
MATCH = ('workload_hash', 'dataset_hash', 'topology_hash', 'runner_host', 'target_host',
         'kind', 'scope', 'rate', 'duration_seconds', 'warmup_seconds', 'case_hash',
         'environment', 'k6_version')
OPTIONAL_MATCH = ('target_address', 'target_port', 'surface', 'transport', 'same_host')
# Tài liệu/sổ sách không đổi hành vi sản phẩm — sửa chúng không làm báo cáo cũ đi.
SOURCE_EXCLUDE = ('context', 'demos', 'intake', '.claude', '.local')
T_CRITICAL_3 = 4.303  # t(0.975, df=2): khoảng tin cậy 95 % của trung bình ba lượt


def load_workload(root):
    """Khối ```json đầu tiên của WORKLOAD.md — một nguồn cho người đọc lẫn máy đọc."""
    text = (Path(root) / WORKLOAD).read_text()
    m = re.search(r'^```json\s*\n(.*?)^```', text, re.MULTILINE | re.DOTALL)
    if not m:
        raise ValueError(f'{WORKLOAD} thiếu khối ```json chứa ngưỡng')
    workload = json.loads(m.group(1))
    if not isinstance(workload, dict) or not isinstance(workload.get('groups'), dict) or not workload['groups']:
        raise ValueError(f'{WORKLOAD}: khối json thiếu `groups`')
    return workload


def workload_hash(workload):
    return hashlib.sha256(json.dumps(workload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_hash(root):
    pathspec = ['.'] + [f':(exclude){p}' for p in SOURCE_EXCLUDE]
    result = subprocess.run(['git', 'ls-files', '-co', '--exclude-standard', '--', *pathspec],
                            cwd=root, capture_output=True, text=True, check=True)
    digest = hashlib.sha256()
    for name in sorted(set(result.stdout.splitlines())):
        p = Path(root) / name
        if p.is_file():
            digest.update(name.encode() + b'\0' + p.read_bytes() + b'\0')
    return digest.hexdigest()


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def regression(baseline, current, max_server_error_rate=.001):
    if not isinstance(baseline, list) or not isinstance(current, list) or len(baseline) != 3 or len(current) != 3:
        raise ValueError('Cần đúng ba lượt nền và ba lượt mới')
    runs = baseline + current
    if any(not isinstance(run, dict) for run in runs):
        raise ValueError('Mỗi lượt phải là một báo cáo')
    matched = MATCH + tuple(key for key in OPTIONAL_MATCH if any(key in run for run in runs))
    for run in runs:
        if any(key not in run or type(run[key]) is not type(baseline[0].get(key)) or
               run[key] != baseline[0].get(key) for key in matched):
            raise ValueError('Hai bộ số khác điều kiện đo')
        for key in matched:
            value = run[key]
            if key == 'same_host':
                if type(value) is not bool:
                    raise ValueError('same_host phải là bool')
            elif key in ('rate', 'duration_seconds', 'warmup_seconds', 'target_port'):
                if type(value) not in (int, float) or not math.isfinite(value) or value < (0 if key == 'warmup_seconds' else 1):
                    raise ValueError('Điều kiện đo thiếu số hợp lệ')
            elif not isinstance(value, str) or not value.strip():
                raise ValueError('Điều kiện đo thiếu định danh')
        if not positive(run.get('p95_ms')) or not positive(run.get('requests')) or run.get('success') is not True:
            raise ValueError('Lượt đo lỗi/thiếu số không dùng làm baseline')
        if run['requests'] != int(run['requests']):
            raise ValueError('Số request phải là số nguyên')
        if (type(run.get('dropped_iterations')) not in (int, float) or run['dropped_iterations'] != 0 or
                type(run.get('semantic_error_rate')) not in (int, float) or run['semantic_error_rate'] != 0):
            raise ValueError('Lượt đo rơi tải hoặc sai nghiệp vụ')
        if 'server_error_rate' in run and (type(run['server_error_rate']) not in (int, float) or
                not math.isfinite(run['server_error_rate']) or
                not 0 <= run['server_error_rate'] < max_server_error_rate):
            raise ValueError('Lượt đo có lỗi server hoặc thiếu tỷ lệ hợp lệ')
    for cohort in (baseline, current):
        source = cohort[0].get('source_hash')
        if not isinstance(source, str) or not source.strip() or any(run.get('source_hash') != source for run in cohort):
            raise ValueError('Ba lượt trong mỗi bộ phải cùng source_hash')
    values = [r['p95_ms'] for r in baseline]
    old, new = statistics.mean(values), statistics.mean(r['p95_ms'] for r in current)
    margin = T_CRITICAL_3 * statistics.stdev(values) / math.sqrt(3)
    ci = [old - margin, old + margin]
    errors = []
    if new > old * 1.10:
        errors.append('p95 tăng hơn 10%')
    if not ci[0] <= new <= ci[1]:
        errors.append('p95 ngoài khoảng tin cậy 95% của trung bình ba lượt nền')
    return dict(errors=errors, baseline_mean=old, current_mean=new, ci95=ci)


def gate(report, expected_hash, env, workload):
    """Bằng chứng trước deploy: gói đầy đủ, đúng môi trường, đúng source, đúng ngưỡng đã chốt."""
    errors = []
    if str(report.get('environment', '')).upper() != env.upper():
        errors.append('Báo cáo không đúng môi trường deploy')
    if report.get('source_hash') != expected_hash:
        errors.append('Báo cáo cũ so với source hiện tại')
    if report.get('scope') != 'full' or report.get('success') is not True:
        errors.append('Cần báo cáo full đạt; probe không thay nghiệm thu')
    if workload.get('confirmed') is not True:
        errors.append('Ngưỡng trong WORKLOAD.md chưa được Authority chốt (`"confirmed": true`)')
    if report.get('workload_hash') != workload_hash(workload):
        errors.append('Báo cáo đo theo WORKLOAD khác bản hiện tại')
    from lib.perf_evidence import assess
    errors.extend(assess(report, workload)['errors'])
    return errors
