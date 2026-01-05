#!/usr/bin/env python3
"""
measure_step.py v14.0 (Per-Core Analysis)
- Feature: Records usage (%) for ALL individual CPU cores (Core_00 ~ Core_XX)
- Feature: Detects CPU Imbalance (e.g., Core 5 is 100% while others are idle)
- Overhead: Negligible (Queries existing Prometheus data)
"""

import requests
import subprocess
import csv
import sys
import time
import os
from datetime import datetime
from typing import Dict, List

# ============================================================
# 1. 설정
# ============================================================
TOTAL_CPU_CORES = float(os.environ.get("TOTAL_CPU_CORES", 36.0))
TOTAL_MEMORY_GB = float(os.environ.get("TOTAL_MEMORY_GB", 252.0))
MEMORY_MAX_GBPS = float(os.environ.get("MEMORY_MAX_GBPS", 332.8))
DISK_MAX_MBPS = float(os.environ.get("DISK_MAX_MBPS", 3544.0))
DISK_DEVICE = os.environ.get("DISK_DEVICE", "nvme0n1")

OUTPUT_FILE = "k8s_full_metrics.csv"
PCM_CSV_FILE = "pcm_temp.csv"
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
PCM_PATH = os.environ.get("PCM_PATH", "./pcm.x")
SCRAPE_BUFFER_SECONDS = 15

TARGET_NAMESPACES = {
    'default': {'enabled': True, 'category': 'application'},
    'hotel-res': {'enabled': True, 'category': 'application'},
    'istio-system': {'enabled': False, 'category': 'istio-control-plane'},
    'kube-system': {'enabled': False, 'category': 'kubernetes-system'}
}

# ============================================================
# 유틸리티 & 클래스
# ============================================================
def log_info(msg: str): print(f"   [INFO] {msg}")
def log_warn(msg: str): print(f"   [WARN] {msg}")
def log_error(msg: str): print(f"   [ERROR] {msg}", file=sys.stderr)

def extract_service_name(pod_name: str) -> str:
    parts = pod_name.rsplit('-', 2)
    return parts[0] if len(parts) >= 2 else pod_name

