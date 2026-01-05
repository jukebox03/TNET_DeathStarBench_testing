#!/usr/bin/env python3
"""
measure_saturation_v2.py
- 측정 오버헤드 최소화 버전
- htop과 동일한 /proc/stat 기반 CPU 측정
- 서버 로컬 스크립트 실행 방식 (테스트 중 SSH 연결 없음)
"""

import subprocess
import time
import sys
import csv
import os
import re
from datetime import datetime
from pathlib import Path

# ============================================================
# 설정
# ============================================================
TARGET_URL = os.environ.get("TARGET", "http://147.46.219.132:31643")
WRK_PATH = os.environ.get("WRK_PATH", "./wrk")
SCRIPT_PATH = os.environ.get("LUA_SCRIPT", "./DeathStarBench/hotelReservation/wrk2/scripts/hotel-reservation/mixed-workload_type_1.lua")

SERVER_USER = os.environ.get("SERVER_USER", "jukebox")
SERVER_IP = os.environ.get("SERVER_IP", "147.46.219.132")

# 기본값
DEFAULT_RPS = 1000
DEFAULT_DURATION = 30  # 초 단위
DEFAULT_THREADS = 4

# Saturation 판단 기준
P99_THRESHOLD_MS = 100.0

# 원격 서버 경로
REMOTE_MONITOR_SCRIPT = "/tmp/cpu_monitor.sh"
REMOTE_CPU_LOG = "/tmp/cpu_log.csv"

# 로컬 출력
LOCAL_OUTPUT_DIR = "./results"


# ============================================================
# 서버 모니터링 스크립트 (Bash)
# ============================================================
CPU_MONITOR_SCRIPT = r'''#!/bin/bash
# cpu_monitor.sh - htop과 동일한 /proc/stat 기반 CPU 측정
# 오버헤드: cat + sleep만 사용 (최소)

OUTPUT_FILE="${1:-/tmp/cpu_log.csv}"
DURATION="${2:-60}"
INTERVAL="${3:-1}"

# PID 파일 생성 (종료 시그널용)
echo $$ > /tmp/cpu_monitor.pid

cleanup() {
    rm -f /tmp/cpu_monitor.pid
    exit 0
}
trap cleanup SIGTERM SIGINT

# 코어 수 확인
NUM_CORES=$(grep -c "^cpu[0-9]" /proc/stat)

# 헤더 작성
HEADER="timestamp,relative_sec"
for i in $(seq 0 $((NUM_CORES-1))); do
    HEADER="$HEADER,cpu$i"
done
echo "$HEADER" > "$OUTPUT_FILE"

# 이전 값 저장
declare -A prev_total prev_idle
START_TIME=$(date +%s%3N)  # 밀리초

# 초기값 읽기
while IFS= read -r line; do
    if [[ $line =~ ^cpu([0-9]+)[[:space:]]+(.*) ]]; then
        core=${BASH_REMATCH[1]}
        read -ra vals <<< "${BASH_REMATCH[2]}"
        total=0
        for v in "${vals[@]}"; do ((total += v)); done
        idle=${vals[3]}
        prev_total[$core]=$total
        prev_idle[$core]=$idle
    fi
done < /proc/stat

sleep "$INTERVAL"

# 측정 루프
END_TIME=$((START_TIME + DURATION * 1000))
while [ $(date +%s%3N) -lt $END_TIME ]; do
    NOW=$(date +%s%3N)
    REL=$(echo "scale=1; ($NOW - $START_TIME) / 1000" | bc)
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    
    LINE="$TIMESTAMP,$REL"
    
    while IFS= read -r line; do
        if [[ $line =~ ^cpu([0-9]+)[[:space:]]+(.*) ]]; then
            core=${BASH_REMATCH[1]}
            read -ra vals <<< "${BASH_REMATCH[2]}"
            total=0
            for v in "${vals[@]}"; do ((total += v)); done
            idle=${vals[3]}
            
            delta_total=$((total - prev_total[$core]))
            delta_idle=$((idle - prev_idle[$core]))
            
            if [ $delta_total -gt 0 ]; then
                # Bash 정수 연산 + awk로 소수점 계산
                usage=$(awk "BEGIN {printf \"%.2f\", (1 - $delta_idle / $delta_total) * 100}")
            else
                usage="0.00"
            fi
            
            LINE="$LINE,$usage"
            prev_total[$core]=$total
            prev_idle[$core]=$idle
        fi
    done < /proc/stat
    
    echo "$LINE" >> "$OUTPUT_FILE"
    sleep "$INTERVAL"
done

rm -f /tmp/cpu_monitor.pid
'''


def ssh_cmd(cmd, check=True, capture=True):
    """SSH 명령 실행 헬퍼"""
    full_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                f"{SERVER_USER}@{SERVER_IP}", cmd]
    if capture:
        result = subprocess.run(full_cmd, capture_output=True, text=True)
        if check and result.returncode != 0:
            print(f"[SSH ERROR] {result.stderr}")
        return result
    else:
        return subprocess.run(full_cmd)


