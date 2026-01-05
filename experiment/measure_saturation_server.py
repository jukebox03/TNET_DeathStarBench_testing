#!/usr/bin/env python3
"""
measure_saturation_local.py
- 로컬 서버 전용 (SSH 불필요)
- htop과 동일한 /proc/stat 기반 CPU 측정
- wrk2와 모니터링을 같은 서버에서 실행
"""

import subprocess
import time
import sys
import csv
import os
import re
import threading
from datetime import datetime

# ============================================================
# 설정
# ============================================================
TARGET_URL = os.environ.get("TARGET", "http://localhost:31643")
WRK_PATH = os.environ.get("WRK_PATH", "./wrk")
SCRIPT_PATH = os.environ.get("LUA_SCRIPT", "./DeathStarBench/hotelReservation/wrk2/scripts/hotel-reservation/mixed-workload_type_1.lua")

# 기본값
DEFAULT_RPS = 1000
DEFAULT_DURATION = 30
DEFAULT_THREADS = 4

# Saturation 판단 기준
P99_THRESHOLD_MS = 100.0

# 출력 디렉토리
OUTPUT_DIR = "./results"


class LocalCPUMonitor:
    """로컬 /proc/stat 기반 CPU 모니터링 (htop과 동일)"""
    
    def __init__(self, output_file):
        self.output_file = output_file
        self.running = False
        self.thread = None
        self.start_time = None
        
        # 통계
        self.samples = 0
        self.sum_avg = 0.0
        self.max_usage = 0.0
        self.max_core = 'N/A'
    
    def _read_proc_stat(self):
        """Parse /proc/stat and return per-core CPU times"""
        cores = {}
        with open('/proc/stat', 'r') as f:
            for line in f:
                if line.startswith('cpu') and not line.startswith('cpu '):
                    parts = line.split()
                    core_id = parts[0]
                    values = [int(x) for x in parts[1:]]
                    total = sum(values)
                    idle = values[3]  # idle is 4th field
                    cores[core_id] = {'total': total, 'idle': idle}
        return cores
    
    def _calculate_usage(self, prev, curr):
        """Calculate CPU usage between two snapshots"""
        usages = {}
        for core in curr:
            if core in prev:
                delta_total = curr[core]['total'] - prev[core]['total']
                delta_idle = curr[core]['idle'] - prev[core]['idle']
                if delta_total > 0:
                    usage = (1 - delta_idle / delta_total) * 100
                    usages[core] = round(usage, 2)
        return usages
    
    def _monitor_loop(self):
        """Main monitoring loop"""
        os.makedirs(os.path.dirname(self.output_file) if os.path.dirname(self.output_file) else '.', exist_ok=True)
        
        with open(self.output_file, 'w', newline='') as f:
            writer = csv.writer(f)
            headers_written = False
            
            prev_stat = self._read_proc_stat()
            time.sleep(1)
            
            while self.running:
                curr_stat = self._read_proc_stat()
                usages = self._calculate_usage(prev_stat, curr_stat)
                
                if usages:
                    # Write headers on first sample
                    if not headers_written:
                        sorted_cores = sorted(usages.keys(), key=lambda x: int(x[3:]))
                        writer.writerow(['timestamp', 'relative_sec'] + sorted_cores)
                        headers_written = True
                    
                    # Write data
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    rel_time = round(time.time() - self.start_time, 1)
                    sorted_cores = sorted(usages.keys(), key=lambda x: int(x[3:]))
                    row = [timestamp, rel_time] + [usages[c] for c in sorted_cores]
                    writer.writerow(row)
                    f.flush()
                    
                    # Update statistics
                    avg = sum(usages.values()) / len(usages)
                    self.sum_avg += avg
                    self.samples += 1
                    
                    max_core = max(usages, key=usages.get)
                    if usages[max_core] > self.max_usage:
                        self.max_usage = usages[max_core]
                        self.max_core = max_core
                
                prev_stat = curr_stat
                time.sleep(1)
    
    def start(self):
        self.running = True
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._monitor_loop)
        self.thread.start()
        print(f"[OK] CPU monitor started (local /proc/stat)")
    
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)
        print(f"[OK] CPU monitor stopped")
    
    def get_stats(self):
        return {
            'avg': self.sum_avg / self.samples if self.samples > 0 else 0,
            'max': self.max_usage,
            'max_core': self.max_core,
            'samples': self.samples
        }


