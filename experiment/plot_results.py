#!/usr/bin/env python3
"""
plot_results.py v6 (Complete Version)
- CPU/Memory/Network 기본 분석
- Disk I/O 및 System BW 시각화
- Latency 분석
- Saturation 감지
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import os
import sys
import numpy as np
from typing import Optional, Tuple

plt.rcParams['figure.dpi'] = 120
plt.rcParams['font.size'] = 10
sns.set_style("whitegrid")

# ============================================================
# 데이터 로딩
# ============================================================
def load_metrics(filepath: str) -> Optional[pd.DataFrame]:
    """메트릭 CSV 로드 및 정규화"""
    if not os.path.exists(filepath):
        print(f"[Error] Metrics file not found: {filepath}")
        return None
    
    df = pd.read_csv(filepath)
    
    # 컬럼명 정규화
    if 'CPU(m)' in df.columns:
        df.rename(columns={'CPU(m)': 'CPU_Total(m)'}, inplace=True)
    if 'Category' not in df.columns:
        df['Category'] = 'application'
    
    # RPS를 숫자로 변환
    df['RPS'] = pd.to_numeric(df['RPS'], errors='coerce')
    
    print(f"[Info] Loaded {len(df)} rows from {filepath}")
    print(f"       RPS levels: {sorted(df['RPS'].unique())}")
    print(f"       Services: {df['Service'].nunique()}")
    
    return df

def load_latency(filepath: str) -> pd.DataFrame:
    """Latency CSV 로드"""
    if not os.path.exists(filepath):
        print(f"[Warn] Latency file not found: {filepath}")
        return pd.DataFrame()
    
    df = pd.read_csv(filepath)
    
    if 'RPS' in df.columns and 'Target_RPS' not in df.columns:
        df.rename(columns={'RPS': 'Target_RPS'}, inplace=True)
    
    # Latency 문자열을 ms 단위 숫자로 변환
    latency_cols = ['P50_Latency', 'P75_Latency', 'P90_Latency', 'P99_Latency', 'P99.9_Latency']
    for col in latency_cols:
        if col in df.columns:
            df[f'{col}_ms'] = df[col].apply(parse_latency_to_ms)
    
    return df

def parse_latency_to_ms(val) -> float:
    """Latency 문자열을 ms로 변환 (예: '4.32ms' -> 4.32, '1.2s' -> 1200)"""
    if pd.isna(val) or val == 'N/A':
        return np.nan
    
    val = str(val).strip().lower()
    
    if val.endswith('ms'):
        return float(val[:-2])
    elif val.endswith('us'):
        return float(val[:-2]) / 1000
    elif val.endswith('s'):
        return float(val[:-1]) * 1000
    else:
        try:
            return float(val)
        except:
            return np.nan

# ============================================================
# Saturation 감지
# ============================================================
def detect_saturation_point(df: pd.DataFrame, df_lat: pd.DataFrame) -> Optional[int]:
    """
    Saturation point 감지:
    1. Error rate > 1%
    2. Actual RPS < Target RPS * 0.9
    3. P99 latency 급증 (이전 대비 2배 이상)
    """
    if df_lat.empty:
        return None
    
    df_lat = df_lat.sort_values('Target_RPS')
    
    for idx, row in df_lat.iterrows():
        target_rps = row.get('Target_RPS', 0)
        actual_rps = row.get('Actual_RPS', 0)
        error_rate = row.get('Error_Rate(%)', 0)
        
        # 조건 1: Error rate
        if error_rate > 1.0:
            print(f"[Saturation] Detected at {target_rps} RPS (Error rate: {error_rate}%)")
            return int(target_rps)
        
        # 조건 2: Throughput saturation
        if actual_rps > 0 and actual_rps < target_rps * 0.9:
            print(f"[Saturation] Detected at {target_rps} RPS (Throughput: {actual_rps:.0f} < {target_rps * 0.9:.0f})")
            return int(target_rps)
    
    return None

# ============================================================
# 1. Overview 시각화
# ============================================================
def plot_category_overview(df: pd.DataFrame, output_prefix: str = ""):
    """카테고리별 리소스 사용량 개요"""
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    # Application 카테고리만 필터링
    app_df = df[df['Category'] == 'application']
    if app_df.empty:
        print("[Warn] No application data found")
        return
    
    # [1] Total CPU by RPS
    ax = axes[0, 0]
    cpu_by_rps = app_df.groupby('RPS')['CPU_Total(m)'].sum().reset_index()
    ax.bar(cpu_by_rps['RPS'].astype(str), cpu_by_rps['CPU_Total(m)'], color='steelblue', alpha=0.8)
    ax.set_title("1. Total CPU Usage by RPS")
    ax.set_xlabel("Target RPS")
    ax.set_ylabel("CPU (millicores)")
    ax.tick_params(axis='x', rotation=45)
    
    # [2] Total Memory by RPS
    ax = axes[0, 1]
    mem_by_rps = app_df.groupby('RPS')['Memory_WorkingSet(Mi)'].sum().reset_index()
    ax.bar(mem_by_rps['RPS'].astype(str), mem_by_rps['Memory_WorkingSet(Mi)'], color='coral', alpha=0.8)
    ax.set_title("2. Total Memory Usage by RPS")
    ax.set_xlabel("Target RPS")
    ax.set_ylabel("Memory (MiB)")
    ax.tick_params(axis='x', rotation=45)
    
    # [3] Network I/O by RPS
    ax = axes[1, 0]
    net_by_rps = app_df.groupby('RPS')[['Net_RX(KB/s)', 'Net_TX(KB/s)']].sum().reset_index()
    x = np.arange(len(net_by_rps))
    width = 0.35
    ax.bar(x - width/2, net_by_rps['Net_RX(KB/s)'], width, label='RX', color='green', alpha=0.7)
    ax.bar(x + width/2, net_by_rps['Net_TX(KB/s)'], width, label='TX', color='blue', alpha=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(net_by_rps['RPS'].astype(str), rotation=45)
    ax.set_title("3. Network Throughput by RPS")
    ax.set_xlabel("Target RPS")
    ax.set_ylabel("KB/s")
    ax.legend()
    
    # [4] CPU per Service (Top 5 at max RPS)
    ax = axes[1, 1]
    max_rps = app_df['RPS'].max()
    top_services = app_df[app_df['RPS'] == max_rps].groupby('Service')['CPU_Total(m)'].sum().nlargest(5)
    ax.barh(top_services.index, top_services.values, color='purple', alpha=0.7)
    ax.set_title(f"4. Top 5 CPU-intensive Services (at {max_rps} RPS)")
    ax.set_xlabel("CPU (millicores)")
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}overview.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}overview.png")
    plt.close()

# ============================================================
# 2. Service별 상세 분석
# ============================================================
def plot_service_breakdown(df: pd.DataFrame, output_prefix: str = ""):
    """서비스별 CPU 사용량 추이"""
    app_df = df[df['Category'] == 'application']
    if app_df.empty:
        return
    
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    
    # [1] CPU by Service across RPS
    ax = axes[0]
    cpu_pivot = app_df.groupby(['RPS', 'Service'])['CPU_Total(m)'].mean().unstack(fill_value=0)
    
    # Top 8 서비스만 표시
    top_services = cpu_pivot.sum().nlargest(8).index
    cpu_pivot[top_services].plot(kind='area', stacked=True, ax=ax, alpha=0.7)
    ax.set_title("CPU Usage by Service (Top 8)")
    ax.set_xlabel("RPS")
    ax.set_ylabel("CPU (millicores)")
    ax.legend(bbox_to_anchor=(1.02, 1), loc='upper left', fontsize=8)
    
    # [2] Sidecar vs App CPU (Istio 환경에서)
    ax = axes[1]
    if 'CPU_Sidecar(m)' in df.columns and df['CPU_Sidecar(m)'].sum() > 0:
        cpu_comparison = app_df.groupby('RPS')[['CPU_App(m)', 'CPU_Sidecar(m)']].sum().reset_index()
        x = np.arange(len(cpu_comparison))
        width = 0.35
        ax.bar(x - width/2, cpu_comparison['CPU_App(m)'], width, label='Application', color='steelblue')
        ax.bar(x + width/2, cpu_comparison['CPU_Sidecar(m)'], width, label='Istio Sidecar', color='coral')
        ax.set_xticks(x)
        ax.set_xticklabels(cpu_comparison['RPS'].astype(str), rotation=45)
        ax.set_title("Application vs Sidecar CPU")
        ax.set_ylabel("CPU (millicores)")
        ax.legend()
    else:
        ax.text(0.5, 0.5, "No Sidecar Data\n(Non-Istio Environment)", 
                ha='center', va='center', transform=ax.transAxes, fontsize=12)
        ax.set_title("Sidecar CPU Analysis")
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}service_breakdown.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}service_breakdown.png")
    plt.close()

# ============================================================
# 3. Latency 분석
# ============================================================
def plot_latency_analysis(df_lat: pd.DataFrame, saturation_rps: Optional[int], output_prefix: str = ""):
    """Latency percentile 분석"""
    if df_lat.empty:
        print("[Warn] No latency data to plot")
        return
    
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    
    # RPS별 평균 계산 (반복 실험 대응)
    lat_cols = ['P50_Latency_ms', 'P75_Latency_ms', 'P90_Latency_ms', 'P99_Latency_ms']
    available_cols = [c for c in lat_cols if c in df_lat.columns]
    
    if not available_cols:
        print("[Warn] No parsed latency columns found")
        return
    
    lat_avg = df_lat.groupby('Target_RPS')[available_cols + ['Actual_RPS', 'Error_Rate(%)']].mean().reset_index()
    lat_avg = lat_avg.sort_values('Target_RPS')
    
    # [1] Latency Percentiles
    ax = axes[0]
    colors = {'P50': 'green', 'P75': 'blue', 'P90': 'orange', 'P99': 'red'}
    
    for col in available_cols:
        percentile = col.split('_')[0]
        ax.plot(lat_avg['Target_RPS'], lat_avg[col], marker='o', 
                label=percentile, color=colors.get(percentile, 'gray'), linewidth=2)
    
    if saturation_rps:
        ax.axvline(x=saturation_rps, color='red', linestyle='--', alpha=0.7, label=f'Saturation ({saturation_rps})')
    
    ax.set_title("Latency Percentiles vs RPS")
    ax.set_xlabel("Target RPS")
    ax.set_ylabel("Latency (ms)")
    ax.legend()
    ax.set_yscale('log')
    ax.grid(True, alpha=0.3)
    
    # [2] Throughput & Error Rate
    ax = axes[1]
    ax2 = ax.twinx()
    
    ax.plot(lat_avg['Target_RPS'], lat_avg['Actual_RPS'], 'b-o', label='Actual RPS', linewidth=2)
    ax.plot(lat_avg['Target_RPS'], lat_avg['Target_RPS'], 'g--', label='Target RPS', alpha=0.5)
    ax.set_xlabel("Target RPS")
    ax.set_ylabel("Throughput (req/s)", color='blue')
    ax.tick_params(axis='y', labelcolor='blue')
    
    if 'Error_Rate(%)' in lat_avg.columns:
        ax2.plot(lat_avg['Target_RPS'], lat_avg['Error_Rate(%)'], 'r-s', label='Error Rate', linewidth=2)
        ax2.set_ylabel("Error Rate (%)", color='red')
        ax2.tick_params(axis='y', labelcolor='red')
    
    ax.set_title("Throughput vs Target RPS")
    ax.legend(loc='upper left')
    ax2.legend(loc='upper right')
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}latency_analysis.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}latency_analysis.png")
    plt.close()

# ============================================================
# 4. Disk I/O & System BW (xTella)
# ============================================================
def plot_xtella_metrics(df: pd.DataFrame, output_prefix: str = ""):
    """Disk I/O 및 System BW 분석 (PCM 포함)"""
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    app_df = df[df['Category'] == 'application']
    if app_df.empty:
        print("[Warn] No application data for xTella metrics")
        return

    # [1] Disk Read by Service (Top 5)./
    ax = axes[0, 0]
    if 'Disk_Read(KB/s)' in app_df.columns and app_df['Disk_Read(KB/s)'].sum() > 0:
        disk_read = app_df.groupby(['RPS', 'Service'])['Disk_Read(KB/s)'].mean().reset_index()
        top_read = disk_read.groupby('Service')['Disk_Read(KB/s)'].mean().nlargest(5).index
        
        for svc in top_read:
            data = disk_read[disk_read['Service'] == svc].sort_values('RPS')
            ax.plot(data['RPS'], data['Disk_Read(KB/s)'], marker='o', label=svc, linewidth=2)
        
        ax.set_title("1. Disk Read (Top 5 Services)")
        ax.set_xlabel("RPS")
        ax.set_ylabel("KB/s")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
    else:
        ax.text(0.5, 0.5, "No Disk Read Data\n(Prometheus not connected?)", 
                ha='center', va='center', transform=ax.transAxes)
        ax.set_title("1. Disk Read")

    # [2] Disk Write by Service (Top 5)
    ax = axes[0, 1]
    if 'Disk_Write(KB/s)' in app_df.columns and app_df['Disk_Write(KB/s)'].sum() > 0:
        disk_write = app_df.groupby(['RPS', 'Service'])['Disk_Write(KB/s)'].mean().reset_index()
        top_write = disk_write.groupby('Service')['Disk_Write(KB/s)'].mean().nlargest(5).index
        
        for svc in top_write:
            data = disk_write[disk_write['Service'] == svc].sort_values('RPS')
            ax.plot(data['RPS'], data['Disk_Write(KB/s)'], marker='s', label=svc, linewidth=2)
        
        ax.set_title("2. Disk Write (Top 5 Services)")
        ax.set_xlabel("RPS")
        ax.set_ylabel("KB/s")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
    else:
        ax.text(0.5, 0.5, "No Disk Write Data\n(Prometheus not connected?)", 
                ha='center', va='center', transform=ax.transAxes)
        ax.set_title("2. Disk Write")

    # [3] System Memory Bandwidth (PCM)
    ax = axes[1, 0]
    if 'System_Mem_BW' in df.columns and df['System_Mem_BW'].sum() > 0:
        sys_mem = df.groupby('RPS')['System_Mem_BW'].mean().reset_index().sort_values('RPS')
        ax.plot(sys_mem['RPS'], sys_mem['System_Mem_BW'], color='purple', marker='o', linewidth=2)
        ax.fill_between(sys_mem['RPS'], sys_mem['System_Mem_BW'], alpha=0.3, color='purple')
        ax.set_title("3. System Memory Bandwidth (PCM)")
        ax.set_xlabel("RPS")
        ax.set_ylabel("Bandwidth (GB/s)")
        ax.grid(True, alpha=0.3)
    else:
        ax.text(0.5, 0.5, "No PCM Data\n(PCM not installed or sudo required)", 
                ha='center', va='center', transform=ax.transAxes, fontsize=11)
        ax.set_title("3. System Memory Bandwidth")

    # [4] System LLC Metric (PCM)
    ax = axes[1, 1]
    if 'System_LLC_Metric' in df.columns and df['System_LLC_Metric'].sum() > 0:
        sys_llc = df.groupby('RPS')['System_LLC_Metric'].mean().reset_index().sort_values('RPS')
        ax.plot(sys_llc['RPS'], sys_llc['System_LLC_Metric'], color='brown', marker='x', linewidth=2)
        ax.fill_between(sys_llc['RPS'], sys_llc['System_LLC_Metric'], alpha=0.3, color='brown')
        ax.set_title("4. LLC Miss Rate (PCM)")
        ax.set_xlabel("RPS")
        ax.set_ylabel("L3 Cache Miss Rate")
        ax.grid(True, alpha=0.3)
    else:
        ax.text(0.5, 0.5, "No PCM Data\n(PCM not installed or sudo required)", 
                ha='center', va='center', transform=ax.transAxes, fontsize=11)
        ax.set_title("4. LLC Miss Rate")

    plt.tight_layout()
    plt.savefig(f"{output_prefix}xtella_io_analysis.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}xtella_io_analysis.png")
    plt.close()

# ============================================================
# 5. CPU Efficiency 분석
# ============================================================
def plot_cpu_efficiency(df: pd.DataFrame, df_lat: pd.DataFrame, output_prefix: str = ""):
    """CPU 효율성 분석 (CPU per RPS)"""
    if df_lat.empty:
        return
    
    app_df = df[df['Category'] == 'application']
    if app_df.empty:
        return
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # RPS별 총 CPU와 실제 처리량
    cpu_by_rps = app_df.groupby('RPS')['CPU_Total(m)'].sum().reset_index()
    lat_avg = df_lat.groupby('Target_RPS')['Actual_RPS'].mean().reset_index()
    
    merged = cpu_by_rps.merge(lat_avg, left_on='RPS', right_on='Target_RPS', how='inner')
    merged['CPU_per_RPS'] = merged['CPU_Total(m)'] / merged['Actual_RPS']
    merged = merged.sort_values('RPS')
    
    ax.plot(merged['RPS'], merged['CPU_per_RPS'], 'b-o', linewidth=2, markersize=8)
    ax.set_title("CPU Efficiency: Millicores per Request")
    ax.set_xlabel("Target RPS")
    ax.set_ylabel("CPU (millicores) / Actual RPS")
    ax.grid(True, alpha=0.3)
    
    # 최적점 표시
    min_idx = merged['CPU_per_RPS'].idxmin()
    opt_rps = merged.loc[min_idx, 'RPS']
    opt_val = merged.loc[min_idx, 'CPU_per_RPS']
    ax.annotate(f'Optimal: {opt_rps} RPS\n({opt_val:.2f} mCPU/req)', 
                xy=(opt_rps, opt_val), xytext=(opt_rps + 100, opt_val + 0.5),
                arrowprops=dict(arrowstyle='->', color='red'),
                fontsize=10, color='red')
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}cpu_efficiency.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}cpu_efficiency.png")
    plt.close()

# ============================================================
# Main
# ============================================================
def main():
    # 인자 파싱
    metrics_file = sys.argv[1] if len(sys.argv) > 1 else "k8s_full_metrics.csv"
    latency_file = sys.argv[2] if len(sys.argv) > 2 else "latency_stats.csv"
    output_prefix = sys.argv[3] if len(sys.argv) > 3 else ""
    
    print("=" * 60)
    print("DeathStarBench Results Visualization")
    print("=" * 60)
    
    # 데이터 로드
    df = load_metrics(metrics_file)
    df_lat = load_latency(latency_file)
    
    if df is None:
        print("[Error] Cannot proceed without metrics data")
        sys.exit(1)
    
    # Saturation 감지
    saturation_rps = detect_saturation_point(df, df_lat)
    
    # 시각화 생성
    print("\nGenerating visualizations...")
    
    print("  [1/5] Category Overview...")
    plot_category_overview(df, output_prefix)
    
    print("  [2/5] Service Breakdown...")
    plot_service_breakdown(df, output_prefix)
    
    print("  [3/5] Latency Analysis...")
    plot_latency_analysis(df_lat, saturation_rps, output_prefix)
    
    print("  [4/5] xTella I/O Analysis...")
    plot_xtella_metrics(df, output_prefix)
    
    print("  [5/5] CPU Efficiency...")
    plot_cpu_efficiency(df, df_lat, output_prefix)
    
    print("\n" + "=" * 60)
    print("Visualization complete!")
    if saturation_rps:
        print(f"⚠️  Saturation detected at {saturation_rps} RPS")
    print("=" * 60)

if __name__ == "__main__":
    main()