def scp_download(remote_path, local_path):
    """SCP로 파일 다운로드"""
    cmd = ["scp", "-o", "BatchMode=yes", 
           f"{SERVER_USER}@{SERVER_IP}:{remote_path}", local_path]
    return subprocess.run(cmd, capture_output=True)


def validate_environment():
    """환경 검증"""
    errors = []
    
    # wrk 확인
    if not os.path.exists(WRK_PATH):
        errors.append(f"wrk not found: {WRK_PATH}")
    elif not os.access(WRK_PATH, os.X_OK):
        errors.append(f"wrk not executable: {WRK_PATH}")
    
    # Lua 스크립트 확인
    if not os.path.exists(SCRIPT_PATH):
        errors.append(f"Lua script not found: {SCRIPT_PATH}")
    
    # SSH 연결 확인
    result = ssh_cmd("echo OK", check=False)
    if result.returncode != 0 or "OK" not in result.stdout:
        errors.append(f"SSH connection failed to {SERVER_USER}@{SERVER_IP}")
        errors.append(f"  stderr: {result.stderr.strip()}")
    
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
    print(f"[OK] SSH: {SERVER_USER}@{SERVER_IP}")


def deploy_monitor_script():
    """서버에 모니터링 스크립트 배포"""
    # 스크립트 내용을 heredoc으로 전송
    cmd = f"cat > {REMOTE_MONITOR_SCRIPT} << 'SCRIPT_EOF'\n{CPU_MONITOR_SCRIPT}\nSCRIPT_EOF"
    result = ssh_cmd(cmd)
    
    # 실행 권한 부여
    ssh_cmd(f"chmod +x {REMOTE_MONITOR_SCRIPT}")
    
    print(f"[OK] Monitor script deployed to {REMOTE_MONITOR_SCRIPT}")


def start_remote_monitor(duration):
    """원격 모니터링 시작 (백그라운드)"""
    # 이전 로그 삭제
    ssh_cmd(f"rm -f {REMOTE_CPU_LOG} /tmp/cpu_monitor.pid")
    
    # 백그라운드 실행 (nohup)
    # duration + 10초 여유
    cmd = f"nohup {REMOTE_MONITOR_SCRIPT} {REMOTE_CPU_LOG} {duration + 10} 1 > /dev/null 2>&1 &"
    ssh_cmd(cmd)
    
    # 시작 확인
    time.sleep(1)
    result = ssh_cmd("cat /tmp/cpu_monitor.pid 2>/dev/null || echo 'NOT_RUNNING'")
    if "NOT_RUNNING" in result.stdout:
        print("[WARN] Monitor may not have started properly")
    else:
        print(f"[OK] Remote monitor started (PID: {result.stdout.strip()})")


def stop_remote_monitor():
    """원격 모니터링 종료"""
    # PID로 종료
    ssh_cmd("kill $(cat /tmp/cpu_monitor.pid 2>/dev/null) 2>/dev/null || true")
    time.sleep(0.5)
    print("[OK] Remote monitor stopped")


def download_cpu_log(local_path):
    """CPU 로그 다운로드"""
    result = scp_download(REMOTE_CPU_LOG, local_path)
    if result.returncode == 0:
        print(f"[OK] CPU log downloaded: {local_path}")
        return True
    else:
        print(f"[WARN] Failed to download CPU log")
        return False


