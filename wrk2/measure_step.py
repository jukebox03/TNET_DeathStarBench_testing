#!/usr/bin/env python3
"""
개선된 K8s 메트릭 수집 스크립트 v2
- 다중 namespace 지원 (default, istio-system, kube-system)
- Control Plane 오버헤드 측정
- 컨테이너별 CPU 분리 (Istio sidecar 대비)
"""

import subprocess
import requests
import csv
import sys
import time
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Tuple, Optional, Any, List

# ============================================================
# 설정
# ============================================================
OUTPUT_FILE = "k8s_full_metrics.csv"
FAILED_LOG = "collection_failures.log"
K8S_API_URL = "http://127.0.0.1:8001"
MEASURE_DURATION = 60
NETWORK_TIMEOUT = 5
MAX_RETRIES = 2
PARALLEL_WORKERS = 10

# ============================================================
# 측정할 Namespace 설정
# ============================================================
# 기본: default만 측정
# --all-namespaces: 시스템 namespace까지 측정
TARGET_NAMESPACES = {
    'default': {
        'enabled': True,
        'category': 'application'
    },
    'istio-system': {
        'enabled': False,  # --all-namespaces로 활성화
        'category': 'istio-control-plane'
    },
    'kube-system': {
        'enabled': False,  # --all-namespaces로 활성화
        'category': 'kubernetes-system'
    }
}

