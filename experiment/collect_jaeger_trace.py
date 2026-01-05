#!/usr/bin/env python3
"""
collect_jaeger_traces.py (Fixed Logic)
"""

import requests
import json
import sys
import os
import csv
import time
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686")
OUTPUT_FILE = "jaeger_traces.csv"
DEPENDENCY_FILE = "service_dependencies.csv"
LATENCY_FILE = "latency_breakdown.csv"

class JaegerClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self._check_connection()
    
    def _check_connection(self):
        try:
            resp = requests.get(f"{self.base_url}/api/services", timeout=5)
            if resp.status_code != 200:
                raise ConnectionError(f"Jaeger returned {resp.status_code}")
            print(f"[INFO] Connected to Jaeger at {self.base_url}")
        except Exception as e:
            print(f"[ERROR] Cannot connect to Jaeger: {e}")
            sys.exit(1)
    
    def get_services(self) -> List[str]:
        resp = requests.get(f"{self.base_url}/api/services", timeout=10)
        return [s for s in resp.json().get('data', []) if not s.startswith('jaeger')]
    
    def get_traces(self, service: str, limit: int = 100, lookback_hours: int = 1) -> List[Dict]:
        end_ts = int(time.time() * 1000000)
        start_ts = end_ts - (lookback_hours * 3600 * 1000000)
        params = {'service': service, 'limit': limit, 'start': start_ts, 'end': end_ts}
        resp = requests.get(f"{self.base_url}/api/traces", params=params, timeout=30)
        return resp.json().get('data', [])

# ============================================================
# 분석 함수 (핵심 수정됨: Root Operation 추출)
# ============================================================
def analyze_trace(trace: Dict) -> Dict[str, Any]:
    spans = trace.get('spans', [])
    processes = trace.get('processes', {})
    
    pid_to_service = {}
    for pid, pinfo in processes.items():
        pid_to_service[pid] = pinfo.get('serviceName', 'unknown')
    
    # Root Span 찾기 (부모가 없는 Span)
    root_span = None
    span_map = {s['spanID']: s for s in spans}
    
    for span in spans:
        refs = span.get('references', [])
        has_parent = any(r.get('refType') == 'CHILD_OF' for r in refs)
        if not has_parent:
            root_span = span
            break
            
    # [수정] Root Operation 이름 추출 (예: /hotels, /recommendations)
    root_operation = root_span.get('operationName', 'unknown') if root_span else 'unknown'
    
    analyzed_spans = []
    services_seen = set()
    
    for span in spans:
        service = pid_to_service.get(span.get('processID'), 'unknown')
        operation = span.get('operationName', 'unknown')
        duration = span.get('duration', 0)
        
        is_sidecar = any(k in service.lower() or k in operation.lower() 
                        for k in ['istio', 'envoy', 'inbound', 'outbound'])
        
        services_seen.add(service)
        analyzed_spans.append({
            'service': service,
            'operation': operation,
            'duration_us': duration,
            'is_sidecar': is_sidecar,
            'span_id': span.get('spanID'),
            'references': span.get('references', [])
        })
    
    return {
        'trace_id': trace.get('traceID', 'unknown'),
        'root_operation': root_operation,  # 이것을 기준으로 분류합니다
        'total_duration_us': root_span.get('duration', 0) if root_span else 0,
        'span_count': len(spans),
        'services': list(services_seen),
        'spans': analyzed_spans,
        'processes': pid_to_service
    }

def calculate_service_latency(traces: List[Dict]) -> Dict[str, Dict]:
    service_durations = defaultdict(list)
    for trace in traces:
        analyzed = analyze_trace(trace)
        for span in analyzed['spans']:
            service_durations[span['service']].append(span['duration_us'])
    
    results = {}
    for service, durations in service_durations.items():
        if not durations: continue
        sorted_d = sorted(durations)
        n = len(sorted_d)
        results[service] = {
            'count': n,
            'avg_us': sum(durations) / n,
            'p50_us': sorted_d[int(n * 0.5)],
            'p95_us': sorted_d[int(n * 0.95)] if n > 20 else sorted_d[-1],
        }
    return results

def calculate_edge_latency(traces: List[Dict]) -> Dict[Tuple[str, str], Dict]:
    edge_durations = defaultdict(list)
    for trace in traces:
        analyzed = analyze_trace(trace)
        spans = analyzed['spans']
        span_map = {s['span_id']: s for s in spans}
        
        for span in spans:
            child_service = span['service']
            for ref in span['references']:
                if ref.get('refType') == 'CHILD_OF':
                    parent_span = span_map.get(ref.get('spanID'))
                    if parent_span:
                        parent_service = parent_span['service']
                        if parent_service != child_service:
                            edge = (parent_service, child_service)
                            edge_durations[edge].append(span['duration_us'])

    results = {}
    for edge, durations in edge_durations.items():
        sorted_d = sorted(durations)
        n = len(sorted_d)
        results[edge] = {
            'call_count': n,
            'avg_us': sum(durations) / n,
            'p95_us': sorted_d[int(n * 0.95)] if n > 20 else sorted_d[-1]
        }
    return results