def run_wrk(rps, duration, threads=DEFAULT_THREADS):
    subprocess.run(["sudo", "hwclock", "-s"], capture_output=True)

    """wrk2 실행"""
    connections = max(100, min(rps // 10, 10000))  # 100 ~ 10000
    
    cmd = [
        WRK_PATH,
        "-D", "exp",           # 지수 분포
        "-t", str(threads),    # 스레드 수
        "-c", str(connections),
        "-d", f"{duration}s",
        "-L",                  # 상세 latency
        "-s", SCRIPT_PATH,
        TARGET_URL,
        "-R", str(rps)
    ]
    
    # LuaSocket 경로 설정 (wrk2 내장 LuaJIT용)
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
        'errors': 0,
        'transfer': 'N/A'
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
        "P99.99": r"99\.990%\s+(\d+\.?\d*[a-z]+)"
    }
    for label, pattern in patterns.items():
        match = re.search(pattern, output)
        if match:
            results['latencies'][label] = match.group(1)
    
    # Errors
    match = re.search(r"Socket errors:\s+connect\s+(\d+),\s+read\s+(\d+),\s+write\s+(\d+),\s+timeout\s+(\d+)", output)
    if match:
        results['errors'] = sum(map(int, match.groups()))
    
    # Non-2xx responses
    match = re.search(r"Non-2xx or 3xx responses:\s+(\d+)", output)
    if match:
        results['errors'] += int(match.group(1))
    
    return results


def analyze_cpu_log(csv_path):
    """CPU 로그 분석"""
    if not os.path.exists(csv_path):
        return {'avg': 0, 'max': 0, 'max_core': 'N/A', 'samples': 0}
    
    total_avg = 0
    max_usage = 0
    max_core = 'N/A'
    samples = 0
    
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        cores = [c for c in reader.fieldnames if c.startswith('cpu')]
        
        for row in reader:
            samples += 1
            usages = {c: float(row[c]) for c in cores if row[c]}
            
            if usages:
                avg = sum(usages.values()) / len(usages)
                total_avg += avg
                
                core_max = max(usages, key=usages.get)
                if usages[core_max] > max_usage:
                    max_usage = usages[core_max]
                    max_core = core_max
    
    return {
        'avg': total_avg / samples if samples > 0 else 0,
        'max': max_usage,
        'max_core': max_core,
        'samples': samples
    }


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
    
    # 1. P99 > 100ms
    if p99_ms and p99_ms > P99_THRESHOLD_MS:
        reasons.append(f"P99 latency ({p99_ms:.1f}ms) > {P99_THRESHOLD_MS}ms")
    
    # 2. Tail latency explosion
    if p50_ms and p99_ms and p50_ms > 0:
        ratio = p99_ms / p50_ms
        metrics['p99_p50_ratio'] = ratio
        if ratio > 10:
            reasons.append(f"Tail latency explosion (P99/P50 = {ratio:.1f}x)")
    
    # 3. CPU 병목
    if cpu_stats['max'] > 85:
        reasons.append(f"CPU bottleneck ({cpu_stats['max_core']} @ {cpu_stats['max']:.1f}%)")
    
    # 4. RPS 미달
    if wrk_results['actual_rps'] < target_rps * 0.95:
        reasons.append(f"RPS underrun ({wrk_results['actual_rps']:.0f}/{target_rps})")
    
    # 5. 에러
    if wrk_results['errors'] > 0:
        reasons.append(f"Errors: {wrk_results['errors']}")
    
    return len(reasons) > 0, reasons, metrics


def print_report(target_rps, duration, wrk_results, cpu_stats, is_saturated, reasons, metrics):
    """결과 리포트 출력"""
    print("\n" + "="*65)
    print(f"{'SATURATION TEST REPORT':^65}")
    print(f"{'(htop-equivalent CPU measurement, minimal overhead)':^65}")
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
    # 인자 파싱
    target_rps = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RPS
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DURATION
    
    print("="*65)
    print(f"{'SATURATION MEASUREMENT v2':^65}")
    print(f"{'(Minimal overhead, htop-equivalent CPU)':^65}")
    print("="*65)
    print(f" Target  : {TARGET_URL}")
    print(f" Load    : {target_rps:,} RPS for {duration}s")
    print(f" Threshold: P99 < {P99_THRESHOLD_MS}ms")
    print("="*65)
    
    # 1. 환경 검증
    print("\n[1/6] Validating environment...")
    validate_environment()
    
    # 2. 모니터링 스크립트 배포
    print("\n[2/6] Deploying monitor script...")
    deploy_monitor_script()
    
    # 3. 원격 모니터링 시작
    print("\n[3/6] Starting remote CPU monitor...")
    start_remote_monitor(duration)
    
    # 4. wrk2 실행 (여기서 SSH 연결 없음 - 최소 오버헤드)
    print("\n[4/6] Running load test...")
    time.sleep(2)  # 모니터 안정화 대기
    wrk_output, wrk_stderr = run_wrk(target_rps, duration)
    
    # 5. 모니터링 종료 및 결과 수집
    print("\n[5/6] Collecting results...")
    time.sleep(2)  # 마지막 샘플 대기
    stop_remote_monitor()
    
    # CPU 로그 다운로드
    os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)
    local_cpu_log = os.path.join(LOCAL_OUTPUT_DIR, f"cpu_{target_rps}rps_{duration}s.csv")
    download_cpu_log(local_cpu_log)
    
    # 6. 분석 및 리포트
    print("\n[6/6] Analyzing results...")
    wrk_results = parse_wrk_output(wrk_output)
    cpu_stats = analyze_cpu_log(local_cpu_log)
    is_saturated, reasons, metrics = check_saturation(wrk_results, cpu_stats, target_rps)
    
    print_report(target_rps, duration, wrk_results, cpu_stats, is_saturated, reasons, metrics)
    save_results(LOCAL_OUTPUT_DIR, target_rps, duration, wrk_results, cpu_stats, is_saturated, reasons, metrics)
    
    print(f"\n [Output Files]")
    print(f"   CPU log    : {local_cpu_log}")
    print(f"   Summary    : {LOCAL_OUTPUT_DIR}/saturation_results.csv")
    print("="*65)


if __name__ == "__main__":
    main()