# ============================================================
# 유틸리티 함수
# ============================================================
def log_failure(pod_name: str, reason: str):
    """수집 실패 로깅"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(FAILED_LOG, 'a') as f:
        f.write(f"[{timestamp}] {pod_name}: {reason}\n")

def get_node_names() -> List[str]:
    """모든 노드 이름 조회"""
    try:
        response = requests.get(f"{K8S_API_URL}/api/v1/nodes", timeout=10)
        response.raise_for_status()
        nodes = response.json().get('items', [])
        return [node['metadata']['name'] for node in nodes]
    except Exception as e:
        print(f"Error getting node names: {e}")
    return []

# ============================================================
# 네트워크 메트릭 수집
# ============================================================
def get_pod_network_direct(pod_name: str, namespace: str = 'default') -> Tuple[int, int]:
    """Pod 내 /proc/net/dev에서 네트워크 통계 수집"""
    for attempt in range(MAX_RETRIES):
        try:
            cmd = [
                "kubectl", "exec", "-n", namespace, pod_name,
                "--", "cat", "/proc/net/dev"
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=NETWORK_TIMEOUT
            )
            
            if result.returncode != 0:
                continue
            
            for line in result.stdout.split('\n'):
                if 'eth0' in line or 'net1' in line:
                    parts = line.split(':')[1].split()
                    rx_bytes = int(parts[0])
                    tx_bytes = int(parts[8])
                    return rx_bytes, tx_bytes
                    
        except subprocess.TimeoutExpired:
            log_failure(f"{namespace}/{pod_name}", f"Network timeout (attempt {attempt+1})")
        except Exception as e:
            log_failure(f"{namespace}/{pod_name}", f"Network error: {e}")
    
    return 0, 0

def collect_network_parallel(pods_info: List[Dict]) -> Dict[str, Tuple[int, int]]:
    """병렬로 모든 Pod의 네트워크 메트릭 수집"""
    results = {}
    
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        future_to_pod = {
            executor.submit(
                get_pod_network_direct, 
                pod['name'], 
                pod['namespace']
            ): pod
            for pod in pods_info
        }
        
        for future in as_completed(future_to_pod):
            pod = future_to_pod[future]
            key = f"{pod['namespace']}/{pod['name']}"
            try:
                rx, tx = future.result()
                results[key] = (rx, tx)
            except Exception as e:
                log_failure(key, f"Parallel collection failed: {e}")
                results[key] = (0, 0)
    
    return results

# ============================================================
# K8s 메트릭 수집 (다중 Namespace)
# ============================================================
def collect_cumulative_metrics(
    node_names: List[str], 
    namespaces: Dict[str, Dict]
) -> Dict[str, Dict[str, Any]]:
    """
    여러 namespace에서 메트릭 수집
    """
    metrics = {}
    pods_for_network = []
    
    # 활성화된 namespace만 필터
    active_namespaces = {ns: info for ns, info in namespaces.items() if info['enabled']}
    
    for node_name in node_names:
        try:
            url = f"{K8S_API_URL}/api/v1/nodes/{node_name}/proxy/stats/summary"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            for pod in data.get('pods', []):
                ns = pod['podRef']['namespace']
                
                # 활성화된 namespace만 처리
                if ns not in active_namespaces:
                    continue
                
                name = pod['podRef']['name']
                category = active_namespaces[ns]['category']
                
                # 서비스 이름 추출
                parts = name.split('-')
                if len(parts) >= 3:
                    service = "-".join(parts[:-2])
                else:
                    service = name
                
                # 컨테이너별 CPU 분리
                total_cpu_cum = 0
                app_cpu_cum = 0
                sidecar_cpu_cum = 0
                container_details = {}
                
                for container in pod.get('containers', []):
                    container_name = container.get('name', 'unknown')
                    cpu_ns = container.get('cpu', {}).get('usageCoreNanoSeconds', 0)
                    container_details[container_name] = cpu_ns
                    total_cpu_cum += cpu_ns
                    
                    if container_name == 'istio-proxy':
                        sidecar_cpu_cum = cpu_ns
                    else:
                        app_cpu_cum += cpu_ns
                
                # Memory
                mem_working_set = pod.get('memory', {}).get('workingSetBytes', 0)
                mem_rss = pod.get('memory', {}).get('rssBytes', 0)
                
                # 네트워크 수집을 위한 정보 저장
                pods_for_network.append({
                    'name': name,
                    'namespace': ns
                })
                
                key = f"{ns}/{name}"
                metrics[key] = {
                    'namespace': ns,
                    'category': category,
                    'service': service,
                    'node': node_name,
                    'cpu_total_cum': total_cpu_cum,
                    'cpu_app_cum': app_cpu_cum,
                    'cpu_sidecar_cum': sidecar_cpu_cum,
                    'container_details': container_details,
                    'mem_working_set': int(mem_working_set / (1024 * 1024)),
                    'mem_rss': int(mem_rss / (1024 * 1024)),
                    'rx_cum': 0,
                    'tx_cum': 0
                }
                
        except Exception as e:
            print(f"Error collecting from node {node_name}: {e}")
    
    # 네트워크 메트릭 병렬 수집
    if pods_for_network:
        network_data = collect_network_parallel(pods_for_network)
        for key, (rx, tx) in network_data.items():
            if key in metrics:
                metrics[key]['rx_cum'] = rx
                metrics[key]['tx_cum'] = tx
    
    return metrics

# ============================================================
# Delta 계산
# ============================================================
def calculate_deltas(
    start_metrics: Dict[str, Dict],
    end_metrics: Dict[str, Dict],
    duration: float
) -> list:
    """시작/종료 메트릭의 delta 계산"""
    rows = []
    
    for key, start_data in start_metrics.items():
        if key not in end_metrics:
            log_failure(key, "Pod disappeared during measurement")
            continue
        
        end_data = end_metrics[key]
        
        # CPU 계산
        cpu_total_delta = end_data['cpu_total_cum'] - start_data['cpu_total_cum']
        cpu_total_m = int((cpu_total_delta / duration) / 1_000_000)
        
        cpu_app_delta = end_data['cpu_app_cum'] - start_data['cpu_app_cum']
        cpu_app_m = int((cpu_app_delta / duration) / 1_000_000)
        
        cpu_sidecar_delta = end_data['cpu_sidecar_cum'] - start_data['cpu_sidecar_cum']
        cpu_sidecar_m = int((cpu_sidecar_delta / duration) / 1_000_000)
        
        # Memory delta
        mem_delta = end_data['mem_working_set'] - start_data['mem_working_set']
        
        # Network
        rx_delta = end_data['rx_cum'] - start_data['rx_cum']
        tx_delta = end_data['tx_cum'] - start_data['tx_cum']
        rx_kbps = round(rx_delta / duration / 1024, 2)
        tx_kbps = round(tx_delta / duration / 1024, 2)
        
        # 음수 체크
        if cpu_total_delta < 0 or rx_delta < 0 or tx_delta < 0:
            log_failure(key, "Negative delta detected (counter reset?)")
            continue
        
        rows.append({
            'namespace': start_data['namespace'],
            'category': start_data['category'],
            'service': start_data['service'],
            'pod': key.split('/')[-1],
            'node': start_data['node'],
            'cpu_total_m': cpu_total_m,
            'cpu_app_m': cpu_app_m,
            'cpu_sidecar_m': cpu_sidecar_m,
            'mem_working_set': end_data['mem_working_set'],
            'mem_rss': end_data['mem_rss'],
            'mem_delta': mem_delta,
            'rx_kbps': rx_kbps,
            'tx_kbps': tx_kbps
        })
    
    return rows

# ============================================================
# 결과 저장
# ============================================================
def save_results(rows: list, rps: str, timestamp: str, istio_enabled: bool = False):
    """결과 CSV 저장"""
    file_exists = os.path.isfile(OUTPUT_FILE)
    
    with open(OUTPUT_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        
        if not file_exists:
            writer.writerow([
                "Timestamp", "RPS", "Namespace", "Category", "Service", "Pod", "Node",
                "CPU_Total(m)", "CPU_App(m)", "CPU_Sidecar(m)",
                "Memory_WorkingSet(Mi)", "Memory_RSS(Mi)", "Memory_Delta(Mi)",
                "Net_RX(KB/s)", "Net_TX(KB/s)",
                "Istio_Enabled"
            ])
        
        for row in rows:
            writer.writerow([
                timestamp, rps, 
                row['namespace'], row['category'], row['service'], row['pod'], row['node'],
                row['cpu_total_m'], row['cpu_app_m'], row['cpu_sidecar_m'],
                row['mem_working_set'], row['mem_rss'], row['mem_delta'],
                row['rx_kbps'], row['tx_kbps'],
                istio_enabled
            ])

def print_summary(rows: list, namespaces: Dict):
    """카테고리별 요약 출력"""
    # 카테고리별 집계
    by_category = {}
    for row in rows:
        cat = row['category']
        if cat not in by_category:
            by_category[cat] = {'cpu': 0, 'mem': 0, 'net_rx': 0, 'net_tx': 0, 'count': 0}
        by_category[cat]['cpu'] += row['cpu_total_m']
        by_category[cat]['mem'] += row['mem_working_set']
        by_category[cat]['net_rx'] += row['rx_kbps']
        by_category[cat]['net_tx'] += row['tx_kbps']
        by_category[cat]['count'] += 1
    
    print(f"\n   [Summary by Category]")
    print(f"   {'Category':<25} {'Pods':>5} {'CPU(m)':>10} {'Mem(Mi)':>10} {'RX(KB/s)':>12} {'TX(KB/s)':>12}")
    print(f"   {'-'*75}")
    
    total_cpu = 0
    total_mem = 0
    for cat, stats in sorted(by_category.items()):
        print(f"   {cat:<25} {stats['count']:>5} {stats['cpu']:>10} {stats['mem']:>10} {stats['net_rx']:>12.1f} {stats['net_tx']:>12.1f}")
        total_cpu += stats['cpu']
        total_mem += stats['mem']
    
    print(f"   {'-'*75}")
    print(f"   {'TOTAL':<25} {len(rows):>5} {total_cpu:>10} {total_mem:>10}")

# ============================================================
# 메인
# ============================================================
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 measure_step.py <RPS> [--istio] [--all-namespaces] [--duration=60]")
        print("")
        print("Options:")
        print("  --istio            Mark as Istio-enabled experiment")
        print("  --all-namespaces   Include istio-system and kube-system")
        print("  --duration=N       Measurement duration in seconds (default: 60)")
        sys.exit(1)
    
    current_rps = sys.argv[1]
    istio_enabled = "--istio" in sys.argv
    all_namespaces = "--all-namespaces" in sys.argv
    
    # duration 파싱
    duration = MEASURE_DURATION
    for arg in sys.argv:
        if arg.startswith("--duration="):
            duration = int(arg.split("=")[1])
    
    # Namespace 설정
    namespaces = TARGET_NAMESPACES.copy()
    if all_namespaces:
        namespaces['istio-system']['enabled'] = True
        namespaces['kube-system']['enabled'] = True
    
    active_ns = [ns for ns, info in namespaces.items() if info['enabled']]
    
    # 노드 확인
    node_names = get_node_names()
    if not node_names:
        print("Error: kubectl proxy is not running or nodes not found.")
        print("Run: kubectl proxy &")
        sys.exit(1)
    
    mode = "with Istio" if istio_enabled else "without Istio"
    print(f"   [Measure] Nodes: {', '.join(node_names)}")
    print(f"   [Measure] Namespaces: {', '.join(active_ns)}")
    print(f"   [Measure] Starting {duration}s sampling for {current_rps} RPS ({mode})...")
    
    # 측정 시작 (T1)
    start_time = datetime.now()
    metrics_start = collect_cumulative_metrics(node_names, namespaces)
    
    if not metrics_start:
        print("Error: Failed to collect initial metrics")
        sys.exit(1)
    
    print(f"   [Measure] Collected {len(metrics_start)} pods at T1")
    
    # 대기
    time.sleep(duration)
    
    # 측정 종료 (T2)
    metrics_end = collect_cumulative_metrics(node_names, namespaces)
    end_time = datetime.now()
    
    actual_duration = (end_time - start_time).total_seconds()
    print(f"   [Measure] Collected {len(metrics_end)} pods at T2 (actual: {actual_duration:.1f}s)")
    
    # Delta 계산
    rows = calculate_deltas(metrics_start, metrics_end, actual_duration)
    
    # 저장
    timestamp_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
    save_results(rows, current_rps, timestamp_str, istio_enabled)
    
    # 요약 출력
    print_summary(rows, namespaces)
    
    print(f"\n   [Measure] ✓ Completed for {current_rps} RPS. Recorded {len(rows)} pods.")

if __name__ == "__main__":
    main()