def detect_istio_overhead(traces: List[Dict]) -> Dict[str, float]:
    ratios = []
    times = []
    for trace in traces:
        analyzed = analyze_trace(trace)
        sidecar = sum(s['duration_us'] for s in analyzed['spans'] if s['is_sidecar'])
        if analyzed['total_duration_us'] > 0:
            ratios.append(sidecar / analyzed['total_duration_us'])
            times.append(sidecar)
    if not ratios: return {'avg_overhead_ratio': 0, 'avg_sidecar_us': 0}
    return {
        'avg_overhead_ratio': sum(ratios) / len(ratios),
        'avg_sidecar_us': sum(times) / len(times)
    }

def print_dependencies(deps: List[Dict]):
    print("\n" + "="*60)
    print("Service Dependencies (Calculated from Traces)")
    print("="*60)
    print(f"{'Parent':<20} {'Child':<20} {'Calls':>10}")
    print("-"*60)
    for dep in sorted(deps, key=lambda x: x['callCount'], reverse=True):
        print(f"{dep['parent']:<20} {dep['child']:<20} {dep['callCount']:>10}")

def print_service_latency(latencies: Dict[str, Dict]):
    print("\n" + "="*80)
    print("Service Latency Statistics")
    print("="*80)
    print(f"{'Service':<25} {'Count':>8} {'Avg(ms)':>10} {'P50(ms)':>10} {'P95(ms)':>10}")
    print("-"*80)
    for s, st in sorted(latencies.items()):
        print(f"{s:<25} {st['count']:>8} {st['avg_us']/1000:>10.2f} "
              f"{st['p50_us']/1000:>10.2f} {st['p95_us']/1000:>10.2f}")

def save_to_csv(deps, service_latency, edge_latency):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(DEPENDENCY_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Timestamp', 'Parent', 'Child', 'CallCount'])
        for dep in deps:
            writer.writerow([ts, dep['parent'], dep['child'], dep['callCount']])
    with open(LATENCY_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Timestamp', 'Service', 'Count', 'Avg_us', 'P95_us'])
        for s, st in service_latency.items():
            writer.writerow([ts, s, st['count'], round(st['avg_us'],2), round(st['p95_us'],2)])

# ============================================================
# 메인 함수 (분류 로직 수정)
# ============================================================
def main():
    limit = 100
    lookback = 1
    for arg in sys.argv[1:]:
        if arg.startswith('--limit='): limit = int(arg.split('=')[1])
        elif arg.startswith('--lookback='): lookback = int(arg.split('=')[1])
    
    client = JaegerClient(JAEGER_URL)
    services = client.get_services()
    print(f"\n[INFO] Fetching traces from 'frontend' (limit={limit})...")
    
    traces = []
    if 'frontend' in services:
        traces = client.get_traces('frontend', limit=limit, lookback_hours=lookback)
    else:
        print("[ERROR] 'frontend' service not found. Cannot analyze workload.")
        return

    if not traces:
        print("[WARN] No traces found!")
        return

    print(f"[INFO] Successfully collected {len(traces)} traces.")

    # [수정] Workload Classification (Based on Root Operation)
    req_counts = {'search': 0, 'recommendation': 0, 'reservation': 0, 'user': 0, 'unknown': 0}
    
    for trace in traces:
        analyzed = analyze_trace(trace)
        root_op = analyzed['root_operation'].lower() # 예: /hotels, /recommendations
        
        # URL Endpoint 기반으로 분류 (가장 정확함)
        if 'hotels' in root_op:
            req_counts['search'] += 1
        elif 'recommendation' in root_op:
            req_counts['recommendation'] += 1
        elif 'reservation' in root_op:
            req_counts['reservation'] += 1
        elif 'user' in root_op:
            req_counts['user'] += 1
        else:
            # Fallback for weird traces
            req_counts['unknown'] += 1

    total = len(traces)
    print("\n" + "="*60)
    print("Workload Distribution Analysis (Based on Root Operation)")
    print("="*60)
    print(f"{'Request Type':<15} {'Count':>8} {'Measured(%)':>12} {'Target(%)':>12}")
    print("-" * 60)
    print(f"{'Search':<15} {req_counts['search']:>8} {req_counts['search']/total*100:>11.1f}% {'~60.0%':>12}")
    print(f"{'Recommendation':<15} {req_counts['recommendation']:>8} {req_counts['recommendation']/total*100:>11.1f}% {'~39.0%':>12}")
    print(f"{'User/Login':<15} {req_counts['user']:>8} {req_counts['user']/total*100:>11.1f}% {'~0.5%':>12}")
    print(f"{'Reservation':<15} {req_counts['reservation']:>8} {req_counts['reservation']/total*100:>11.1f}% {'~0.5%':>12}")
    print(f"{'Unknown':<15} {req_counts['unknown']:>8} {req_counts['unknown']/total*100:>11.1f}% {'-':>12}")

    print("\n[INFO] Analyzing traces for Latency and DAG...")
    service_latency = calculate_service_latency(traces)
    edge_latency = calculate_edge_latency(traces)
    
    calculated_deps = []
    for (parent, child), stats in edge_latency.items():
        calculated_deps.append({'parent': parent, 'child': child, 'callCount': stats['call_count']})
    
    print_dependencies(calculated_deps)
    print_service_latency(service_latency)
    
    overhead = detect_istio_overhead(traces)
    if overhead['avg_overhead_ratio'] > 0:
        print(f"\n[INFO] Istio Overhead: {overhead['avg_overhead_ratio']*100:.1f}% ({overhead['avg_sidecar_us']/1000:.2f}ms)")

    save_to_csv(calculated_deps, service_latency, edge_latency)

if __name__ == "__main__":
    main()