#!/usr/bin/env python3
"""
measure_step.py v4 (Network via kubectl exec)
- CPU/Memory: Kubelet Summary API
- Network: kubectl exec /proc/net/dev (병렬 처리)
- Disk I/O: Prometheus API
- LLC/Memory BW: Intel PCM (System-wide)

수정사항:
- Network: Minikube에서 작동하도록 kubectl exec 방식으로 변경
- 병렬 처리로 오버헤드 최소화 (10 workers)
"""

import requests
import subprocess
import csv
import sys
import time
import os
import json
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# 설정 (Configuration)
# ============================================================
OUTPUT_FILE = "k8s_full_metrics.csv"
PCM_CSV_FILE = "pcm_temp.csv"
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
K8S_API_URL = os.environ.get("K8S_API_URL", "http://127.0.0.1:8001")
PCM_PATH = os.environ.get("PCM_PATH", "./pcm.x")

# Network 수집 설정
NETWORK_TIMEOUT = 5
NETWORK_WORKERS = 10
MAX_RETRIES = 2

# ============================================================
# Namespace 설정
# ============================================================
TARGET_NAMESPACES = {
    'default': {'enabled': True, 'category': 'application'},
    'hotel-res': {'enabled': True, 'category': 'application'},
    'istio-system': {'enabled': False, 'category': 'istio-control-plane'},
    'kube-system': {'enabled': False, 'category': 'kubernetes-system'}
}

# ============================================================
# 로깅
# ============================================================
def log_info(msg: str):
    print(f"   [INFO] {msg}")

def log_warn(msg: str):
    print(f"   [WARN] {msg}")

def log_error(msg: str):
    print(f"   [ERROR] {msg}", file=sys.stderr)

# ============================================================
# 유틸리티
# ============================================================
def check_prometheus() -> bool:
    """Prometheus 연결 확인"""
    try:
        resp = requests.get(f"{PROMETHEUS_URL}/-/healthy", timeout=2)
        return resp.status_code == 200
    except:
        return False

def check_kubectl_proxy() -> bool:
    """kubectl proxy 연결 확인"""
    try:
        resp = requests.get(f"{K8S_API_URL}/api/v1/nodes", timeout=2)
        return resp.status_code == 200
    except:
        return False

