#!/usr/bin/env python3
"""
compare_istio.py v4 (Complete Version)
- Istio 유무 환경 비교
- CPU/Memory/Network/Latency 오버헤드 분석
- Disk I/O 및 System BW 비교
- Sidecar 비용 분석
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import os
import sys
import glob
from typing import Optional, Tuple, Dict

plt.rcParams['figure.dpi'] = 120
plt.rcParams['font.size'] = 10
sns.set_style("whitegrid")

# ============================================================
# 데이터 로딩
# ============================================================
def find_latest_result(base_path: str, pattern: str) -> Optional[str]:
    """최신 결과 디렉토리 찾기"""
    matches = glob.glob(os.path.join(base_path, pattern))
    if not matches:
        return None
    return sorted(matches)[-1]

def load_metrics(filepath: str) -> Optional[pd.DataFrame]:
    """메트릭 CSV 로드"""
    if not os.path.exists(filepath):
        return None
    
    df = pd.read_csv(filepath)
    
    if 'CPU(m)' in df.columns:
        df.rename(columns={'CPU(m)': 'CPU_Total(m)'}, inplace=True)
    if 'Category' not in df.columns:
        df['Category'] = 'application'
    
    df['RPS'] = pd.to_numeric(df['RPS'], errors='coerce')
    return df

def load_latency(filepath: str) -> pd.DataFrame:
    """Latency CSV 로드"""
    if not os.path.exists(filepath):
        return pd.DataFrame()
    
    df = pd.read_csv(filepath)
    
    if 'RPS' in df.columns and 'Target_RPS' not in df.columns:
        df.rename(columns={'RPS': 'Target_RPS'}, inplace=True)
    
    return df

def parse_latency_to_ms(val) -> float:
    """Latency 문자열을 ms로 변환"""
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
# 오버헤드 계산
# ============================================================
def calculate_overhead(df_no: pd.DataFrame, df_with: pd.DataFrame) -> pd.DataFrame:
    """Istio 오버헤드 계산 (RPS별)"""
    
    app_no = df_no[df_no['Category'] == 'application']
    app_with = df_with[df_with['Category'] == 'application']
    
    # RPS별 집계
    metrics = ['CPU_Total(m)', 'Memory_WorkingSet(Mi)', 'Net_RX(KB/s)', 'Net_TX(KB/s)']
    
    agg_no = app_no.groupby('RPS')[metrics].sum().reset_index()
    agg_with = app_with.groupby('RPS')[metrics].sum().reset_index()
    
    # 공통 RPS만 비교
    common_rps = set(agg_no['RPS']) & set(agg_with['RPS'])
    
    results = []
    for rps in sorted(common_rps):
        row_no = agg_no[agg_no['RPS'] == rps].iloc[0]
        row_with = agg_with[agg_with['RPS'] == rps].iloc[0]
        
        result = {'RPS': rps}
        
        for metric in metrics:
            val_no = row_no[metric]
            val_with = row_with[metric]
            
            result[f'{metric}_NoIstio'] = val_no
            result[f'{metric}_WithIstio'] = val_with
            
            if val_no > 0:
                overhead_pct = (val_with - val_no) / val_no * 100
            else:
                overhead_pct = 0
            
            result[f'{metric}_Overhead%'] = round(overhead_pct, 2)
        
        results.append(result)
    
    return pd.DataFrame(results)

def calculate_sidecar_cost(df_with: pd.DataFrame) -> pd.DataFrame:
    """Sidecar CPU/Memory 비용 분석"""
    
    app_with = df_with[df_with['Category'] == 'application']
    
    if 'CPU_Sidecar(m)' not in app_with.columns:
        return pd.DataFrame()
    
    # RPS별 sidecar 비용
    sidecar_stats = app_with.groupby('RPS').agg({
        'CPU_Total(m)': 'sum',
        'CPU_App(m)': 'sum',
        'CPU_Sidecar(m)': 'sum'
    }).reset_index()
    
    sidecar_stats['Sidecar_CPU_Ratio%'] = (
        sidecar_stats['CPU_Sidecar(m)'] / sidecar_stats['CPU_Total(m)'] * 100
    ).round(2)
    
    return sidecar_stats

# ============================================================
# 시각화: 메인 비교
# ============================================================
def plot_main_comparison(df_no: pd.DataFrame, df_with: pd.DataFrame, 
                         overhead_df: pd.DataFrame, output_prefix: str = ""):
    """CPU/Memory/Network 주요 비교"""
    
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    app_no = df_no[df_no['Category'] == 'application']
    app_with = df_with[df_with['Category'] == 'application']
    
    # [1] Total CPU Comparison
    ax = axes[0, 0]
    cpu_no = app_no.groupby('RPS')['CPU_Total(m)'].sum().reset_index()
    cpu_no['Env'] = 'No Istio'
    cpu_with = app_with.groupby('RPS')['CPU_Total(m)'].sum().reset_index()
    cpu_with['Env'] = 'With Istio'
    
    combined = pd.concat([cpu_no, cpu_with])
    
    x = np.arange(len(cpu_no))
    width = 0.35
    ax.bar(x - width/2, cpu_no['CPU_Total(m)'], width, label='No Istio', color='#2ecc71')
    ax.bar(x + width/2, cpu_with['CPU_Total(m)'], width, label='With Istio', color='#e74c3c')
    ax.set_xticks(x)
    ax.set_xticklabels(cpu_no['RPS'].astype(int), rotation=45)
    ax.set_title("1. Total CPU Usage")
    ax.set_xlabel("RPS")
    ax.set_ylabel("CPU (millicores)")
    ax.legend()
    
    # 오버헤드 % 표시
    for i, rps in enumerate(cpu_no['RPS']):
        if rps in overhead_df['RPS'].values:
            oh = overhead_df[overhead_df['RPS'] == rps]['CPU_Total(m)_Overhead%'].values[0]
            max_val = max(cpu_no.iloc[i]['CPU_Total(m)'], cpu_with.iloc[i]['CPU_Total(m)'])
            ax.annotate(f'+{oh:.0f}%', xy=(i, max_val), ha='center', va='bottom', 
                       fontsize=9, color='red', fontweight='bold')
    
    # [2] Total Memory Comparison
    ax = axes[0, 1]
    mem_no = app_no.groupby('RPS')['Memory_WorkingSet(Mi)'].sum().reset_index()
    mem_with = app_with.groupby('RPS')['Memory_WorkingSet(Mi)'].sum().reset_index()
    
    ax.bar(x - width/2, mem_no['Memory_WorkingSet(Mi)'], width, label='No Istio', color='#2ecc71')
    ax.bar(x + width/2, mem_with['Memory_WorkingSet(Mi)'], width, label='With Istio', color='#e74c3c')
    ax.set_xticks(x)
    ax.set_xticklabels(mem_no['RPS'].astype(int), rotation=45)
    ax.set_title("2. Total Memory Usage")
    ax.set_xlabel("RPS")
    ax.set_ylabel("Memory (MiB)")
    ax.legend()
    
    # [3] Network Comparison
    ax = axes[1, 0]
    net_no = app_no.groupby('RPS')[['Net_RX(KB/s)', 'Net_TX(KB/s)']].sum()
    net_no['Total'] = net_no['Net_RX(KB/s)'] + net_no['Net_TX(KB/s)']
    net_no = net_no.reset_index()
    
    net_with = app_with.groupby('RPS')[['Net_RX(KB/s)', 'Net_TX(KB/s)']].sum()
    net_with['Total'] = net_with['Net_RX(KB/s)'] + net_with['Net_TX(KB/s)']
    net_with = net_with.reset_index()
    
    ax.bar(x - width/2, net_no['Total'], width, label='No Istio', color='#2ecc71')
    ax.bar(x + width/2, net_with['Total'], width, label='With Istio', color='#e74c3c')
    ax.set_xticks(x)
    ax.set_xticklabels(net_no['RPS'].astype(int), rotation=45)
    ax.set_title("3. Total Network I/O (RX + TX)")
    ax.set_xlabel("RPS")
    ax.set_ylabel("KB/s")
    ax.legend()
    
    # [4] Overhead Summary
    ax = axes[1, 1]
    if not overhead_df.empty:
        overhead_cols = [c for c in overhead_df.columns if c.endswith('_Overhead%')]
        
        avg_overhead = {}
        for col in overhead_cols:
            metric_name = col.replace('_Overhead%', '').replace('(m)', '').replace('(Mi)', '').replace('(KB/s)', '')
            avg_overhead[metric_name] = overhead_df[col].mean()
        
        bars = ax.bar(avg_overhead.keys(), avg_overhead.values(), color=['steelblue', 'coral', 'green', 'purple'])
        ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
        ax.set_title("4. Average Istio Overhead (%)")
        ax.set_ylabel("Overhead (%)")
        ax.tick_params(axis='x', rotation=30)
        
        # 값 표시
        for bar, val in zip(bars, avg_overhead.values()):
            ax.annotate(f'{val:.1f}%', xy=(bar.get_x() + bar.get_width()/2, bar.get_height()),
                       ha='center', va='bottom', fontsize=10, fontweight='bold')
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}main_comparison.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}main_comparison.png")
    plt.close()

# ============================================================
# 시각화: Sidecar 분석
# ============================================================
def plot_sidecar_analysis(df_with: pd.DataFrame, sidecar_df: pd.DataFrame, output_prefix: str = ""):
    """Sidecar CPU 비용 분석"""
    
    if sidecar_df.empty:
        print("[Warn] No sidecar data available")
        return
    
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    
    # [1] App vs Sidecar CPU
    ax = axes[0]
    sidecar_df = sidecar_df.sort_values('RPS')
    x = np.arange(len(sidecar_df))
    width = 0.35
    
    ax.bar(x - width/2, sidecar_df['CPU_App(m)'], width, label='Application', color='steelblue')
    ax.bar(x + width/2, sidecar_df['CPU_Sidecar(m)'], width, label='Istio Sidecar', color='coral')
    ax.set_xticks(x)
    ax.set_xticklabels(sidecar_df['RPS'].astype(int), rotation=45)
    ax.set_title("Application vs Sidecar CPU")
    ax.set_xlabel("RPS")
    ax.set_ylabel("CPU (millicores)")
    ax.legend()
    
    # [2] Sidecar CPU Ratio
    ax = axes[1]
    ax.plot(sidecar_df['RPS'], sidecar_df['Sidecar_CPU_Ratio%'], 'r-o', linewidth=2, markersize=8)
    ax.fill_between(sidecar_df['RPS'], sidecar_df['Sidecar_CPU_Ratio%'], alpha=0.3, color='red')
    ax.set_title("Sidecar CPU as % of Total")
    ax.set_xlabel("RPS")
    ax.set_ylabel("Sidecar CPU Ratio (%)")
    ax.grid(True, alpha=0.3)
    
    # 평균선 표시
    avg_ratio = sidecar_df['Sidecar_CPU_Ratio%'].mean()
    ax.axhline(y=avg_ratio, color='blue', linestyle='--', label=f'Average: {avg_ratio:.1f}%')
    ax.legend()
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}sidecar_analysis.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}sidecar_analysis.png")
    plt.close()

# ============================================================
# 시각화: Latency 비교
# ============================================================
def plot_latency_comparison(lat_no: pd.DataFrame, lat_with: pd.DataFrame, output_prefix: str = ""):
    """Latency 비교"""
    
    if lat_no.empty or lat_with.empty:
        print("[Warn] Latency data missing for comparison")
        return
    
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    
    # P99 Latency 파싱
    for df in [lat_no, lat_with]:
        if 'P99_Latency' in df.columns:
            df['P99_ms'] = df['P99_Latency'].apply(parse_latency_to_ms)
    
    lat_no_avg = lat_no.groupby('Target_RPS')[['Actual_RPS', 'P99_ms', 'Error_Rate(%)']].mean().reset_index()
    lat_with_avg = lat_with.groupby('Target_RPS')[['Actual_RPS', 'P99_ms', 'Error_Rate(%)']].mean().reset_index()
    
    # [1] P99 Latency Comparison
    ax = axes[0]
    if 'P99_ms' in lat_no_avg.columns:
        ax.plot(lat_no_avg['Target_RPS'], lat_no_avg['P99_ms'], 'g-o', label='No Istio', linewidth=2)
        ax.plot(lat_with_avg['Target_RPS'], lat_with_avg['P99_ms'], 'r-s', label='With Istio', linewidth=2)
        ax.set_title("P99 Latency Comparison")
        ax.set_xlabel("Target RPS")
        ax.set_ylabel("P99 Latency (ms)")
        ax.set_yscale('log')
        ax.legend()
        ax.grid(True, alpha=0.3)
    
    # [2] Throughput Comparison
    ax = axes[1]
    ax.plot(lat_no_avg['Target_RPS'], lat_no_avg['Actual_RPS'], 'g-o', label='No Istio', linewidth=2)
    ax.plot(lat_with_avg['Target_RPS'], lat_with_avg['Actual_RPS'], 'r-s', label='With Istio', linewidth=2)
    ax.plot(lat_no_avg['Target_RPS'], lat_no_avg['Target_RPS'], 'k--', label='Ideal', alpha=0.5)
    ax.set_title("Actual Throughput Comparison")
    ax.set_xlabel("Target RPS")
    ax.set_ylabel("Actual RPS")
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}latency_comparison.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}latency_comparison.png")
    plt.close()

# ============================================================
# 시각화: Disk & System BW 비교
# ============================================================
def plot_io_comparison(df_no: pd.DataFrame, df_with: pd.DataFrame, output_prefix: str = ""):
    """Disk 및 System BW 비교"""
    
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    
    # [1] Total Disk I/O Comparison
    ax = axes[0]
    
    has_disk = ('Disk_Read(KB/s)' in df_no.columns and 
                df_no['Disk_Read(KB/s)'].sum() + df_with['Disk_Read(KB/s)'].sum() > 0)
    
    if has_disk:
        d_no = df_no.groupby('RPS')[['Disk_Read(KB/s)', 'Disk_Write(KB/s)']].sum()
        d_no['Total_Disk'] = d_no['Disk_Read(KB/s)'] + d_no['Disk_Write(KB/s)']
        d_no = d_no.reset_index()
        d_no['Env'] = 'No Istio'
        
        d_with = df_with.groupby('RPS')[['Disk_Read(KB/s)', 'Disk_Write(KB/s)']].sum()
        d_with['Total_Disk'] = d_with['Disk_Read(KB/s)'] + d_with['Disk_Write(KB/s)']
        d_with = d_with.reset_index()
        d_with['Env'] = 'With Istio'
        
        combined = pd.concat([d_no[['RPS', 'Total_Disk', 'Env']], 
                              d_with[['RPS', 'Total_Disk', 'Env']]])
        
        sns.barplot(data=combined, x='RPS', y='Total_Disk', hue='Env', ax=ax,
                   palette={'No Istio': '#2ecc71', 'With Istio': '#e74c3c'})
        ax.set_title("Total Disk I/O (Read + Write)")
        ax.set_ylabel("KB/s")
    else:
        ax.text(0.5, 0.5, "No Disk Data Available", ha='center', va='center', 
                transform=ax.transAxes, fontsize=12)
        ax.set_title("Disk I/O Comparison")
    
    # [2] System Memory BW Comparison (PCM)
    ax = axes[1]
    
    has_pcm = ('System_Mem_BW' in df_no.columns and 
               df_no['System_Mem_BW'].sum() + df_with['System_Mem_BW'].sum() > 0)
    
    if has_pcm:
        m_no = df_no.groupby('RPS')['System_Mem_BW'].mean().reset_index()
        m_with = df_with.groupby('RPS')['System_Mem_BW'].mean().reset_index()
        
        ax.plot(m_no['RPS'], m_no['System_Mem_BW'], 'g-o', label='No Istio', linewidth=2)
        ax.plot(m_with['RPS'], m_with['System_Mem_BW'], 'r-s', label='With Istio', linewidth=2)
        ax.set_title("System Memory Bandwidth (PCM)")
        ax.set_xlabel("RPS")
        ax.set_ylabel("Bandwidth (GB/s)")
        ax.legend()
        ax.grid(True, alpha=0.3)
    else:
        ax.text(0.5, 0.5, "No PCM Data Available", ha='center', va='center', 
                transform=ax.transAxes, fontsize=12)
        ax.set_title("System Memory Bandwidth")

    plt.tight_layout()
    plt.savefig(f"{output_prefix}io_system_comparison.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}io_system_comparison.png")
    plt.close()

# ============================================================
# 결과 요약 출력
# ============================================================
def print_summary(overhead_df: pd.DataFrame, sidecar_df: pd.DataFrame):
    """콘솔에 요약 출력"""
    
    print("\n" + "=" * 60)
    print("ISTIO OVERHEAD SUMMARY")
    print("=" * 60)
    
    if not overhead_df.empty:
        print("\n[Resource Overhead by RPS]")
        print("-" * 50)
        
        for _, row in overhead_df.iterrows():
            print(f"\nRPS {int(row['RPS'])}:")
            print(f"  CPU:     +{row['CPU_Total(m)_Overhead%']:.1f}%")
            print(f"  Memory:  +{row['Memory_WorkingSet(Mi)_Overhead%']:.1f}%")
            print(f"  Net RX:  +{row['Net_RX(KB/s)_Overhead%']:.1f}%")
            print(f"  Net TX:  +{row['Net_TX(KB/s)_Overhead%']:.1f}%")
        
        print("\n[Average Overhead]")
        print("-" * 50)
        for col in [c for c in overhead_df.columns if c.endswith('_Overhead%')]:
            avg = overhead_df[col].mean()
            metric = col.replace('_Overhead%', '')
            print(f"  {metric}: +{avg:.1f}%")
    
    if not sidecar_df.empty:
        print("\n[Sidecar CPU Cost]")
        print("-" * 50)
        avg_ratio = sidecar_df['Sidecar_CPU_Ratio%'].mean()
        print(f"  Average Sidecar CPU Ratio: {avg_ratio:.1f}%")
        print(f"  (Sidecar consumes ~{avg_ratio:.0f}% of total pod CPU)")
    
    print("\n" + "=" * 60)

# ============================================================
# Main
# ============================================================
def main():
    print("=" * 60)
    print("Istio Overhead Comparison Tool")
    print("=" * 60)
    
    # 인자 파싱
    if len(sys.argv) < 3:
        print("\nUsage: python3 compare_istio.py <no_istio_dir> <with_istio_dir> [output_prefix]")
        print("\nExample:")
        print("  python3 compare_istio.py results/no_istio_20240101 results/with_istio_20240101")
        print("\nOr use pattern matching:")
        print("  python3 compare_istio.py results/no_istio_* results/with_istio_*")
        sys.exit(1)
    
    no_istio_dir = sys.argv[1]
    with_istio_dir = sys.argv[2]
    output_prefix = sys.argv[3] if len(sys.argv) > 3 else "compare_"
    
    # 파일 경로 결정
    if os.path.isdir(no_istio_dir):
        metrics_no = os.path.join(no_istio_dir, "k8s_full_metrics.csv")
        latency_no = os.path.join(no_istio_dir, "latency_stats.csv")
    else:
        metrics_no = no_istio_dir
        latency_no = no_istio_dir.replace("k8s_full_metrics", "latency_stats")
    
    if os.path.isdir(with_istio_dir):
        metrics_with = os.path.join(with_istio_dir, "k8s_full_metrics.csv")
        latency_with = os.path.join(with_istio_dir, "latency_stats.csv")
    else:
        metrics_with = with_istio_dir
        latency_with = with_istio_dir.replace("k8s_full_metrics", "latency_stats")
    
    print(f"\nLoading data...")
    print(f"  No Istio:   {metrics_no}")
    print(f"  With Istio: {metrics_with}")
    
    # 데이터 로드
    df_no = load_metrics(metrics_no)
    df_with = load_metrics(metrics_with)
    lat_no = load_latency(latency_no)
    lat_with = load_latency(latency_with)
    
    if df_no is None or df_with is None:
        print("[Error] Cannot load metrics data")
        sys.exit(1)
    
    print(f"\nNo Istio:   {len(df_no)} rows, RPS: {sorted(df_no['RPS'].unique())}")
    print(f"With Istio: {len(df_with)} rows, RPS: {sorted(df_with['RPS'].unique())}")
    
    # 오버헤드 계산
    overhead_df = calculate_overhead(df_no, df_with)
    sidecar_df = calculate_sidecar_cost(df_with)
    
    # 시각화 생성
    print("\nGenerating comparison plots...")
    
    print("  [1/4] Main Comparison...")
    plot_main_comparison(df_no, df_with, overhead_df, output_prefix)
    
    print("  [2/4] Sidecar Analysis...")
    plot_sidecar_analysis(df_with, sidecar_df, output_prefix)
    
    print("  [3/4] Latency Comparison...")
    plot_latency_comparison(lat_no, lat_with, output_prefix)
    
    print("  [4/4] I/O & System Comparison...")
    plot_io_comparison(df_no, df_with, output_prefix)
    
    # 요약 출력
    print_summary(overhead_df, sidecar_df)
    
    # 오버헤드 CSV 저장
    if not overhead_df.empty:
        overhead_df.to_csv(f"{output_prefix}overhead_summary.csv", index=False)
        print(f"\n✓ Saved: {output_prefix}overhead_summary.csv")
    
    print("\nComparison complete!")

if __name__ == "__main__":
    main()
