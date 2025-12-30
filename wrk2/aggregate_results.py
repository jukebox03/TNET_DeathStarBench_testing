#!/usr/bin/env python3
"""
실험 결과 집계 스크립트 (aggregate_stats.py)
- 반복 실험(Repetitions) 결과를 평균/표준편차로 요약
"""

import pandas as pd
import os
import sys

def aggregate_csv(metrics_file="k8s_full_metrics.csv", latency_file="latency_stats.csv"):
    # 1. 메트릭 집계 (CPU, Memory, Network)
    if os.path.exists(metrics_file):
        try:
            df = pd.read_csv(metrics_file)
            
            # 숫자형 컬럼만 선택하여 집계 (에러 방지)
            numeric_cols = ['CPU_Total(m)', 'Memory_WorkingSet(Mi)', 'Net_RX(KB/s)', 'Net_TX(KB/s)']
            # 실제 데이터에 있는 컬럼만 필터링
            target_cols = [c for c in numeric_cols if c in df.columns]
            
            # RPS별, Service별 평균 및 표준편차 계산
            summary = df.groupby(['RPS', 'Service'])[target_cols].agg(['mean', 'std']).round(2)
            
            summary.to_csv("metrics_summary.csv")
            print(f"   [Aggregate] Metrics summary saved to metrics_summary.csv")
        except Exception as e:
            print(f"   [Error] Failed to aggregate metrics: {e}")

    # 2. Latency 집계
    if os.path.exists(latency_file):
        try:
            df_lat = pd.read_csv(latency_file)

            cols_to_fix = ['P50_Latency', 'P99_Latency', 'Actual_RPS'] # 에러 나는 컬럼들
            for col in cols_to_fix:
                if col in df_lat.columns:
                    # 1. 혹시 단위(ms)가 있다면 제거 (간단 버전)
                    if df_lat[col].dtype == 'object':
                        df_lat[col] = df_lat[col].astype(str).str.replace('ms', '', regex=False).str.replace('us', '', regex=False)
                    
                    # 2. 숫자로 변환 (변환 안 되는 'N/A' 등은 NaN으로 처리)
                    df_lat[col] = pd.to_numeric(df_lat[col], errors='coerce')
            
            # 주요 컬럼 집계
            cols = ['Actual_RPS', 'Error_Rate(%)', 'P50_Latency', 'P99_Latency']
            # 존재하는 컬럼만 선택 (호환성)
            target_cols = [c for c in cols if c in df_lat.columns]
            
            # RPS별 평균
            lat_summary = df_lat.groupby('Target_RPS')[target_cols].agg(['mean', 'std']).round(2)
            
            lat_summary.to_csv("latency_summary.csv")
            print(f"   [Aggregate] Latency summary saved to latency_summary.csv")
        except Exception as e:
            print(f"   [Error] Failed to aggregate latency: {e}")

if __name__ == "__main__":
    # 인자로 파일명을 받을 수도 있게 확장 가능
    m_file = sys.argv[1] if len(sys.argv) > 1 else "k8s_full_metrics.csv"
    l_file = sys.argv[2] if len(sys.argv) > 2 else "latency_stats.csv"
    
    aggregate_csv(m_file, l_file)