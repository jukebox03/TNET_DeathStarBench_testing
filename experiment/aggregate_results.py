#!/usr/bin/env python3
"""
aggregate_results.py
- 추가된 xTella 메트릭(Disk, System BW) 집계 지원
"""

import pandas as pd
import os
import sys

def aggregate_csv(metrics_file="k8s_full_metrics.csv", latency_file="latency_stats.csv"):
    # 1. 메트릭 집계
    if os.path.exists(metrics_file):
        try:
            df = pd.read_csv(metrics_file)
            
            # 집계할 숫자형 컬럼 확장
            numeric_cols = [
                'CPU_Total(m)', 'Memory_WorkingSet(Mi)', 
                'Net_RX(KB/s)', 'Net_TX(KB/s)',
                'Disk_Read(KB/s)', 'Disk_Write(KB/s)',
                'System_Mem_BW', 'System_LLC_Metric'
            ]
            
            target_cols = [c for c in numeric_cols if c in df.columns]
            
            # RPS별, Service별 평균/표준편차
            # System Metrics는 Service별로 동일하므로 groupby에 포함되어도 무방
            summary = df.groupby(['RPS', 'Service'])[target_cols].agg(['mean', 'std']).round(2)
            
            summary.to_csv("metrics_summary.csv")
            print(f"   [Aggregate] Metrics summary saved.")
        except Exception as e:
            print(f"   [Error] Metrics aggregation failed: {e}")

    # 2. Latency 집계 (기존과 동일)
    if os.path.exists(latency_file):
        try:
            df_lat = pd.read_csv(latency_file)
            cols = ['Actual_RPS', 'Error_Rate(%)', 'P50_Latency', 'P99_Latency']
            target_cols = [c for c in cols if c in df_lat.columns]
            
            lat_summary = df_lat.groupby('Target_RPS')[target_cols].agg(['mean', 'std']).round(2)
            lat_summary.to_csv("latency_summary.csv")
            print(f"   [Aggregate] Latency summary saved.")
        except Exception as e:
            print(f"   [Error] Latency aggregation failed: {e}")

if __name__ == "__main__":
    aggregate_csv()