# ============================================================
# 1. PCM Controller
# ============================================================
class PCMController:
    """Intel PCM 제어"""
    
    def __init__(self, output_file: str, interval: float = 1.0):
        self.output_file = output_file
        self.interval = interval
        self.process = None
        self.available = False
        self._check_availability()
    
    def _check_availability(self):
        """PCM 사용 가능 여부 확인"""
        if not os.path.exists(PCM_PATH):
            return
        
        if not os.access(PCM_PATH, os.X_OK):
            return
        
        result = subprocess.run(["sudo", "-n", "true"], capture_output=True)
        if result.returncode != 0:
            return
        
        self.available = True
        log_info("PCM is available")

    def start(self):
        """PCM 백그라운드 실행"""
        if not self.available:
            return
        
        if os.path.exists(self.output_file):
            os.remove(self.output_file)
        
        cmd = ["sudo", PCM_PATH, str(self.interval), f"-csv={self.output_file}"]
        
        try:
            self.process = subprocess.Popen(
                cmd, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL
            )
            log_info(f"PCM started (PID: {self.process.pid})")
        except Exception as e:
            log_error(f"Failed to start PCM: {e}")
            self.available = False

    def stop(self):
        """PCM 종료"""
        if not self.process:
            return
        
        try:
            subprocess.run(["sudo", "kill", str(self.process.pid)], 
                          capture_output=True, timeout=5)
            self.process.wait(timeout=2)
            log_info("PCM stopped")
        except subprocess.TimeoutExpired:
            subprocess.run(["sudo", "kill", "-9", str(self.process.pid)], 
                          capture_output=True)
        except Exception as e:
            log_error(f"Error stopping PCM: {e}")

    def parse_results(self) -> Dict[str, float]:
        """PCM CSV 파싱 (2-row 헤더 구조 처리)"""
        default_result = {'mem_bw_system': 0.0, 'llc_metric_system': 0.0}
        
        if not self.available or not os.path.exists(self.output_file):
            return default_result
        
        try:
            with open(self.output_file, 'r') as f:
                lines = f.readlines()
            
            if len(lines) < 3:
                return default_result
            
            # Row 0: categories, Row 1: column names
            categories = lines[0].strip().split(',')
            headers = lines[1].strip().split(',')
            
            # System 영역에서 READ, WRITE, L3HIT 인덱스 찾기
            read_idx = None
            write_idx = None
            l3hit_idx = None
            
            for i, (cat, col) in enumerate(zip(categories, headers)):
                if cat == 'System' and col == 'READ':
                    read_idx = i
                elif cat == 'System' and col == 'WRITE':
                    write_idx = i
                elif cat == 'System' and col == 'L3HIT':
                    l3hit_idx = i
            
            # 데이터 파싱
            read_vals, write_vals, l3hit_vals = [], [], []
            
            for line in lines[2:]:
                parts = line.strip().split(',')
                try:
                    if read_idx and read_idx < len(parts):
                        val = float(parts[read_idx])
                        if val > 0:
                            read_vals.append(val)
                    if write_idx and write_idx < len(parts):
                        val = float(parts[write_idx])
                        if val > 0:
                            write_vals.append(val)
                    if l3hit_idx and l3hit_idx < len(parts):
                        val = float(parts[l3hit_idx])
                        if 0 <= val <= 1:
                            l3hit_vals.append(val)
                except (ValueError, IndexError):
                    continue
            
            avg_read = sum(read_vals) / len(read_vals) if read_vals else 0
            avg_write = sum(write_vals) / len(write_vals) if write_vals else 0
            avg_l3hit = sum(l3hit_vals) / len(l3hit_vals) if l3hit_vals else 0
            
            mem_bw_total = avg_read + avg_write
            
            log_info(f"PCM: Mem BW={mem_bw_total:.2f} GB/s, L3HIT={avg_l3hit:.4f}")
            
            return {
                'mem_bw_system': round(mem_bw_total, 2),
                'llc_metric_system': round(avg_l3hit, 4)
            }
            
        except Exception as e:
            log_error(f"Failed to parse PCM: {e}")
            return default_result

# ============================================================
# 2. Network via kubectl exec (병렬 처리)
# ============================================================
def get_pod_network_direct(pod_name: str, namespace: str) -> Tuple[int, int]:
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
                # eth0 또는 net1 인터페이스 찾기
                if 'eth0' in line or 'net1' in line:
                    parts = line.split(':')[1].split()
                    rx_bytes = int(parts[0])   # 수신 바이트
                    tx_bytes = int(parts[8])   # 송신 바이트
                    return rx_bytes, tx_bytes
                    
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass
    
    return 0, 0

def collect_network_parallel(pods_info: List[Dict]) -> Dict[str, Tuple[int, int]]:
    """병렬로 모든 Pod의 네트워크 메트릭 수집"""
    results = {}
    
    with ThreadPoolExecutor(max_workers=NETWORK_WORKERS) as executor:
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
            except Exception:
                results[key] = (0, 0)
    
    return results