class PCMController:
    def __init__(self, output_file: str, interval: float = 1.0):
        self.output_file = output_file
        self.interval = interval
        self.process = None
        self.available = False
        self._check_availability()
    
    def _check_availability(self):
        if not os.path.exists(PCM_PATH) or not os.access(PCM_PATH, os.X_OK): return
        if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
            self.available = True
            log_info("PCM is available")

    def start(self):
        if not self.available: return
        if os.path.exists(self.output_file): os.remove(self.output_file)
        subprocess.Popen(["sudo", PCM_PATH, str(self.interval), f"-csv={self.output_file}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self):
        if self.available:
            subprocess.run("sudo pkill -f pcm.x", shell=True, stderr=subprocess.DEVNULL)

    def parse_results(self) -> Dict[str, float]:
        default = {'mem_bw_system': 0.0, 'llc_metric_system': 0.0}
        if not self.available or not os.path.exists(self.output_file): return default
        try:
            with open(self.output_file, 'r') as f: lines = f.readlines()
            if len(lines) < 3: return default
            cats = lines[0].strip().split(',')
            headers = lines[1].strip().split(',')
            r_idx = next((i for i, (c, h) in enumerate(zip(cats, headers)) if c == 'System' and h == 'READ'), None)
            w_idx = next((i for i, (c, h) in enumerate(zip(cats, headers)) if c == 'System' and h == 'WRITE'), None)
            r_vals = [float(l.split(',')[r_idx]) for l in lines[2:] if r_idx < len(l.split(','))]
            w_vals = [float(l.split(',')[w_idx]) for l in lines[2:] if w_idx < len(l.split(','))]
            avg_bw = (sum(r_vals) + sum(w_vals)) / len(r_vals) if r_vals else 0
            return {'mem_bw_system': round(avg_bw, 2), 'llc_metric_system': 0.0}
        except: return default

class PrometheusClient:
    def __init__(self, url: str): self.url = url
    def query(self, query: str, time_point: float = None) -> List[Dict]:
        try:
            params = {'query': query, 'time': time_point} if time_point else {'query': query}
            resp = requests.get(f"{self.url}/api/v1/query", params=params, timeout=10)
            return resp.json().get('data', {}).get('result', []) if resp.status_code == 200 else []
        except: return []

# ============================================================
# 메인 로직
# ============================================================
def collect_metrics(rps: int, duration: int, istio_enabled: bool, include_all_ns: bool):
    prom = PrometheusClient(PROMETHEUS_URL)
    pcm = PCMController(PCM_CSV_FILE)

    if include_all_ns:
        TARGET_NAMESPACES['istio-system']['enabled'] = True
        TARGET_NAMESPACES['kube-system']['enabled'] = True

    log_info(f"Starting Measurement ({duration}s)...")
    pcm.start()
    time.sleep(duration)
    
    end_time = time.time()
    pcm.stop()
    pcm_metrics = pcm.parse_results()
    mem_bw_pct = (pcm_metrics['mem_bw_system'] / MEMORY_MAX_GBPS * 100) if MEMORY_MAX_GBPS > 0 else 0
    
    log_info(f"Waiting {SCRAPE_BUFFER_SECONDS}s for ingestion...")
    time.sleep(SCRAPE_BUFFER_SECONDS)
    
    # --- PromQL Queries ---
    q_pod = {
        'cpu': f'sum(rate(container_cpu_usage_seconds_total{{image!=""}}[{duration}s])) by (namespace, pod, container)',
        'mem': f'sum(avg_over_time(container_memory_working_set_bytes{{image!=""}}[{duration}s])) by (namespace, pod)',
        'disk_io': f'sum(rate(container_fs_reads_bytes_total[{duration}s]) + rate(container_fs_writes_bytes_total[{duration}s])) by (namespace, pod)'
    }
    q_node = {
        'disk_r_time': f'rate(node_disk_read_time_seconds_total{{device="{DISK_DEVICE}"}}[{duration}s])',
        'disk_r_ops':  f'rate(node_disk_reads_completed_total{{device="{DISK_DEVICE}"}}[{duration}s])',
        'disk_w_time': f'rate(node_disk_write_time_seconds_total{{device="{DISK_DEVICE}"}}[{duration}s])',
        'disk_w_ops':  f'rate(node_disk_writes_completed_total{{device="{DISK_DEVICE}"}}[{duration}s])',
        'disk_io_time': f'rate(node_disk_io_time_seconds_total{{device="{DISK_DEVICE}"}}[{duration}s])'
    }
    
    # [NEW] Per-Core CPU Usage Query
    # 100 - (idle_seconds * 100) = Used %
    q_cores = f'100 - (avg by (cpu) (rate(node_cpu_seconds_total{{mode="idle"}}[{duration}s])) * 100)'

    data_pod = {k: prom.query(q, time_point=end_time) for k, q in q_pod.items()}
    data_node = {k: prom.query(q, time_point=end_time) for k, q in q_node.items()}
    data_cores = prom.query(q_cores, time_point=end_time)

    # --- Process Per-Core Stats ---
    core_stats = {}
    max_core_usage = 0.0
    max_core_id = "N/A"
    
    for item in data_cores:
        cpu_id = int(item['metric'].get('cpu', -1))
        usage = float(item['value'][1])
        if cpu_id >= 0:
            core_key = f"Core_{cpu_id:02d}"
            core_stats[core_key] = usage
            if usage > max_core_usage:
                max_core_usage = usage
                max_core_id = str(cpu_id)

    # --- Node Disk Stat Calculation ---
    def gnv(k): return float(data_node[k][0]['value'][1]) if data_node.get(k) else 0.0
    r_lat = (gnv('disk_r_time') / gnv('disk_r_ops') * 1000) if gnv('disk_r_ops') > 0 else 0.0
    w_lat = (gnv('disk_w_time') / gnv('disk_w_ops') * 1000) if gnv('disk_w_ops') > 0 else 0.0
    disk_util = gnv('disk_io_time') * 100

    # --- Pod Metrics Processing ---
    pod_metrics = {}
    max_pod_cpu = 0.0
    max_pod_name = ""

    def get_val(item): return float(item['value'][1])

    for item in data_pod['cpu']:
        ns, pod = item['metric']['namespace'], item['metric']['pod']
        if ns not in TARGET_NAMESPACES: continue
        key = f"{ns}/{pod}"
        if key not in pod_metrics:
            pod_metrics[key] = {'ns': ns, 'pod': pod, 'cpu': 0, 'mem': 0, 'disk': 0}
        
        cpu_val = get_val(item) * 1000 # mCore
        pod_metrics[key]['cpu'] += cpu_val

    for item in data_pod['mem']:
        key = f"{item['metric']['namespace']}/{item['metric']['pod']}"
        if key in pod_metrics: pod_metrics[key]['mem'] = get_val(item) / 1024 / 1024
    
    for item in data_pod['disk_io']:
        key = f"{item['metric']['namespace']}/{item['metric']['pod']}"
        if key in pod_metrics: pod_metrics[key]['disk'] = get_val(item) / 1024 / 1024

    for k, v in pod_metrics.items():
        if v['cpu'] > max_pod_cpu:
            max_pod_cpu = v['cpu']
            max_pod_name = v['pod']

    # --- Save CSV ---
    results = []
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    for v in pod_metrics.values():
        row = {
            'Timestamp': ts, 'RPS': rps, 'Pod': v['pod'],
            'CPU(m)': int(v['cpu']), 'Mem(Mi)': int(v['mem']),
            'Disk(MB/s)': round(v['disk'], 2),
            'Node_Disk_Lat_R(ms)': round(r_lat, 2),
            'Node_Disk_Lat_W(ms)': round(w_lat, 2),
            'Node_Disk_Util(%)': round(disk_util, 2),
            'Mem_BW(%)': round(mem_bw_pct, 2),
            'Max_Core_Util(%)': round(max_core_usage, 2) # Summary Metric
        }
        # Add ALL Cores columns
        for c_k, c_v in core_stats.items():
            row[c_k] = round(c_v, 1)
            
        results.append(row)

    if results:
        write_header = not os.path.exists(OUTPUT_FILE)
        with open(OUTPUT_FILE, 'a', newline='') as f:
            # Dynamic Header Generation for Cores
            fieldnames = list(results[0].keys())
            # Ensure Core_XX are sorted
            core_cols = sorted([k for k in fieldnames if k.startswith('Core_')])
            other_cols = [k for k in fieldnames if not k.startswith('Core_')]
            final_header = other_cols + core_cols
            
            writer = csv.DictWriter(f, fieldnames=final_header)
            if write_header: writer.writeheader()
            writer.writerows(results)

        # Bottleneck Analysis
        total_cpu = sum(p['cpu'] for p in pod_metrics.values())
        cluster_cpu_pct = (total_cpu / (TOTAL_CPU_CORES * 1000)) * 100
        
        print(f"\n   [BOTTLENECK ANALYSIS REPORT]")
        print(f"   1. Cluster CPU    : {cluster_cpu_pct:5.1f}% (Total {int(total_cpu)}m)")
        print(f"   2. Max Single Pod : {max_pod_cpu:5.0f}m ({max_pod_name})")
        print(f"   3. Max Core Usage : {max_core_usage:5.1f}% (Core #{max_core_id})") # [NEW]
        print(f"   4. Memory BW      : {mem_bw_pct:5.1f}%")
        print(f"   5. Disk Latency   : R {r_lat:.1f}ms / W {w_lat:.1f}ms (Util: {disk_util:.1f}%)")

        bottlenecks = []
        if cluster_cpu_pct > 80: bottlenecks.append("Cluster CPU Saturation")
        if max_core_usage > 95: bottlenecks.append(f"Single Core Bottleneck (Core {max_core_id} @ {max_core_usage:.0f}%)")
        if mem_bw_pct > 80: bottlenecks.append("Memory Bandwidth")
        if r_lat > 5 or w_lat > 5: bottlenecks.append("Disk Latency")

        if bottlenecks:
            print(f"\n   🔴 CRITICAL: {', '.join(bottlenecks)}")
        elif cluster_cpu_pct < 60 and max_pod_cpu < 3000:
            print(f"\n   ⚠️  WARNING: Resources look FINE, but if Latency is high:")
            print(f"      -> Likely SOFTWARE Bottleneck (Locking, DB Connection, Concurrency)")
            print(f"      -> SOLUTION: Scale Out (Increase Replicas)")
        else:
            print(f"\n   🟢 SYSTEM STABLE (Watch for Latency)")

    else:
        log_warn("No metrics found.")

def main():
    rps = int(sys.argv[1])
    include_all_ns = '--all-namespaces' in sys.argv
    duration = 60
    for arg in sys.argv:
        if arg.startswith('--duration='): duration = int(arg.split('=')[1])
    collect_metrics(rps, duration, False, include_all_ns)

if __name__ == "__main__":
    main()