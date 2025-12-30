#!/usr/bin/env python3
"""
개선된 wrk2 출력 파싱 스크립트
- P75, P90, P99.9 추가
- 실제 처리량 (Requests/sec)
- 에러율 (Socket errors, Non-2xx)
- 전송량 (Transfer/sec)
"""

import sys
import re
import csv
import os
from typing import Dict, Optional

# ============================================================
# 설정
# ============================================================
OUTPUT_CSV = "latency_stats.csv"

# ============================================================
# 파싱 함수
# ============================================================
def parse_latency_value(content: str, pattern: str) -> Optional[str]:
    """정규식 패턴으로 latency 값 추출"""
    match = re.search(pattern, content, re.IGNORECASE)
    if match:
        return match.group(1) + match.group(2)
    return None

def parse_numeric_value(content: str, pattern: str) -> Optional[float]:
    """정규식 패턴으로 숫자 값 추출"""
    match = re.search(pattern, content, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None

def parse_transfer_rate(content: str) -> Optional[str]:
    """Transfer/sec 추출"""
    match = re.search(r'Transfer/sec:\s+([0-9\.]+)([KMGT]?B)', content)
    if match:
        return match.group(1) + match.group(2)
    return None

def parse_wrk_output(content: str) -> Dict:
    """
    wrk2 출력 전체 파싱
    
    예시 출력:
    Running 90s test @ http://192.168.49.2:30918
      4 threads and 100 connections
      Thread Stats   Avg      Stdev     Max   +/- Stdev
        Latency     4.32ms    2.10ms  45.23ms   78.54%
        Req/Sec     232.45     34.12   312.00    67.89%
      Latency Distribution (HdrHistogram - Recorded Latency)
       50.000%    3.89ms
       75.000%    5.12ms
       90.000%    6.78ms
       99.000%   12.34ms
       99.900%   23.45ms
      ...
      Requests/sec:   928.45
      Transfer/sec:    1.23MB
    """
    
    result = {
        # Latency 메트릭
        'lat_mean': 'N/A',
        'lat_stdev': 'N/A',
        'lat_max': 'N/A',
        'lat_p50': 'N/A',
        'lat_p75': 'N/A',
        'lat_p90': 'N/A',
        'lat_p99': 'N/A',
        'lat_p999': 'N/A',
        
        # 처리량 메트릭
        'actual_rps': 0.0,
        'transfer_rate': 'N/A',
        
        # 에러 메트릭
        'total_requests': 0,
        'socket_errors': 0,
        'non_2xx_responses': 0,
        'error_rate': 0.0,
        'timeout_errors': 0
    }
    
    # --- Latency Stats (Avg, Stdev, Max) ---
    # 패턴: "Latency   4.32ms   2.10ms  45.23ms"
    lat_stats = re.search(
        r'Latency\s+([0-9\.]+)(us|ms|s)\s+([0-9\.]+)(us|ms|s)\s+([0-9\.]+)(us|ms|s)',
        content
    )
    if lat_stats:
        result['lat_mean'] = lat_stats.group(1) + lat_stats.group(2)
        result['lat_stdev'] = lat_stats.group(3) + lat_stats.group(4)
        result['lat_max'] = lat_stats.group(5) + lat_stats.group(6)
    
    # --- Percentiles ---
    percentile_patterns = {
        'lat_p50': r'50\.000%\s+([0-9\.]+)(us|ms|s)',
        'lat_p75': r'75\.000%\s+([0-9\.]+)(us|ms|s)',
        'lat_p90': r'90\.000%\s+([0-9\.]+)(us|ms|s)',
        'lat_p99': r'99\.000%\s+([0-9\.]+)(us|ms|s)',
        'lat_p999': r'99\.900%\s+([0-9\.]+)(us|ms|s)'
    }
    
    for key, pattern in percentile_patterns.items():
        val = parse_latency_value(content, pattern)
        if val:
            result[key] = val
    
    # --- Requests/sec ---
    rps_match = re.search(r'Requests/sec:\s+([0-9\.]+)', content)
    if rps_match:
        result['actual_rps'] = float(rps_match.group(1))
    
    # --- Transfer/sec ---
    result['transfer_rate'] = parse_transfer_rate(content) or 'N/A'
    
    # --- Total Requests ---
    total_match = re.search(r'(\d+)\s+requests\s+in', content)
    if total_match:
        result['total_requests'] = int(total_match.group(1))
    
    # --- Socket Errors ---
    # 패턴: "Socket errors: connect 0, read 5, write 0, timeout 3"
    socket_err = re.search(
        r'Socket errors:\s+connect\s+(\d+),\s+read\s+(\d+),\s+write\s+(\d+),\s+timeout\s+(\d+)',
        content
    )
    if socket_err:
        connect_err = int(socket_err.group(1))
        read_err = int(socket_err.group(2))
        write_err = int(socket_err.group(3))
        timeout_err = int(socket_err.group(4))
        result['socket_errors'] = connect_err + read_err + write_err + timeout_err
        result['timeout_errors'] = timeout_err
    
    # --- Non-2xx responses ---
    non_2xx = re.search(r'Non-2xx or 3xx responses:\s+(\d+)', content)
    if non_2xx:
        result['non_2xx_responses'] = int(non_2xx.group(1))
    
    # --- Error Rate 계산 ---
    if result['total_requests'] > 0:
        total_errors = result['socket_errors'] + result['non_2xx_responses']
        result['error_rate'] = round(total_errors / result['total_requests'] * 100, 4)
    
    return result

def save_to_csv(rps: str, metrics: Dict):
    """CSV 파일에 저장"""
    file_exists = os.path.isfile(OUTPUT_CSV)
    
    with open(OUTPUT_CSV, 'a', newline='') as f:
        writer = csv.writer(f)
        
        if not file_exists:
            writer.writerow([
                "Target_RPS", "Actual_RPS",
                "Mean_Latency", "Stdev_Latency", "Max_Latency",
                "P50_Latency", "P75_Latency", "P90_Latency", "P99_Latency", "P99.9_Latency",
                "Total_Requests", "Socket_Errors", "Non_2xx", "Timeout_Errors", "Error_Rate(%)",
                "Transfer_Rate"
            ])
        
        writer.writerow([
            rps, metrics['actual_rps'],
            metrics['lat_mean'], metrics['lat_stdev'], metrics['lat_max'],
            metrics['lat_p50'], metrics['lat_p75'], metrics['lat_p90'], 
            metrics['lat_p99'], metrics['lat_p999'],
            metrics['total_requests'], metrics['socket_errors'], 
            metrics['non_2xx_responses'], metrics['timeout_errors'],
            metrics['error_rate'],
            metrics['transfer_rate']
        ])

def print_summary(rps: str, metrics: Dict):
    """파싱 결과 요약 출력"""
    print(f"   [Parsed] RPS: {rps}")
    print(f"            Actual: {metrics['actual_rps']:.1f} req/s")
    print(f"            Latency: P50={metrics['lat_p50']}, P99={metrics['lat_p99']}, P99.9={metrics['lat_p999']}")
    
    if metrics['error_rate'] > 0:
        print(f"            ⚠️  Errors: {metrics['error_rate']:.2f}% "
              f"(socket: {metrics['socket_errors']}, non-2xx: {metrics['non_2xx_responses']})")
    else:
        print(f"            ✓ No errors")

# ============================================================
# 메인
# ============================================================
def main():
    if len(sys.argv) < 3:
        print("Usage: python3 parse_wrk.py <RPS> <LOG_FILE>")
        sys.exit(1)
    
    rps = sys.argv[1]
    log_file = sys.argv[2]
    
    if not os.path.exists(log_file):
        print(f"Error: Log file {log_file} not found.")
        sys.exit(1)
    
    try:
        with open(log_file, 'r') as f:
            content = f.read()
        
        metrics = parse_wrk_output(content)
        save_to_csv(rps, metrics)
        print_summary(rps, metrics)
        
    except Exception as e:
        print(f"Error parsing wrk output: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()