# ============================================================
# 3. Disk I/O via Prometheus
# ============================================================
def get_disk_bandwidth(duration: int) -> Dict[str, Dict[str, float]]:
    """Prometheus에서 Disk I/O 가져오기"""
    metrics = {}
    
    if not check_prometheus():
        return metrics
    
    queries = {
        'disk_read_kbps': f'rate(container_fs_reads_bytes_total[{duration}s]) / 1024',
        'disk_write_kbps': f'rate(container_fs_writes_bytes_total[{duration}s]) / 1024'
    }
    
    for metric_name, query in queries.items():
        try:
            resp = requests.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={'query': query},
                timeout=5
            )
            
            if resp.status_code != 200:
                continue
            
            data = resp.json()
            
            for result in data.get('data', {}).get('result', []):
                labels = result.get('metric', {})
                pod = labels.get('pod', labels.get('container', 'unknown'))
                namespace = labels.get('namespace', 'unknown')
                value = float(result['value'][1])
                
                key = f"{namespace}/{pod}"
                if key not in metrics:
                    metrics[key] = {'namespace': namespace, 'pod': pod, 'disk_read_kbps': 0, 'disk_write_kbps': 0}
                
                metrics[key][metric_name] = round(value, 2)
                
        except Exception as e:
            log_error(f"Disk query failed: {e}")
    
    return metrics

# ============================================================
# 4. Kubelet API (CPU/Memory)
# ============================================================
def get_node_summary(node: str) -> Optional[Dict]:
    """Kubelet Summary API 호출"""
    try:
        url = f"{K8S_API_URL}/api/v1/nodes/{node}/proxy/stats/summary"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log_error(f"Failed to get node summary for {node}: {e}")
    return None

