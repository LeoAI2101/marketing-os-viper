"""Dữ liệu mẫu dùng chung cho test công cụ perf — không phát tải, không đọc file dự án."""
import copy

WORKLOAD = {
    "confirmed": True,
    "groups": {"R1": {"weight": 60, "p95_ms": 300}, "R2": {"weight": 30, "p95_ms": 300},
               "W1": {"weight": 10, "p95_ms": 500}},
    "p99_factor": 2.5, "max_server_error_rate": .001, "mix_tolerance_pct": 1,
    "load": {"rps": 900, "duration_seconds": 1800, "warmup_seconds": 300, "active_users": 1000},
    "peak": {"rps": 1200, "duration_seconds": 900, "warmup_seconds": 300, "active_users": 1000},
    "soak": {"rps": 900, "duration_seconds": 28800, "warmup_seconds": 300, "active_users": 1000,
             "sample_interval_seconds": 300, "rss_growth": .05, "inventory": ["units", "queues", "streams"]},
    "stress": {"headroom": 3, "reference_rps": 1200, "surface": "gateway"},
    "spike": {"peak_rps": 1200, "ramp_seconds": 30},
    "recovery": {"faults": ["pod", "cache", "boundary"], "max_seconds": 60, "p95_ms": 300, "consecutive": 3},
    "plans": {"seq_scan_rows": 100000},
}


def workload():
    return copy.deepcopy(WORKLOAD)


def run(duration=1800, rate=900):
    requests = duration * rate
    weights = {k: g["weight"] for k, g in WORKLOAD["groups"].items()}
    total_weight = sum(weights.values())
    return {"scope": "full", "duration_seconds": duration, "warmup_seconds": 300,
            "requests": requests, "achieved_rps": rate, "active_users": 1000,
            "dropped_iterations": 0, "groups": {
                name: {"requests": round(requests * w / total_weight), "p95_ms": 100,
                       "p99_ms": 200, "semantic_error_rate": 0, "server_error_rate": 0}
                for name, w in weights.items()}}


def stress_stage(rate, passed=True):
    stage = dict(run(60, rate), requested_rps=rate, slo_met=passed, success=True,
                 semantic_error_rate=0, server_error_rate=0)
    if not passed:
        stage["groups"]["R1"].update(p95_ms=350, p99_ms=600)
    return stage


def regression_run(latency, source="current-source"):
    return {"workload_hash": "w", "dataset_hash": "d", "topology_hash": "t",
            "runner_host": "loadgen", "target_host": "pt-host", "kind": "load", "scope": "full",
            "rate": 900, "duration_seconds": 1800, "warmup_seconds": 300, "case_hash": "c",
            "source_hash": source, "p95_ms": latency, "success": True, "requests": 1620000,
            "environment": "PT", "k6_version": "v1.0.0",
            "dropped_iterations": 0, "semantic_error_rate": 0}


def runs(latencies=(99, 100, 101), source="baseline-source"):
    return [regression_run(p, source) for p in latencies]


def evidence():
    soak = run(28800)
    soak["samples"] = [{
        "elapsed_seconds": index * 300,
        "units": {"svc-orders": {"rss_bytes": 100000000, "restarts": 0, "pid": 1234}},
        "queues": {"orders.outbox": 0},
        "streams": {"orders.events": {"length": 100 + index % 2, "pending": 0}},
        "pool_errors": {"pool_timeout": 0}, "pool_error_coverage": True,
    } for index in range(97)]
    drills = [{"fault": fault, "fault_at": 0, "restored": True, "samples": [
        {"elapsed_seconds": elapsed, "p95_ms": 100, "server_error_rate": 0,
         "semantic_error_rate": 0, "requests": 1000}
        for elapsed in [20, 40, 60]]} for fault in ["pod", "cache", "boundary"]]
    return {"schema_version": 1, "environment": "PT", "source_hash": "current-source",
            "load": run(), "peak": run(900, 1200), "soak": soak,
            "stress": {"scope": "full", "surface": "gateway",
                       "stages": [stress_stage(1200), stress_stage(3600), stress_stage(3800, False)],
                       "bottleneck": "CPU boundary chạm trần"},
            "recovery": {"spike": {"start_rps": 0, "peak_rps": 1200, "ramp_seconds": 30},
                         "drills": drills},
            "plans": {"queries": [{"name": "order-list", "plan": [{"Plan": {
                "Node Type": "Index Scan", "Schema": "orders", "Relation Name": "order"}}],
                "relation_rows": {"orders.order": 100000}}]},
            "regression": {"baseline": runs(source="old-source"),
                           "current": runs((100, 101, 102), source="current-source")}}