def validate_environment():
    """환경 검증"""
    errors = []
    
    if not os.path.exists(WRK_PATH):
        errors.append(f"wrk not found: {WRK_PATH}")
    elif not os.access(WRK_PATH, os.X_OK):
        errors.append(f"wrk not executable: {WRK_PATH}")
    
    if not os.path.exists(SCRIPT_PATH):
        errors.append(f"Lua script not found: {SCRIPT_PATH}")
    
    if not os.path.exists('/proc/stat'):
        errors.append("/proc/stat not found (not Linux?)")
    
    if errors:
        print("\n" + "="*60)
        print("FATAL: Environment validation failed!")
        print("="*60)
        for err in errors:
            print(f"  ✗ {err}")
        print("="*60)
        sys.exit(1)
    
    print(f"[OK] wrk: {WRK_PATH}")
    print(f"[OK] Lua: {SCRIPT_PATH}")
    print(f"[OK] /proc/stat available")


def run_wrk(rps, duration, threads=DEFAULT_THREADS):
    """wrk2 실행"""
    connections = max(100, min(rps // 10, 10000))
    
    cmd = [
        WRK_PATH,
        "-D", "exp",
        "-t", str(threads),
        "-c", str(connections),
        "-d", f"{duration}s",
        "-L",
        "-s", SCRIPT_PATH,
        TARGET_URL,
        "-R", str(rps)
    ]
    
    # LuaSocket 경로 설정
    env = os.environ.copy()
    env["LUA_PATH"] = "/usr/share/lua/5.1/?.lua;/usr/share/lua/5.1/?/init.lua;;"
    env["LUA_CPATH"] = "/usr/lib/x86_64-linux-gnu/lua/5.1/?.so;;"
    
    print(f"[RUN] {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return result.stdout, result.stderr


def parse_latency_to_ms(lat_str):
    """Latency 문자열을 ms로 변환"""
    if not lat_str or lat_str == 'N/A':
        return None
    
    match = re.match(r'(\d+\.?\d*)(us|ms|s)', lat_str)
    if match:
        val, unit = float(match.group(1)), match.group(2)
        if unit == 'us':
            return val / 1000
        if unit == 'ms':
            return val
        if unit == 's':
            return val * 1000
    return None


def parse_wrk_output(output):
    """wrk2 출력 파싱"""
    results = {
        'actual_rps': 0,
        'latencies': {},
        'errors': 0
    }
    
    # RPS
    match = re.search(r"Requests/sec:\s+(\d+\.?\d*)", output)
    if match:
        results['actual_rps'] = float(match.group(1))
    
    # Latency percentiles
    patterns = {
        "P50": r"50\.000%\s+(\d+\.?\d*[a-z]+)",
        "P75": r"75\.000%\s+(\d+\.?\d*[a-z]+)",
        "P90": r"90\.000%\s+(\d+\.?\d*[a-z]+)",
        "P99": r"99\.000%\s+(\d+\.?\d*[a-z]+)",
        "P99.9": r"99\.900%\s+(\d+\.?\d*[a-z]+)",
    }
    for label, pattern in patterns.items():
        match = re.search(pattern, output)
        if match:
            results['latencies'][label] = match.group(1)
    
    # Errors
    match = re.search(r"Socket errors:\s+connect\s+(\d+),\s+read\s+(\d+),\s+write\s+(\d+),\s+timeout\s+(\d+)", output)
    if match:
        results['errors'] = sum(map(int, match.groups()))
    
    match = re.search(r"Non-2xx or 3xx responses:\s+(\d+)", output)
    if match:
        results['errors'] += int(match.group(1))
    
    return results


def check_saturation(wrk_results, cpu_stats, target_rps):
    """Saturation 판단"""
    reasons = []
    
    p99_ms = parse_latency_to_ms(wrk_results['latencies'].get('P99', 'N/A'))
    p50_ms = parse_latency_to_ms(wrk_results['latencies'].get('P50', 'N/A'))
    
    metrics = {
        'p50_ms': p50_ms,
        'p99_ms': p99_ms,
        'p99_p50_ratio': None,
        'rps_achievement': (wrk_results['actual_rps'] / target_rps * 100) if target_rps > 0 else 0
    }
    
    if p99_ms and p99_ms > P99_THRESHOLD_MS:
        reasons.append(f"P99 latency ({p99_ms:.1f}ms) > {P99_THRESHOLD_MS}ms")
    
    if p50_ms and p99_ms and p50_ms > 0:
        ratio = p99_ms / p50_ms
        metrics['p99_p50_ratio'] = ratio
        if ratio > 10:
            reasons.append(f"Tail latency explosion (P99/P50 = {ratio:.1f}x)")
    
    if cpu_stats['max'] > 85:
        reasons.append(f"CPU bottleneck ({cpu_stats['max_core']} @ {cpu_stats['max']:.1f}%)")
    
    if wrk_results['actual_rps'] < target_rps * 0.95:
        reasons.append(f"RPS underrun ({wrk_results['actual_rps']:.0f}/{target_rps})")
    
    if wrk_results['errors'] > 0:
        reasons.append(f"Errors: {wrk_results['errors']}")
    
    return len(reasons) > 0, reasons, metrics


def print_report(target_rps, duration, wrk_results, cpu_stats, is_saturated, reasons, metrics):
    """결과 리포트 출력"""
    print("\n" + "="*65)
    print(f"{'SATURATION TEST REPORT':^65}")
    print(f"{'(Local execution - htop-equivalent CPU)':^65}")
    print("="*65)
    
    print(f"\n [Configuration]")
    print(f"   Target URL : {TARGET_URL}")
    print(f"   Duration   : {duration}s")
    
    print(f"\n [Load]")
    print(f"   Target RPS : {target_rps:,}")
    print(f"   Actual RPS : {wrk_results['actual_rps']:,.1f} ({metrics['rps_achievement']:.1f}%)")
    
    print(f"\n [Latency]")
    for pct in ['P50', 'P75', 'P90', 'P99', 'P99.9']:
        val = wrk_results['latencies'].get(pct, 'N/A')
        ms = parse_latency_to_ms(val)
        if ms:
            status = " ⚠" if pct == 'P99' and ms > P99_THRESHOLD_MS else ""
            print(f"   {pct:8} : {val:>12} ({ms:>8.2f} ms){status}")
        else:
            print(f"   {pct:8} : {val}")
    
    if metrics['p99_p50_ratio']:
        ratio = metrics['p99_p50_ratio']
        status = " ⚠ (queuing detected)" if ratio > 10 else " ✓"
        print(f"   P99/P50   : {ratio:.1f}x{status}")
    
    print(f"\n [CPU] (htop-equivalent)")
    print(f"   Avg (all)  : {cpu_stats['avg']:.1f}%")
    print(f"   Max core   : {cpu_stats['max_core']} @ {cpu_stats['max']:.1f}%", end="")
    print(" ⚠" if cpu_stats['max'] > 85 else " ✓")
    print(f"   Samples    : {cpu_stats['samples']}")
    
    print(f"\n [Errors]")
    print(f"   Total      : {wrk_results['errors']}")
    
    print(f"\n [Saturation Analysis]")
    print(f"   Criterion  : P99 < {P99_THRESHOLD_MS}ms")
    
    if is_saturated:
        print(f"\n   ╔{'═'*57}╗")
        print(f"   ║{'⚠  SYSTEM SATURATED':^57}║")
        print(f"   ╚{'═'*57}╝")
        for reason in reasons:
            print(f"     • {reason}")
    else:
        print(f"\n   ╔{'═'*57}╗")
        print(f"   ║{'✓  SYSTEM HEALTHY':^57}║")
        print(f"   ╚{'═'*57}╝")
    
    print("\n" + "="*65)


def save_results(output_dir, target_rps, duration, wrk_results, cpu_stats, is_saturated, reasons, metrics):
    """결과를 CSV로 저장"""
    os.makedirs(output_dir, exist_ok=True)
    
    summary_file = os.path.join(output_dir, "saturation_results.csv")
    file_exists = os.path.exists(summary_file)
    
    with open(summary_file, 'a', newline='') as f:
        writer = csv.writer(f)
        
        if not file_exists:
            writer.writerow([
                'Timestamp', 'Target_RPS', 'Duration_sec', 'Actual_RPS', 'RPS_%',
                'P50_ms', 'P99_ms', 'P99.9_ms', 'P99_P50_Ratio',
                'Avg_CPU_%', 'Max_CPU_%', 'Max_CPU_Core',
                'Errors', 'Is_Saturated', 'Reasons'
            ])
        
        writer.writerow([
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            target_rps,
            duration,
            round(wrk_results['actual_rps'], 2),
            round(metrics['rps_achievement'], 2),
            round(metrics['p50_ms'], 3) if metrics['p50_ms'] else 'N/A',
            round(metrics['p99_ms'], 3) if metrics['p99_ms'] else 'N/A',
            round(parse_latency_to_ms(wrk_results['latencies'].get('P99.9', 'N/A')) or 0, 3) or 'N/A',
            round(metrics['p99_p50_ratio'], 2) if metrics['p99_p50_ratio'] else 'N/A',
            round(cpu_stats['avg'], 2),
            round(cpu_stats['max'], 2),
            cpu_stats['max_core'],
            wrk_results['errors'],
            'YES' if is_saturated else 'NO',
            '; '.join(reasons) if reasons else 'None'
        ])
    
    print(f" [Saved] {summary_file}")


def main():
    target_rps = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RPS
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DURATION
    
    print("="*65)
    print(f"{'SATURATION MEASUREMENT (Local)':^65}")
    print(f"{'(No SSH - direct /proc/stat monitoring)':^65}")
    print("="*65)
    print(f" Target  : {TARGET_URL}")
    print(f" Load    : {target_rps:,} RPS for {duration}s")
    print(f" Threshold: P99 < {P99_THRESHOLD_MS}ms")
    print("="*65)
    
    # 1. 환경 검증
    print("\n[1/5] Validating environment...")
    validate_environment()
    
    # 2. CPU 모니터 시작
    print("\n[2/5] Starting CPU monitor...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    cpu_log = os.path.join(OUTPUT_DIR, f"cpu_{target_rps}rps_{duration}s.csv")
    monitor = LocalCPUMonitor(cpu_log)
    monitor.start()
    
    # 3. wrk2 실행
    print("\n[3/5] Running load test...")
    time.sleep(2)  # 모니터 안정화
    wrk_output, wrk_stderr = run_wrk(target_rps, duration)
    
    # 4. 모니터 중지
    print("\n[4/5] Stopping monitor...")
    time.sleep(2)
    monitor.stop()
    cpu_stats = monitor.get_stats()
    
    # 5. 분석 및 리포트
    print("\n[5/5] Analyzing results...")
    wrk_results = parse_wrk_output(wrk_output)
    
    # 디버그: wrk 출력이 비어있으면 표시
    if wrk_results['actual_rps'] == 0:
        print("\n[DEBUG] wrk2 output:")
        print(wrk_output[:500] if wrk_output else "EMPTY")
        print("\n[DEBUG] wrk2 stderr:")
        print(wrk_stderr[:500] if wrk_stderr else "EMPTY")
    
    is_saturated, reasons, metrics = check_saturation(wrk_results, cpu_stats, target_rps)
    
    print_report(target_rps, duration, wrk_results, cpu_stats, is_saturated, reasons, metrics)
    save_results(OUTPUT_DIR, target_rps, duration, wrk_results, cpu_stats, is_saturated, reasons, metrics)
    
    print(f"\n [Output Files]")
    print(f"   CPU log    : {cpu_log}")
    print(f"   Summary    : {OUTPUT_DIR}/saturation_results.csv")
    print("="*65)


if __name__ == "__main__":
    main()