def get_nodes() -> List[str]:
    """클러스터 노드 목록"""
    try:
        resp = requests.get(f"{K8S_API_URL}/api/v1/nodes", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return [item['metadata']['name'] for item in data.get('items', [])]
    except Exception as e:
        log_error(f"Failed to get nodes: {e}")
    return []

def extract_service_name(pod_name: str) -> str:
    """Pod 이름에서 서비스 이름 추출"""
    parts = pod_name.rsplit('-', 2)
    if len(parts) >= 3:
        return parts[0]
    elif len(parts) == 2:
        return parts[0]
    return pod_name

def collect_kubelet_snapshot(include_all_ns: bool) -> Tuple[Dict, List[Dict]]:
    """
    Kubelet 스냅샷 + Network 수집용 Pod 목록
    Returns: (snapshot_data, pods_for_network)
    """
    snapshot = {}
    pods_for_network = []
    nodes = get_nodes()
    
    for node in nodes:
        data = get_node_summary(node)
        if not data:
            continue
        
        for pod in data.get('pods', []):
            pod_ref = pod.get('podRef', {})
            namespace = pod_ref.get('namespace', '')
            pod_name = pod_ref.get('name', '')
            
            # Namespace 필터링
            ns_config = TARGET_NAMESPACES.get(namespace)
            if not ns_config:
                continue
            if not ns_config['enabled'] and not include_all_ns:
                continue
            
            # CPU/Memory 수집
            cpu_total = 0
            cpu_app = 0
            cpu_sidecar = 0
            memory_ws = 0
            memory_rss = 0
            
            for container in pod.get('containers', []):
                container_name = container.get('name', '')
                cpu_nano = container.get('cpu', {}).get('usageCoreNanoSeconds', 0) or 0
                mem_ws = container.get('memory', {}).get('workingSetBytes', 0) or 0
                mem_rss = container.get('memory', {}).get('rssBytes', 0) or 0
                
                cpu_total += cpu_nano
                memory_ws += mem_ws
                memory_rss += mem_rss
                
                if 'istio' in container_name or 'envoy' in container_name:
                    cpu_sidecar += cpu_nano
                else:
                    cpu_app += cpu_nano
            
            key = f"{namespace}/{pod_name}"
            snapshot[key] = {
                'namespace': namespace,
                'category': ns_config['category'],
                'pod': pod_name,
                'service': extract_service_name(pod_name),
                'node': node,
                'cpu_total_nano': cpu_total,
                'cpu_app_nano': cpu_app,
                'cpu_sidecar_nano': cpu_sidecar,
                'memory_ws_bytes': memory_ws,
                'memory_rss_bytes': memory_rss
            }
            
            # Network 수집용 목록에 추가
            pods_for_network.append({
                'name': pod_name,
                'namespace': namespace
            })
    
    return snapshot, pods_for_network

# ============================================================
# 5. Main Collection Logic
# ============================================================
def collect_metrics(rps: int, duration: int, istio_enabled: bool, include_all_ns: bool):
    """전체 메트릭 수집"""
    
    log_info(f"Starting measurement: RPS={rps}, Duration={duration}s, Istio={istio_enabled}")
    
    # 1. PCM 시작
    pcm = PCMController(PCM_CSV_FILE)
    pcm.start()
    
    # 2. T1 스냅샷 (CPU/Memory + Network)
    log_info("Taking T1 snapshot...")
    t1_snapshot, pods_for_network = collect_kubelet_snapshot(include_all_ns)
    
    log_info(f"Collecting T1 network from {len(pods_for_network)} pods...")
    t1_network = collect_network_parallel(pods_for_network)
    
    t1_time = time.time()
    
    # 3. 대기
    log_info(f"Waiting {duration}s for measurement...")
    time.sleep(duration)
    
    # 4. T2 스냅샷 (CPU/Memory + Network)
    log_info("Taking T2 snapshot...")
    t2_snapshot, _ = collect_kubelet_snapshot(include_all_ns)
    
    log_info(f"Collecting T2 network...")
    t2_network = collect_network_parallel(pods_for_network)
    
    t2_time = time.time()
    
    # 5. PCM 종료 및 파싱
    pcm.stop()
    time.sleep(1)
    pcm_metrics = pcm.parse_results()
    
    # 6. Prometheus에서 Disk I/O 가져오기
    disk_metrics = get_disk_bandwidth(duration)
    
    # 7. Delta 계산 및 결과 조합
    actual_duration = t2_time - t1_time
    results = []
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    for key, t1_data in t1_snapshot.items():
        if key not in t2_snapshot:
            continue
        
        t2_data = t2_snapshot[key]
        
        # CPU Delta (millicores)
        cpu_delta = t2_data['cpu_total_nano'] - t1_data['cpu_total_nano']
        cpu_app_delta = t2_data['cpu_app_nano'] - t1_data['cpu_app_nano']
        cpu_sidecar_delta = t2_data['cpu_sidecar_nano'] - t1_data['cpu_sidecar_nano']
        
        # 음수 방지
        if cpu_delta < 0 or cpu_app_delta < 0:
            log_warn(f"Counter reset detected for {key}, skipping")
            continue
        
        cpu_millicores = int(cpu_delta / actual_duration / 1_000_000)
        cpu_app_millicores = int(cpu_app_delta / actual_duration / 1_000_000)
        cpu_sidecar_millicores = int(cpu_sidecar_delta / actual_duration / 1_000_000)
        
        # Memory (snapshot, MiB)
        memory_ws_mib = int(t2_data['memory_ws_bytes'] / 1024 / 1024)
        memory_rss_mib = int(t2_data['memory_rss_bytes'] / 1024 / 1024)
        
        # Network Delta (from kubectl exec)
        t1_rx, t1_tx = t1_network.get(key, (0, 0))
        t2_rx, t2_tx = t2_network.get(key, (0, 0))
        
        rx_delta = t2_rx - t1_rx
        tx_delta = t2_tx - t1_tx
        
        # 음수 방지 (counter reset)
        if rx_delta < 0:
            rx_delta = 0
        if tx_delta < 0:
            tx_delta = 0
        
        net_rx_kbps = round(rx_delta / actual_duration / 1024, 2)
        net_tx_kbps = round(tx_delta / actual_duration / 1024, 2)
        
        # Disk (from Prometheus)
        disk_data = disk_metrics.get(key, {})
        disk_read = disk_data.get('disk_read_kbps', 0)
        disk_write = disk_data.get('disk_write_kbps', 0)
        
        results.append({
            'Timestamp': timestamp,
            'RPS': rps,
            'Namespace': t1_data['namespace'],
            'Category': t1_data['category'],
            'Service': t1_data['service'],
            'Pod': t1_data['pod'],
            'Node': t1_data['node'],
            'CPU_Total(m)': cpu_millicores,
            'CPU_App(m)': cpu_app_millicores,
            'CPU_Sidecar(m)': cpu_sidecar_millicores,
            'Memory_WorkingSet(Mi)': memory_ws_mib,
            'Memory_RSS(Mi)': memory_rss_mib,
            'Net_RX(KB/s)': net_rx_kbps,
            'Net_TX(KB/s)': net_tx_kbps,
            'Disk_Read(KB/s)': disk_read,
            'Disk_Write(KB/s)': disk_write,
            'System_Mem_BW': pcm_metrics['mem_bw_system'],
            'System_LLC_Metric': pcm_metrics['llc_metric_system'],
            'Istio_Enabled': istio_enabled
        })
    
    # 8. CSV 저장
    if results:
        write_header = not os.path.exists(OUTPUT_FILE)
        with open(OUTPUT_FILE, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            if write_header:
                writer.writeheader()
            writer.writerows(results)
        
        log_info(f"Saved {len(results)} pod metrics to {OUTPUT_FILE}")
        
        # Summary 출력
        print_summary(results)
    else:
        log_warn("No metrics collected!")

def print_summary(results: List[Dict]):
    """카테고리별 요약 출력"""
    by_category = {}
    for row in results:
        cat = row['Category']
        if cat not in by_category:
            by_category[cat] = {'cpu': 0, 'mem': 0, 'net_rx': 0, 'net_tx': 0, 'count': 0}
        by_category[cat]['cpu'] += row['CPU_Total(m)']
        by_category[cat]['mem'] += row['Memory_WorkingSet(Mi)']
        by_category[cat]['net_rx'] += row['Net_RX(KB/s)']
        by_category[cat]['net_tx'] += row['Net_TX(KB/s)']
        by_category[cat]['count'] += 1
    
    print(f"\n   [Summary by Category]")
    print(f"   {'Category':<25} {'Pods':>5} {'CPU(m)':>10} {'Mem(Mi)':>10} {'RX(KB/s)':>12} {'TX(KB/s)':>12}")
    print(f"   {'-'*75}")
    
    total_cpu = 0
    total_mem = 0
    total_rx = 0
    total_tx = 0
    for cat, stats in sorted(by_category.items()):
        print(f"   {cat:<25} {stats['count']:>5} {stats['cpu']:>10} {stats['mem']:>10} {stats['net_rx']:>12.1f} {stats['net_tx']:>12.1f}")
        total_cpu += stats['cpu']
        total_mem += stats['mem']
        total_rx += stats['net_rx']
        total_tx += stats['net_tx']
    
    print(f"   {'-'*75}")
    print(f"   {'TOTAL':<25} {len(results):>5} {total_cpu:>10} {total_mem:>10} {total_rx:>12.1f} {total_tx:>12.1f}")

# ============================================================
# Main
# ============================================================
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 measure_step.py <RPS> [--istio] [--all-namespaces] [--duration=60]")
        sys.exit(1)
    
    rps = int(sys.argv[1])
    istio_enabled = '--istio' in sys.argv
    include_all_ns = '--all-namespaces' in sys.argv
    
    # Duration 파싱
    duration = 60
    for arg in sys.argv:
        if arg.startswith('--duration='):
            duration = int(arg.split('=')[1])
    
    # Namespace 설정 업데이트
    if include_all_ns:
        TARGET_NAMESPACES['istio-system']['enabled'] = True
        TARGET_NAMESPACES['kube-system']['enabled'] = True
    
    # 검증
    if not check_kubectl_proxy():
        log_error("kubectl proxy not running! Run: kubectl proxy --port=8001 &")
        sys.exit(1)
    
    collect_metrics(rps, duration, istio_enabled, include_all_ns)

if __name__ == "__main__":
    main()