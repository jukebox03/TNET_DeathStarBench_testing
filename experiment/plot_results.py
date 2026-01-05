#!/usr/bin/env python3
"""
plot_results.py v9.0 (Bugfix Edition)
- Fix: Latency Plot empty issue (matches 'P50_Latency' column name)
- Features: CPU Core Heatmap, Disk Latency, Service Breakdown
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import os
import sys
import numpy as np
from typing import Optional

plt.rcParams['figure.dpi'] = 120
plt.rcParams['font.size'] = 10
sns.set_style("whitegrid")

# ============================================================
# 데이터 로딩 및 전처리
# ============================================================
def load_metrics(filepath: str) -> Optional[pd.DataFrame]:
    """메트릭 CSV 로드 및 v14.0 -> v6 컬럼 매핑"""
    if not os.path.exists(filepath):
        print(f"[Error] Metrics file not found: {filepath}")
        return None
    
    df = pd.read_csv(filepath)
    
    # 컬럼 이름 매핑 (호환성 확보)
    rename_map = {
        'CPU(m)': 'CPU_Total(m)',
        'Mem(Mi)': 'Memory_WorkingSet(Mi)',
        'Disk(MB/s)': 'Disk_Total(MB/s)'
    }
    df.rename(columns=rename_map, inplace=True)

    if 'Category' not in df.columns:
        df['Category'] = 'application'
    if 'Service' not in df.columns and 'Pod' in df.columns:
        df['Service'] = df['Pod'].apply(lambda x: x.split('-')[0])

    df['RPS'] = pd.to_numeric(df['RPS'], errors='coerce')
    
    print(f"[Info] Loaded {len(df)} rows from {filepath}")
    return df

def parse_latency_to_ms(val) -> float:
    """10.67ms -> 10.67 변환"""
    if pd.isna(val) or str(val).strip().upper() == 'N/A': return np.nan
    val = str(val).strip().lower()
    if val.endswith('ms'): return float(val[:-2])
    elif val.endswith('us'): return float(val[:-2]) / 1000
    elif val.endswith('s'): return float(val[:-1]) * 1000
    else:
        try: return float(val)
        except: return np.nan

def load_latency(filepath: str) -> pd.DataFrame:
    """Latency CSV 로드 및 숫자 변환"""
    if not os.path.exists(filepath):
        print(f"[Warn] Latency file not found: {filepath}")
        return pd.DataFrame()
    
    df = pd.read_csv(filepath)
    
    if 'RPS' in df.columns and 'Target_RPS' not in df.columns:
        df.rename(columns={'RPS': 'Target_RPS'}, inplace=True)
    
    # 가능한 모든 Latency 컬럼 패턴 처리
    # 1. P50_Latency (사용자 데이터) -> P50_Latency_ms
    # 2. P50_ms (일부 스크립트) -> P50_ms
    target_cols = ['P50', 'P75', 'P90', 'P99', 'P99.9']
    
    for p in target_cols:
        # P50_Latency -> P50_Latency_ms
        col_long = f"{p}_Latency"
        if col_long in df.columns:
            df[f"{col_long}_ms"] = df[col_long].apply(parse_latency_to_ms)
            
        # P50_ms -> P50_ms (이미 숫자인 경우 pass, 문자면 변환)
        col_short = f"{p}_ms"
        if col_short in df.columns:
             if df[col_short].dtype == object:
                 df[col_short] = df[col_short].apply(parse_latency_to_ms)

    return df

# ============================================================
# Saturation 감지
# ============================================================
def detect_saturation_point(df: pd.DataFrame, df_lat: pd.DataFrame) -> Optional[int]:
    if df_lat.empty: return None
    df_lat = df_lat.sort_values('Target_RPS')
    for idx, row in df_lat.iterrows():
        target = row.get('Target_RPS', 0)
        actual = row.get('Actual_RPS', 0)
        errors = row.get('Errors', 0) # parse_wrk.py uses 'Errors' count
        
        # Error count check
        if errors > 0:
            print(f"[Saturation] Detected at {target} RPS (Errors > 0)")
            return int(target)
            
        if actual > 0 and actual < target * 0.9:
            print(f"[Saturation] Detected at {target} RPS (Throughput Drop)")
            return int(target)
    return None

# ============================================================
# Plotting Functions
# ============================================================
def plot_core_heatmap(df: pd.DataFrame, output_prefix: str = ""):
    core_cols = [c for c in df.columns if c.startswith('Core_')]
    if not core_cols: return
    core_data = df.groupby('RPS')[core_cols].mean()
    plt.figure(figsize=(12, 8))
    sns.heatmap(core_data.T, cmap='RdYlGn_r', vmin=0, vmax=100, cbar_kws={'label': 'Core Usage (%)'})
    plt.title("CPU Core Usage Heatmap")
    plt.xlabel("Target RPS")
    plt.ylabel("CPU Cores")
    plt.tight_layout()
    plt.savefig(f"{output_prefix}0_core_heatmap.png", bbox_inches='tight')
    plt.close()

def plot_category_overview(df: pd.DataFrame, output_prefix: str = ""):
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    app_df = df[df['Category'] == 'application']
    if app_df.empty: return

    # 1. CPU
    ax = axes[0, 0]
    cpu = app_df.groupby('RPS')['CPU_Total(m)'].sum().reset_index()
    ax.bar(cpu['RPS'].astype(str), cpu['CPU_Total(m)'], color='steelblue', alpha=0.8)
    ax.set_title("1. Total CPU Usage")
    ax.set_ylabel("CPU (m)")

    # 2. Mem
    ax = axes[0, 1]
    mem = app_df.groupby('RPS')['Memory_WorkingSet(Mi)'].sum().reset_index()
    ax.bar(mem['RPS'].astype(str), mem['Memory_WorkingSet(Mi)'], color='coral', alpha=0.8)
    ax.set_title("2. Total Memory Usage")
    ax.set_ylabel("MiB")

    # 3. Disk
    ax = axes[1, 0]
    if 'Disk_Total(MB/s)' in app_df.columns:
        disk = app_df.groupby('RPS')['Disk_Total(MB/s)'].sum().reset_index()
        ax.bar(disk['RPS'].astype(str), disk['Disk_Total(MB/s)'], color='purple', alpha=0.7)
        ax.set_title("3. Total Disk I/O")
        ax.set_ylabel("MB/s")

    # 4. Top 5 CPU Pods
    ax = axes[1, 1]
    max_rps = app_df['RPS'].max()
    top = app_df[app_df['RPS'] == max_rps].groupby('Service')['CPU_Total(m)'].sum().nlargest(5)
    ax.barh(top.index, top.values, color='green', alpha=0.7)
    ax.set_title(f"4. Top 5 CPU Services (@ {max_rps} RPS)")
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}1_overview.png", bbox_inches='tight')
    plt.close()

def plot_service_breakdown(df: pd.DataFrame, output_prefix: str = ""):
    app_df = df[df['Category'] == 'application']
    if app_df.empty: return
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    
    # 1. Area Chart
    ax = axes[0]
    pivot = app_df.groupby(['RPS', 'Service'])['CPU_Total(m)'].mean().unstack(fill_value=0)
    top = pivot.sum().nlargest(8).index
    pivot[top].plot(kind='area', stacked=True, ax=ax, alpha=0.7)
    ax.set_title("CPU Usage by Service")
    ax.set_ylabel("CPU (m)")
    
    # 2. Max Single Pod
    ax = axes[1]
    max_pod = app_df.loc[app_df.groupby('RPS')['CPU_Total(m)'].idxmax()]
    sns.barplot(data=max_pod, x='RPS', y='CPU_Total(m)', ax=ax, color='orange')
    for i, p in enumerate(ax.patches):
        if i < len(max_pod):
            ax.text(p.get_x() + p.get_width()/2., p.get_height(), 
                    max_pod.iloc[i]['Service'], ha="center", va="bottom", fontsize=8)
    ax.set_title("Most CPU-Intensive Single Pod")
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}2_service_breakdown.png", bbox_inches='tight')
    plt.close()

def plot_latency_analysis(df_lat: pd.DataFrame, saturation_rps: Optional[int], output_prefix: str = ""):
    if df_lat.empty: return
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    
    # [FIXED] Column Matching Logic
    ax = axes[0]
    
    # 우선순위: P50_Latency_ms (사용자 CSV) -> P50_ms (대안)
    plot_map = {
        'P50': ['P50_Latency_ms', 'P50_ms'],
        'P90': ['P90_Latency_ms', 'P90_ms'],
        'P99': ['P99_Latency_ms', 'P99_ms'],
        'P99.9': ['P99.9_Latency_ms', 'P99.9_ms']
    }
    
    # Prepare data for plotting
    lat_avg = df_lat.groupby('Target_RPS').mean(numeric_only=True).reset_index().sort_values('Target_RPS')
    
    plotted = False
    for label, candidates in plot_map.items():
        for col in candidates:
            if col in lat_avg.columns:
                # Drop N/A values for clean plotting
                valid_data = lat_avg.dropna(subset=[col])
                if not valid_data.empty:
                    ax.plot(valid_data['Target_RPS'], valid_data[col], marker='o', label=label, lw=2)
                    plotted = True
                break # Found valid column for this percentile

    if plotted:
        ax.set_yscale('log')
        ax.legend()
        ax.grid(True, alpha=0.3, which="both")
    
    if saturation_rps:
        ax.axvline(x=saturation_rps, color='red', ls='--', label='Saturation')

    ax.set_title("Latency Percentiles (Log Scale)")
    ax.set_ylabel("Latency (ms)")

    # 2. Throughput
    ax = axes[1]
    ax.plot(lat_avg['Target_RPS'], lat_avg['Actual_RPS'], 'b-o', label='Actual')
    ax.plot(lat_avg['Target_RPS'], lat_avg['Target_RPS'], 'g--', label='Target')
    ax.set_title("Throughput vs Target RPS")
    ax.set_ylabel("Req/s")
    ax.legend()
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}3_latency_analysis.png", bbox_inches='tight')
    plt.close()

def plot_xtella_metrics(df: pd.DataFrame, output_prefix: str = ""):
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    app_df = df[df['Category'] == 'application']

    # 1. Disk Throughput per Service
    ax = axes[0, 0]
    if 'Disk_Total(MB/s)' in app_df.columns:
        disk_sum = app_df.groupby(['RPS', 'Service'])['Disk_Total(MB/s)'].mean().reset_index()
        top = disk_sum.groupby('Service')['Disk_Total(MB/s)'].mean().nlargest(5).index
        for svc in top:
            d = disk_sum[disk_sum['Service'] == svc].sort_values('RPS')
            ax.plot(d['RPS'], d['Disk_Total(MB/s)'], marker='o', label=svc)
        ax.set_title("1. Disk Throughput (Top 5)")
        ax.set_ylabel("MB/s")
        ax.legend()

    # 2. Node Disk Latency
    ax = axes[0, 1]
    if 'Node_Disk_Lat_R(ms)' in df.columns:
        node = df.groupby('RPS')[['Node_Disk_Lat_R(ms)', 'Node_Disk_Lat_W(ms)']].mean()
        ax.plot(node.index, node['Node_Disk_Lat_R(ms)'], marker='x', label='Read', color='green')
        ax.plot(node.index, node['Node_Disk_Lat_W(ms)'], marker='s', label='Write', color='red')
        ax.set_title("2. Node Disk Latency")
        ax.set_ylabel("ms")
        ax.legend()

    # 3. Mem BW
    ax = axes[1, 0]
    if 'Mem_BW(%)' in df.columns:
        mbw = df.groupby('RPS')['Mem_BW(%)'].mean()
        ax.plot(mbw.index, mbw.values, color='purple', marker='o')
        ax.set_title("3. Memory BW Usage (%)")
        ax.set_ylim(0, 100)

    # 4. Core Util
    ax = axes[1, 1]
    if 'Max_Core_Util(%)' in df.columns:
        mc = df.groupby('RPS')['Max_Core_Util(%)'].max()
        ax.plot(mc.index, mc.values, color='brown', marker='s')
        ax.set_title("4. Max Single Core Util (%)")
        ax.set_ylim(0, 100)
        
    plt.tight_layout()
    plt.savefig(f"{output_prefix}4_xtella_io_analysis.png", bbox_inches='tight')
    plt.close()

def plot_cpu_efficiency(df: pd.DataFrame, df_lat: pd.DataFrame, output_prefix: str = ""):
    if df_lat.empty or df.empty: return
    app_df = df[df['Category'] == 'application']
    if app_df.empty: return
    
    cpu = app_df.groupby('RPS')['CPU_Total(m)'].sum().reset_index()
    lat = df_lat.groupby('Target_RPS')['Actual_RPS'].mean().reset_index()
    merged = cpu.merge(lat, left_on='RPS', right_on='Target_RPS')
    merged['Eff'] = merged['CPU_Total(m)'] / merged['Actual_RPS']
    
    plt.figure(figsize=(10, 6))
    plt.plot(merged['RPS'], merged['Eff'], 'b-o')
    plt.title("CPU Efficiency (mCore per Request)")
    plt.xlabel("RPS")
    plt.ylabel("mCore/Req")
    plt.savefig(f"{output_prefix}5_cpu_efficiency.png", bbox_inches='tight')
    plt.close()

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 plot_results.py <metrics.csv> [latency.csv] [output_dir]")
        sys.exit(1)

    metrics_file = sys.argv[1]
    latency_file = sys.argv[2] if len(sys.argv) > 2 else "latency_stats.csv"
    output_dir = sys.argv[3] if len(sys.argv) > 3 else "."
    output_prefix = os.path.join(output_dir, "")

    df = load_metrics(metrics_file)
    df_lat = load_latency(latency_file)
    
    if df is None: return

    sat_rps = detect_saturation_point(df, df_lat)

    plot_core_heatmap(df, output_prefix)
    plot_category_overview(df, output_prefix)
    plot_service_breakdown(df, output_prefix)
    plot_latency_analysis(df_lat, sat_rps, output_prefix)
    plot_xtella_metrics(df, output_prefix)
    plot_cpu_efficiency(df, df_lat, output_prefix)

    print("\nDone! Visualizations generated in:", output_dir)

if __name__ == "__main__":
    main()