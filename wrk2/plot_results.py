#!/usr/bin/env python3
"""
통합 결과 시각화 및 분석 스크립트 v4
- 포화(saturation) 구간 자동 감지 및 시각화
- 정상 구간 / 전체 구간 분리 그래프
- 로그 스케일 지원
- 다중 namespace 지원
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import os
import sys

plt.rcParams['figure.dpi'] = 120
plt.rcParams['savefig.dpi'] = 150
plt.rcParams['font.size'] = 10
sns.set_style("whitegrid")

# ============================================================
# 포화 감지 유틸리티
# ============================================================
def detect_saturation_point(df: pd.DataFrame, df_lat: pd.DataFrame = None) -> int:
    """
    포화 지점 자동 감지
    - Latency가 급격히 증가하거나
    - CPU 증가율이 급격히 감소하는 지점
    
    Returns: 포화 시작 RPS (없으면 None)
    """
    saturation_rps = None
    
    # 방법 1: Latency 기반 (P99가 이전 대비 5배 이상 증가)
    if df_lat is not None and not df_lat.empty:
        p99_col = 'P99_Latency_ms' if 'P99_Latency_ms' in df_lat.columns else None
        if p99_col:
            sorted_lat = df_lat.sort_values('Target_RPS')
            p99_values = sorted_lat[p99_col].values
            rps_values = sorted_lat['Target_RPS'].values
            
            for i in range(1, len(p99_values)):
                if p99_values[i-1] > 0 and p99_values[i] / p99_values[i-1] > 5:
                    saturation_rps = rps_values[i]
                    break
    
    # 방법 2: CPU 증가율 기반 (증가율이 50% 이하로 떨어지면)
    if saturation_rps is None and df is not None:
        app_df = df[df['Category'] == 'application']
        cpu_by_rps = app_df.groupby('RPS')['CPU_Total(m)'].sum().sort_index()
        
        if len(cpu_by_rps) >= 3:
            rps_list = cpu_by_rps.index.tolist()
            cpu_list = cpu_by_rps.values.tolist()
            
            for i in range(2, len(rps_list)):
                # 예상 증가량 vs 실제 증가량
                expected_ratio = (rps_list[i] - rps_list[i-1]) / (rps_list[i-1] - rps_list[i-2])
                actual_ratio = (cpu_list[i] - cpu_list[i-1]) / max(cpu_list[i-1] - cpu_list[i-2], 1)
                
                # 실제 증가가 예상의 50% 미만이면 포화
                if expected_ratio > 0 and actual_ratio / expected_ratio < 0.5:
                    saturation_rps = rps_list[i]
                    break
    
    return saturation_rps

def split_by_saturation(df: pd.DataFrame, saturation_rps: int):
    """데이터를 포화 전/후로 분리"""
    if saturation_rps is None:
        return df, pd.DataFrame()
    
    normal = df[df['RPS'] < saturation_rps]
    saturated = df[df['RPS'] >= saturation_rps]
    return normal, saturated

# ============================================================
# 데이터 로딩
# ============================================================
def load_metrics(filepath: str = "k8s_full_metrics.csv") -> pd.DataFrame:
    if not os.path.exists(filepath):
        print(f"Error: {filepath} not found")
        return None
    
    df = pd.read_csv(filepath)
    
    column_mapping = {
        'CPU(m)': 'CPU_Total(m)',
        'Memory(Mi)': 'Memory_WorkingSet(Mi)',
    }
    df.rename(columns=column_mapping, inplace=True)
    
    if 'Namespace' not in df.columns:
        df['Namespace'] = 'default'
    if 'Category' not in df.columns:
        df['Category'] = 'application'
    if 'CPU_App(m)' not in df.columns:
        df['CPU_App(m)'] = df['CPU_Total(m)']
    if 'CPU_Sidecar(m)' not in df.columns:
        df['CPU_Sidecar(m)'] = 0
    
    return df

def load_latency(filepath: str = "latency_stats.csv") -> pd.DataFrame:
    if not os.path.exists(filepath):
        print(f"Warning: {filepath} not found")
        return pd.DataFrame()
    
    df = pd.read_csv(filepath)
    
    if 'RPS' in df.columns and 'Target_RPS' not in df.columns:
        df.rename(columns={'RPS': 'Target_RPS'}, inplace=True)
    
    def parse_latency(val):
        if pd.isna(val) or val == 'N/A':
            return np.nan
        if isinstance(val, (int, float)):
            return float(val)
        val = str(val).strip()
        if 'us' in val:
            return float(val.replace('us', '')) / 1000.0
        elif 'ms' in val:
            return float(val.replace('ms', ''))
        elif 's' in val and 'ms' not in val:
            return float(val.replace('s', '')) * 1000.0
        try:
            return float(val)
        except:
            return np.nan
    
    latency_cols = [c for c in df.columns if 'Latency' in c and '_ms' not in c]
    for col in latency_cols:
        df[f'{col}_ms'] = df[col].apply(parse_latency)
    
    return df

# ============================================================
# 기본 시각화 함수
# ============================================================
def plot_category_overview(df: pd.DataFrame, output_prefix: str = "", saturation_rps: int = None):
    """카테고리별 리소스 사용량 개요 (포화 구간 표시 포함)"""
    
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    categories = df['Category'].unique()
    cat_palette = {
        'application': '#3498db',
        'istio-control-plane': '#e74c3c',
        'kubernetes-system': '#2ecc71'
    }
    for cat in categories:
        if cat not in cat_palette:
            cat_palette[cat] = '#95a5a6'
    
    # ★ 반복 측정 고려: 먼저 RPS+Service별 평균을 구한 후, Category별 합계
    # Step 1: RPS, Category, Service별 평균
    svc_avg = df.groupby(['RPS', 'Category', 'Service']).agg({
        'CPU_Total(m)': 'mean',
        'Memory_WorkingSet(Mi)': 'mean',
        'Net_RX(KB/s)': 'mean'
    }).reset_index()
    
    # Step 2: RPS, Category별 합계 (서비스들의 평균값을 합산)
    cat_cpu = svc_avg.groupby(['RPS', 'Category'])['CPU_Total(m)'].sum().reset_index()
    cat_cpu = cat_cpu.sort_values('RPS')
    
    # [1] 카테고리별 총 CPU
    ax = axes[0, 0]
    
    for cat in categories:
        data = cat_cpu[cat_cpu['Category'] == cat].sort_values('RPS')
        color = cat_palette.get(cat)
        
        # 전체 데이터를 먼저 그림 (연결선)
        ax.plot(data['RPS'], data['CPU_Total(m)'], color=color, linewidth=2, alpha=0.8)
        
        # 정상 구간: 원형 마커
        if saturation_rps:
            normal = data[data['RPS'] < saturation_rps]
            saturated = data[data['RPS'] >= saturation_rps]
            
            if not normal.empty:
                ax.scatter(normal['RPS'], normal['CPU_Total(m)'], marker='o', 
                          color=color, s=60, zorder=5, label=cat)
            if not saturated.empty:
                ax.scatter(saturated['RPS'], saturated['CPU_Total(m)'], marker='x',
                          color=color, s=80, zorder=5, alpha=0.7)
        else:
            ax.scatter(data['RPS'], data['CPU_Total(m)'], marker='o',
                      color=color, s=60, zorder=5, label=cat)
    
    if saturation_rps:
        ax.axvline(x=saturation_rps, color='red', linestyle=':', alpha=0.7)
        ax.annotate(f'Saturation', xy=(saturation_rps, ax.get_ylim()[1]*0.95),
                   fontsize=9, color='red', ha='center')
    
    ax.set_title("1. Total CPU by Category")
    ax.set_xlabel("RPS")
    ax.set_ylabel("CPU (millicores)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    # [2] 카테고리별 총 Memory
    ax = axes[0, 1]
    cat_mem = svc_avg.groupby(['RPS', 'Category'])['Memory_WorkingSet(Mi)'].sum().reset_index()
    cat_mem = cat_mem.sort_values('RPS')
    
    for cat in categories:
        data = cat_mem[cat_mem['Category'] == cat].sort_values('RPS')
        color = cat_palette.get(cat)
        ax.plot(data['RPS'], data['Memory_WorkingSet(Mi)'], color=color, linewidth=2, alpha=0.8)
        ax.scatter(data['RPS'], data['Memory_WorkingSet(Mi)'], marker='s',
                  color=color, s=60, zorder=5, label=cat)
    
    if saturation_rps:
        ax.axvline(x=saturation_rps, color='red', linestyle=':', alpha=0.7)
    
    ax.set_title("2. Total Memory by Category")
    ax.set_xlabel("RPS")
    ax.set_ylabel("Memory (MiB)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    # [3] 카테고리별 네트워크 RX
    ax = axes[1, 0]
    cat_net = svc_avg.groupby(['RPS', 'Category'])['Net_RX(KB/s)'].sum().reset_index()
    cat_net = cat_net.sort_values('RPS')
    
    for cat in categories:
        data = cat_net[cat_net['Category'] == cat].sort_values('RPS')
        color = cat_palette.get(cat)
        
        # 전체 데이터를 먼저 그림 (연결선)
        ax.plot(data['RPS'], data['Net_RX(KB/s)'], color=color, linewidth=2, alpha=0.8)
        
        # 마커 구분
        if saturation_rps:
            normal = data[data['RPS'] < saturation_rps]
            saturated = data[data['RPS'] >= saturation_rps]
            
            if not normal.empty:
                ax.scatter(normal['RPS'], normal['Net_RX(KB/s)'], marker='^',
                          color=color, s=60, zorder=5, label=cat)
            if not saturated.empty:
                ax.scatter(saturated['RPS'], saturated['Net_RX(KB/s)'], marker='x',
                          color=color, s=80, zorder=5, alpha=0.7)
        else:
            ax.scatter(data['RPS'], data['Net_RX(KB/s)'], marker='^',
                      color=color, s=60, zorder=5, label=cat)
    
    if saturation_rps:
        ax.axvline(x=saturation_rps, color='red', linestyle=':', alpha=0.7)
    
    ax.set_title("3. Total Network RX by Category")
    ax.set_xlabel("RPS")
    ax.set_ylabel("KB/s")
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    # [4] 카테고리별 CPU 비율 (Stacked)
    ax = axes[1, 1]
    pivot = cat_cpu.pivot(index='RPS', columns='Category', values='CPU_Total(m)').fillna(0)
    pivot_pct = pivot.div(pivot.sum(axis=1), axis=0) * 100
    
    pivot_pct.plot(kind='bar', stacked=True, ax=ax,
                   color=[cat_palette.get(c, '#95a5a6') for c in pivot_pct.columns])
    ax.set_title("4. CPU Distribution by Category (%)")
    ax.set_xlabel("RPS")
    ax.set_ylabel("Percentage")
    ax.legend(title='Category', bbox_to_anchor=(1.02, 1))
    ax.set_xticklabels(ax.get_xticklabels(), rotation=0)
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}category_overview.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}category_overview.png")
    plt.close()

def plot_category_normal_range(df: pd.DataFrame, output_prefix: str = "", saturation_rps: int = None):
    """포화 전 정상 구간만 확대한 그래프"""
    
    if saturation_rps is None:
        return  # 포화 없으면 이 그래프 생략
    
    normal_df = df[df['RPS'] < saturation_rps]
    
    if normal_df.empty or len(normal_df['RPS'].unique()) < 2:
        return
    
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    
    categories = normal_df['Category'].unique()
    cat_palette = {
        'application': '#3498db',
        'istio-control-plane': '#e74c3c',
        'kubernetes-system': '#2ecc71'
    }
    
    # ★ 반복 측정 고려: 먼저 Service별 평균, 그 다음 Category별 합계
    svc_avg = normal_df.groupby(['RPS', 'Category', 'Service']).agg({
        'CPU_Total(m)': 'mean',
        'Net_RX(KB/s)': 'mean'
    }).reset_index()
    
    # [1] CPU (정상 구간)
    ax = axes[0]
    cat_cpu = svc_avg.groupby(['RPS', 'Category'])['CPU_Total(m)'].sum().reset_index()
    cat_cpu = cat_cpu.sort_values('RPS')
    
    for cat in categories:
        data = cat_cpu[cat_cpu['Category'] == cat].sort_values('RPS')
        ax.plot(data['RPS'], data['CPU_Total(m)'], marker='o',
               label=cat, color=cat_palette.get(cat, '#95a5a6'), linewidth=2)
    
    ax.set_title(f"CPU (Normal Range: < {saturation_rps} RPS)")
    ax.set_xlabel("RPS")
    ax.set_ylabel("CPU (millicores)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    # [2] Network (정상 구간)
    ax = axes[1]
    cat_net = svc_avg.groupby(['RPS', 'Category'])['Net_RX(KB/s)'].sum().reset_index()
    cat_net = cat_net.sort_values('RPS')
    
    for cat in categories:
        data = cat_net[cat_net['Category'] == cat].sort_values('RPS')
        ax.plot(data['RPS'], data['Net_RX(KB/s)'], marker='^',
               label=cat, color=cat_palette.get(cat, '#95a5a6'), linewidth=2)
    
    ax.set_title(f"Network RX (Normal Range: < {saturation_rps} RPS)")
    ax.set_xlabel("RPS")
    ax.set_ylabel("KB/s")
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}category_normal_range.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}category_normal_range.png")
    plt.close()

def plot_istio_overhead_detail(df: pd.DataFrame, output_prefix: str = ""):
    """Istio 오버헤드 상세 분석"""
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    app_df = df[df['Category'] == 'application']
    istio_cp = df[df['Category'] == 'istio-control-plane']
    
    # [1] App CPU vs Sidecar CPU
    ax = axes[0, 0]
    if not app_df.empty and app_df['CPU_Sidecar(m)'].sum() > 0:
        # ★ 반복 측정 고려: Service별 평균 → RPS별 합계
        svc_avg = app_df.groupby(['RPS', 'Service']).agg({
            'CPU_App(m)': 'mean',
            'CPU_Sidecar(m)': 'mean'
        }).reset_index()
        cpu_breakdown = svc_avg.groupby('RPS').agg({
            'CPU_App(m)': 'sum',
            'CPU_Sidecar(m)': 'sum'
        }).reset_index().sort_values('RPS')
        
        x = np.arange(len(cpu_breakdown))
        width = 0.35
        
        ax.bar(x - width/2, cpu_breakdown['CPU_App(m)'], width, label='App', color='#3498db')
        ax.bar(x + width/2, cpu_breakdown['CPU_Sidecar(m)'], width, label='Sidecar', color='#e74c3c')
        
        ax.set_xticks(x)
        ax.set_xticklabels(cpu_breakdown['RPS'])
        ax.set_title("1. Application CPU: App vs Sidecar")
        ax.set_xlabel("RPS")
        ax.set_ylabel("CPU (millicores)")
        ax.legend()
    else:
        ax.text(0.5, 0.5, 'No Sidecar Data\n(Non-Istio or Istio not measured)',
                ha='center', va='center', transform=ax.transAxes, fontsize=12)
        ax.set_title("1. Application CPU: App vs Sidecar")
    
    # [2] istiod CPU (단일 서비스이므로 mean만 사용)
    ax = axes[0, 1]
    if not istio_cp.empty:
        istiod = istio_cp[istio_cp['Service'].str.contains('istiod', case=False, na=False)]
        if not istiod.empty:
            istiod_cpu = istiod.groupby('RPS')['CPU_Total(m)'].mean().reset_index().sort_values('RPS')
            ax.plot(istiod_cpu['RPS'], istiod_cpu['CPU_Total(m)'],
                   marker='o', linewidth=2, color='#e74c3c')
            ax.fill_between(istiod_cpu['RPS'], istiod_cpu['CPU_Total(m)'], alpha=0.3, color='#e74c3c')
            ax.set_title("2. istiod (Control Plane) CPU")
            ax.set_xlabel("RPS")
            ax.set_ylabel("CPU (millicores)")
        else:
            ax.text(0.5, 0.5, 'istiod not found', ha='center', va='center', transform=ax.transAxes)
            ax.set_title("2. istiod CPU")
    else:
        ax.text(0.5, 0.5, 'istio-system not measured\n(Use --all-namespaces)',
                ha='center', va='center', transform=ax.transAxes, fontsize=12)
        ax.set_title("2. istiod (Control Plane) CPU")
    
    # [3] Sidecar CPU 비율 분포 (boxplot은 raw 데이터 사용 OK)
    ax = axes[1, 0]
    if not app_df.empty and app_df['CPU_Sidecar(m)'].sum() > 0:
        app_df_copy = app_df.copy()
        app_df_copy['Sidecar_Ratio'] = app_df_copy['CPU_Sidecar(m)'] / app_df_copy['CPU_Total(m)'].replace(0, np.nan) * 100
        
        sns.boxplot(data=app_df_copy, x='RPS', y='Sidecar_Ratio', ax=ax)
        ax.axhline(y=50, color='red', linestyle='--', alpha=0.5, label='50% threshold')
        ax.set_title("3. Sidecar CPU Ratio Distribution")
        ax.set_ylabel("Sidecar CPU / Total CPU (%)")
        ax.legend()
    else:
        ax.text(0.5, 0.5, 'No Sidecar Data', ha='center', va='center', transform=ax.transAxes)
        ax.set_title("3. Sidecar CPU Ratio Distribution")
    
    # [4] 전체 Istio 오버헤드
    ax = axes[1, 1]
    
    # ★ 반복 측정 고려
    if not app_df.empty:
        svc_avg_sidecar = app_df.groupby(['RPS', 'Service'])['CPU_Sidecar(m)'].mean().reset_index()
        sidecar_total = svc_avg_sidecar.groupby('RPS')['CPU_Sidecar(m)'].sum().reset_index()
    else:
        sidecar_total = pd.DataFrame()
    
    if not istio_cp.empty:
        cp_svc_avg = istio_cp.groupby(['RPS', 'Service'])['CPU_Total(m)'].mean().reset_index()
        cp_total = cp_svc_avg.groupby('RPS')['CPU_Total(m)'].sum().reset_index()
    else:
        cp_total = pd.DataFrame()
    
    if not sidecar_total.empty or not cp_total.empty:
        rps_values = sorted(df['RPS'].unique())
        sidecar_vals = []
        cp_vals = []
        
        for rps in rps_values:
            sc = sidecar_total[sidecar_total['RPS'] == rps]['CPU_Sidecar(m)'].sum() if not sidecar_total.empty else 0
            cp = cp_total[cp_total['RPS'] == rps]['CPU_Total(m)'].sum() if not cp_total.empty else 0
            sidecar_vals.append(sc)
            cp_vals.append(cp)
        
        x = np.arange(len(rps_values))
        width = 0.35
        
        ax.bar(x - width/2, sidecar_vals, width, label='All Sidecars', color='#f39c12')
        ax.bar(x + width/2, cp_vals, width, label='Control Plane', color='#e74c3c')
        
        ax.set_xticks(x)
        ax.set_xticklabels(rps_values)
        ax.set_title("4. Total Istio Overhead")
        ax.set_xlabel("RPS")
        ax.set_ylabel("CPU (millicores)")
        ax.legend()
    else:
        ax.text(0.5, 0.5, 'No Istio overhead data', ha='center', va='center', transform=ax.transAxes)
        ax.set_title("4. Total Istio Overhead")
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}istio_overhead_detail.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}istio_overhead_detail.png")
    plt.close()

def plot_service_breakdown(df: pd.DataFrame, output_prefix: str = ""):
    """서비스별 상세 분석"""
    app_df = df[df['Category'] == 'application']
    
    if app_df.empty:
        print("Warning: No application data found")
        return
    
    fig, axes = plt.subplots(2, 2, figsize=(18, 14))
    
    services = app_df['Service'].unique()
    palette = dict(zip(services, sns.color_palette("tab20", len(services))))
    
    # [1] 서비스별 CPU
    ax = axes[0, 0]
    for service in services:
        svc_data = app_df[app_df['Service'] == service].groupby('RPS')['CPU_Total(m)'].mean()
        ax.plot(svc_data.index, svc_data.values, marker='o', label=service,
                color=palette[service], linewidth=1.5)
    
    ax.set_title("1. CPU by Service (Application)")
    ax.set_xlabel("RPS")
    ax.set_ylabel("CPU (millicores)")
    ax.legend(bbox_to_anchor=(1.02, 1), loc='upper left', fontsize='x-small')
    
    # [2] 서비스별 Network RX
    ax = axes[0, 1]
    for service in services:
        svc_data = app_df[app_df['Service'] == service].groupby('RPS')['Net_RX(KB/s)'].mean()
        ax.plot(svc_data.index, svc_data.values, marker='o', label=service,
                color=palette[service], linewidth=1.5)
    
    ax.set_title("2. Network RX by Service")
    ax.set_xlabel("RPS")
    ax.set_ylabel("KB/s")
    ax.legend(bbox_to_anchor=(1.02, 1), loc='upper left', fontsize='x-small')
    
    # [3] Top 5 CPU
    ax = axes[1, 0]
    top5 = app_df.groupby('Service')['CPU_Total(m)'].mean().nlargest(5)
    colors = [palette[s] for s in top5.index]
    top5.plot(kind='barh', ax=ax, color=colors)
    ax.set_title("3. Top 5 CPU Consumers")
    ax.set_xlabel("Avg CPU (millicores)")
    
    # [4] Top 5 Network
    ax = axes[1, 1]
    app_df = app_df.copy()
    app_df['Total_Net'] = app_df['Net_RX(KB/s)'] + app_df['Net_TX(KB/s)']
    top5_net = app_df.groupby('Service')['Total_Net'].mean().nlargest(5)
    colors = [palette[s] for s in top5_net.index]
    top5_net.plot(kind='barh', ax=ax, color=colors)
    ax.set_title("4. Top 5 Network Consumers")
    ax.set_xlabel("Avg Network (KB/s)")
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}service_breakdown.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}service_breakdown.png")
    plt.close()

def plot_latency_analysis(df_lat: pd.DataFrame, output_prefix: str = "", saturation_rps: int = None):
    """Latency 분석 (포화 감지 포함)"""
    if df_lat.empty:
        print("Warning: No latency data")
        return
    
    df_lat = df_lat.copy()
    
    # ★ 핵심: RPS로 정렬 (선이 이상하게 연결되는 문제 해결)
    df_lat = df_lat.sort_values('Target_RPS').reset_index(drop=True)
    
    p50_col = 'P50_Latency_ms' if 'P50_Latency_ms' in df_lat.columns else None
    p99_col = 'P99_Latency_ms' if 'P99_Latency_ms' in df_lat.columns else None
    p999_col = 'P99.9_Latency_ms' if 'P99.9_Latency_ms' in df_lat.columns else None
    
    # 포화 상태 마킹
    if saturation_rps:
        df_lat['is_saturated'] = df_lat['Target_RPS'] >= saturation_rps
    else:
        df_lat['is_saturated'] = False
    
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    percentile_cols = []
    if p50_col: percentile_cols.append(p50_col)
    if p99_col: percentile_cols.append(p99_col)
    if p999_col: percentile_cols.append(p999_col)
    
    if not percentile_cols:
        percentile_cols = [c for c in df_lat.columns if 'Latency' in c and '_ms' in c]
    
    colors = {'P50': '#3498db', 'P99': '#f39c12', 'P99.9': '#2ecc71'}
    
    # ================================================================
    # [1] Latency Percentiles (로그 스케일) - 전체 구간
    # ================================================================
    ax = axes[0, 0]
    
    for col in percentile_cols:
        label = col.replace('_Latency_ms', '')
        color = colors.get(label, None)
        ax.plot(df_lat['Target_RPS'], df_lat[col], marker='o', label=label, color=color, linewidth=2)
    
    ax.set_yscale('log')
    ax.set_title("1. Latency Percentiles (Log Scale)")
    ax.set_xlabel("RPS")
    ax.set_ylabel("Latency (ms) - Log Scale")
    ax.legend()
    ax.grid(True, alpha=0.3, which='both')
    
    if saturation_rps:
        ax.axvline(x=saturation_rps, color='red', linestyle=':', alpha=0.7)
    
    # ================================================================
    # [2] Latency (정상 구간만) - 선형 스케일
    # ================================================================
    ax = axes[0, 1]
    normal_data = df_lat[~df_lat['is_saturated']].copy()
    
    if not normal_data.empty and len(normal_data) >= 2:
        # 정상 구간도 정렬 확인
        normal_data = normal_data.sort_values('Target_RPS')
        
        for col in percentile_cols:
            label = col.replace('_Latency_ms', '')
            color = colors.get(label, None)
            ax.plot(normal_data['Target_RPS'], normal_data[col],
                   marker='o', label=label, color=color, linewidth=2)
        
        # Y축 범위: 데이터 기반으로 설정
        if p999_col and p999_col in normal_data.columns:
            y_max = normal_data[p999_col].max()
        elif p99_col:
            y_max = normal_data[p99_col].max()
        else:
            y_max = normal_data[percentile_cols[0]].max()
        
        ax.set_ylim(0, y_max * 1.2)
        
        title = "2. Latency (Normal Range)"
        if saturation_rps:
            title += f" [< {saturation_rps} RPS]"
        ax.set_title(title)
        ax.set_xlabel("RPS")
        ax.set_ylabel("Latency (ms)")
        ax.legend()
        ax.grid(True, alpha=0.3)
    else:
        ax.text(0.5, 0.5, 'All data points are saturated\nor insufficient data',
               ha='center', va='center', transform=ax.transAxes, fontsize=11)
        ax.set_title("2. Latency (Normal Range)")
    
    # ================================================================
    # [3] Tail Latency Ratio (P99/P50)
    # ================================================================
    ax = axes[1, 0]
    if p50_col and p99_col:
        df_lat['Tail_Ratio'] = df_lat[p99_col] / df_lat[p50_col].replace(0, np.nan)
        bar_colors = ['#e74c3c' if sat else '#3498db' for sat in df_lat['is_saturated']]
        
        # X축 라벨을 정렬된 순서로
        x_labels = df_lat['Target_RPS'].astype(int).astype(str).tolist()
        
        ax.bar(x_labels, df_lat['Tail_Ratio'], color=bar_colors)
        ax.axhline(y=2, color='orange', linestyle='--', label='2x threshold')
        ax.axhline(y=5, color='red', linestyle='--', label='5x threshold')
        ax.set_title("3. Tail Latency Ratio (P99/P50)")
        ax.set_xlabel("RPS")
        ax.set_ylabel("Ratio")
        ax.legend(loc='upper left')
        
        if df_lat['is_saturated'].any():
            ax.annotate('Red = Saturated', xy=(0.98, 0.98), xycoords='axes fraction',
                       ha='right', va='top', fontsize=9, color='#e74c3c')
    
    # ================================================================
    # [4] Actual vs Target RPS (처리량 달성)
    # ================================================================
    ax = axes[1, 1]
    if 'Actual_RPS' in df_lat.columns:
        # Scatter plot으로 변경 (더 명확함)
        scatter_colors = ['#e74c3c' if sat else '#3498db' for sat in df_lat['is_saturated']]
        
        ax.scatter(df_lat['Target_RPS'], df_lat['Actual_RPS'], 
                  c=scatter_colors, s=100, alpha=0.7, edgecolors='black', linewidths=0.5)
        
        # Ideal line (y=x)
        max_val = max(df_lat['Target_RPS'].max(), df_lat['Actual_RPS'].max())
        ax.plot([0, max_val], [0, max_val], 'k--', alpha=0.5, label='Ideal (100%)')
        
        # 각 점에 라벨 추가
        for idx, row in df_lat.iterrows():
            achievement = row['Actual_RPS'] / row['Target_RPS'] * 100
            ax.annotate(f"{achievement:.0f}%", 
                       (row['Target_RPS'], row['Actual_RPS']),
                       textcoords="offset points", xytext=(5, 5), fontsize=8)
        
        ax.set_title("4. Throughput: Actual vs Target RPS")
        ax.set_xlabel("Target RPS")
        ax.set_ylabel("Actual RPS")
        ax.legend(loc='upper left')
        ax.grid(True, alpha=0.3)
        
        if df_lat['is_saturated'].any():
            ax.annotate('Red = Saturated', xy=(0.98, 0.02), xycoords='axes fraction',
                       ha='right', va='bottom', fontsize=9, color='#e74c3c')
    else:
        ax.text(0.5, 0.5, 'No Actual_RPS data', ha='center', va='center', transform=ax.transAxes)
        ax.set_title("4. Throughput")
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}latency_analysis.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}latency_analysis.png")
    plt.close()

# ============================================================
# 심화 분석
# ============================================================
def plot_efficiency_analysis(df: pd.DataFrame, output_prefix: str = ""):
    """서비스별 효율성 분석"""
    app_df = df[df['Category'] == 'application'].copy()
    
    if app_df.empty:
        print("Warning: No application data for efficiency analysis")
        return
    
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    # [1] CPU per RPS
    ax = axes[0, 0]
    efficiency = app_df.groupby(['RPS', 'Service']).agg({'CPU_Total(m)': 'mean'}).reset_index()
    efficiency['CPU_per_100RPS'] = efficiency['CPU_Total(m)'] / efficiency['RPS'] * 100
    
    top_services = app_df.groupby('Service')['CPU_Total(m)'].mean().nlargest(5).index
    eff_top = efficiency[efficiency['Service'].isin(top_services)]
    
    for service in top_services:
        svc_data = eff_top[eff_top['Service'] == service]
        ax.plot(svc_data['RPS'], svc_data['CPU_per_100RPS'], marker='o', label=service)
    
    ax.set_title("1. CPU Efficiency (CPU per 100 RPS)")
    ax.set_xlabel("RPS")
    ax.set_ylabel("millicores per 100 req/s")
    ax.legend()
    
    # [2] Network per RPS
    ax = axes[0, 1]
    net_eff = app_df.groupby(['RPS', 'Service']).agg({
        'Net_RX(KB/s)': 'mean', 'Net_TX(KB/s)': 'mean'
    }).reset_index()
    net_eff['Net_Total'] = net_eff['Net_RX(KB/s)'] + net_eff['Net_TX(KB/s)']
    net_eff['Net_per_100RPS'] = net_eff['Net_Total'] / net_eff['RPS'] * 100
    
    top_net_services = app_df.groupby('Service')[['Net_RX(KB/s)', 'Net_TX(KB/s)']].mean().sum(axis=1).nlargest(5).index
    net_top = net_eff[net_eff['Service'].isin(top_net_services)]
    
    for service in top_net_services:
        svc_data = net_top[net_top['Service'] == service]
        ax.plot(svc_data['RPS'], svc_data['Net_per_100RPS'], marker='s', label=service)
    
    ax.set_title("2. Network Efficiency (KB per 100 RPS)")
    ax.set_xlabel("RPS")
    ax.set_ylabel("KB/s per 100 req/s")
    ax.legend()
    
    # [3] CPU vs Network 상관관계
    ax = axes[1, 0]
    svc_summary = app_df.groupby('Service').agg({
        'CPU_Total(m)': 'mean', 'Net_RX(KB/s)': 'mean', 'Net_TX(KB/s)': 'mean'
    }).reset_index()
    svc_summary['Net_Total'] = svc_summary['Net_RX(KB/s)'] + svc_summary['Net_TX(KB/s)']
    
    ax.scatter(svc_summary['CPU_Total(m)'], svc_summary['Net_Total'],
               s=100, alpha=0.7, c=range(len(svc_summary)), cmap='tab20')
    
    for idx, row in svc_summary.iterrows():
        ax.annotate(row['Service'], (row['CPU_Total(m)'], row['Net_Total']), fontsize=8, alpha=0.8)
    
    ax.set_title("3. CPU vs Network Correlation")
    ax.set_xlabel("Avg CPU (millicores)")
    ax.set_ylabel("Avg Network (KB/s)")
    
    # [4] 서비스 타입별 분류
    ax = axes[1, 1]
    
    def classify_service(name):
        if 'mongodb' in name.lower():
            return 'Database'
        elif 'memcached' in name.lower():
            return 'Cache'
        elif name in ['frontend', 'search', 'profile', 'rate', 'recommendation', 'reservation', 'user', 'geo']:
            return 'App Logic'
        elif 'consul' in name.lower():
            return 'Discovery'
        elif 'jaeger' in name.lower():
            return 'Tracing'
        else:
            return 'Other'
    
    app_df['ServiceType'] = app_df['Service'].apply(classify_service)
    
    # ★ 반복 측정 고려: Service별 평균 → ServiceType별 합계
    svc_avg_type = app_df.groupby(['RPS', 'ServiceType', 'Service'])['CPU_Total(m)'].mean().reset_index()
    type_summary = svc_avg_type.groupby(['RPS', 'ServiceType'])['CPU_Total(m)'].sum().reset_index()
    pivot = type_summary.pivot(index='RPS', columns='ServiceType', values='CPU_Total(m)').fillna(0)
    pivot.plot(kind='bar', stacked=True, ax=ax, colormap='Set2')
    
    ax.set_title("4. CPU by Service Type")
    ax.set_xlabel("RPS")
    ax.set_ylabel("Total CPU (millicores)")
    ax.legend(title='Type', bbox_to_anchor=(1.02, 1), fontsize='small')
    ax.set_xticklabels(ax.get_xticklabels(), rotation=0)
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}efficiency_analysis.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}efficiency_analysis.png")
    plt.close()

def plot_scalability_analysis(df: pd.DataFrame, df_lat: pd.DataFrame, output_prefix: str = "", saturation_rps: int = None):
    """스케일링 특성 분석"""
    app_df = df[df['Category'] == 'application'].copy()
    
    if app_df.empty:
        print("Warning: No application data for scalability analysis")
        return
    
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    # ★ 반복 측정 고려: Service별 평균 → RPS별 합계
    svc_avg = app_df.groupby(['RPS', 'Service']).agg({
        'CPU_Total(m)': 'mean',
        'Net_RX(KB/s)': 'mean'
    }).reset_index()
    
    total_by_rps = svc_avg.groupby('RPS').agg({
        'CPU_Total(m)': 'sum',
        'Net_RX(KB/s)': 'sum'
    }).reset_index().sort_values('RPS')
    
    # [1] 리소스 스케일링 - 전체 데이터 연결
    ax = axes[0, 0]
    ax2 = ax.twinx()
    
    # 전체 데이터를 먼저 선으로 연결
    ax.plot(total_by_rps['RPS'], total_by_rps['CPU_Total(m)'], 'b-', linewidth=2, alpha=0.8)
    ax2.plot(total_by_rps['RPS'], total_by_rps['Net_RX(KB/s)'], 'r-', linewidth=2, alpha=0.8)
    
    # 마커로 구분
    if saturation_rps:
        normal = total_by_rps[total_by_rps['RPS'] < saturation_rps]
        saturated = total_by_rps[total_by_rps['RPS'] >= saturation_rps]
        
        if not normal.empty:
            ax.scatter(normal['RPS'], normal['CPU_Total(m)'], marker='o', color='blue', s=60, zorder=5, label='CPU')
            ax2.scatter(normal['RPS'], normal['Net_RX(KB/s)'], marker='s', color='red', s=60, zorder=5, label='Network')
        if not saturated.empty:
            ax.scatter(saturated['RPS'], saturated['CPU_Total(m)'], marker='x', color='blue', s=80, zorder=5, alpha=0.7)
            ax2.scatter(saturated['RPS'], saturated['Net_RX(KB/s)'], marker='x', color='red', s=80, zorder=5, alpha=0.7)
        
        ax.axvline(x=saturation_rps, color='gray', linestyle=':', alpha=0.7)
    else:
        ax.scatter(total_by_rps['RPS'], total_by_rps['CPU_Total(m)'], marker='o', color='blue', s=60, zorder=5, label='CPU')
        ax2.scatter(total_by_rps['RPS'], total_by_rps['Net_RX(KB/s)'], marker='s', color='red', s=60, zorder=5, label='Network')
    
    ax.set_xlabel("RPS")
    ax.set_ylabel("Total CPU (millicores)", color='blue')
    ax2.set_ylabel("Total Network RX (KB/s)", color='red')
    ax.set_title("1. Resource Scaling with Load")
    ax.legend(loc='upper left')
    ax2.legend(loc='upper right')
    ax.grid(True, alpha=0.3)
    
    # [2] CPU 효율성 (CPU per 1000 RPS) - 더 의미있는 그래프로 변경
    ax = axes[0, 1]
    
    total_by_rps_sorted = total_by_rps.sort_values('RPS')
    total_by_rps_sorted['CPU_per_1000RPS'] = total_by_rps_sorted['CPU_Total(m)'] / total_by_rps_sorted['RPS'] * 1000
    
    ax.plot(total_by_rps_sorted['RPS'], total_by_rps_sorted['CPU_per_1000RPS'], 'g-', linewidth=2, alpha=0.8)
    
    if saturation_rps:
        normal = total_by_rps_sorted[total_by_rps_sorted['RPS'] < saturation_rps]
        saturated = total_by_rps_sorted[total_by_rps_sorted['RPS'] >= saturation_rps]
        
        if not normal.empty:
            ax.scatter(normal['RPS'], normal['CPU_per_1000RPS'], marker='o', color='green', s=60, zorder=5)
        if not saturated.empty:
            ax.scatter(saturated['RPS'], saturated['CPU_per_1000RPS'], marker='x', color='green', s=80, zorder=5, alpha=0.7)
        
        ax.axvline(x=saturation_rps, color='gray', linestyle=':', alpha=0.7)
    else:
        ax.scatter(total_by_rps_sorted['RPS'], total_by_rps_sorted['CPU_per_1000RPS'], marker='o', color='green', s=60, zorder=5)
    
    # 이상적인 수평선 (효율이 일정하면 수평)
    avg_efficiency = total_by_rps_sorted[total_by_rps_sorted['RPS'] < (saturation_rps or float('inf'))]['CPU_per_1000RPS'].mean()
    ax.axhline(y=avg_efficiency, color='orange', linestyle='--', alpha=0.7, label=f'Avg: {avg_efficiency:.0f}m')
    
    ax.set_xlabel("RPS")
    ax.set_ylabel("CPU per 1000 RPS (millicores)")
    ax.set_title("2. CPU Efficiency (lower = better)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    # [3] 스케일링 계수 (Service별 평균 사용)
    ax = axes[1, 0]
    scaling_coeffs = {}
    for service in app_df['Service'].unique():
        # ★ 반복 측정 고려: 먼저 평균
        svc_data = app_df[app_df['Service'] == service].groupby('RPS')['CPU_Total(m)'].mean()
        if len(svc_data) >= 2:
            z = np.polyfit(svc_data.index, svc_data.values, 1)
            scaling_coeffs[service] = z[0]
    
    coeffs_df = pd.DataFrame(list(scaling_coeffs.items()), columns=['Service', 'Scaling_Coeff'])
    coeffs_df = coeffs_df.sort_values('Scaling_Coeff', ascending=True)
    
    colors = ['red' if c > coeffs_df['Scaling_Coeff'].median() else 'green' for c in coeffs_df['Scaling_Coeff']]
    ax.barh(coeffs_df['Service'], coeffs_df['Scaling_Coeff'], color=colors)
    ax.axvline(x=coeffs_df['Scaling_Coeff'].median(), color='black', linestyle='--',
               label=f'Median: {coeffs_df["Scaling_Coeff"].median():.2f}')
    ax.set_title("3. CPU Scaling Coefficient by Service")
    ax.set_xlabel("CPU increase per RPS")
    ax.legend()
    
    # [4] 히트맵
    ax = axes[1, 1]
    pivot = app_df.groupby(['RPS', 'Service'])['CPU_Total(m)'].mean().unstack(fill_value=0)
    pivot_pct = pivot.div(pivot.sum(axis=1), axis=0) * 100
    top10 = pivot.mean().nlargest(10).index
    pivot_top = pivot_pct[top10]
    
    sns.heatmap(pivot_top.T, annot=True, fmt='.1f', cmap='YlOrRd', ax=ax)
    ax.set_title("4. CPU Share by Service (%)")
    ax.set_xlabel("RPS")
    ax.set_ylabel("Service")
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}scalability_analysis.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}scalability_analysis.png")
    plt.close()

# ============================================================
# 요약
# ============================================================
def generate_baseline_summary(df: pd.DataFrame, df_lat: pd.DataFrame, output_prefix: str = "", saturation_rps: int = None):
    """Baseline 요약 CSV"""
    app_df = df[df['Category'] == 'application']
    
    # ★ 반복 측정 고려: Service별 평균 → RPS별 합계
    svc_avg = app_df.groupby(['RPS', 'Service']).agg({
        'CPU_Total(m)': 'mean',
        'Net_RX(KB/s)': 'mean',
        'Net_TX(KB/s)': 'mean'
    }).reset_index()
    
    rps_totals = svc_avg.groupby('RPS').agg({
        'CPU_Total(m)': 'sum',
        'Net_RX(KB/s)': 'sum',
        'Net_TX(KB/s)': 'sum'
    }).reset_index()
    
    rps_summary = []
    for rps in sorted(rps_totals['RPS'].unique()):
        rps_row = rps_totals[rps_totals['RPS'] == rps].iloc[0]
        
        row = {
            'RPS': rps,
            'Total_CPU_m': round(rps_row['CPU_Total(m)'], 0),
            'Avg_CPU_per_Pod': round(rps_row['CPU_Total(m)'] / len(app_df['Service'].unique()), 1),
            'Total_Net_RX': round(rps_row['Net_RX(KB/s)'], 1),
            'Total_Net_TX': round(rps_row['Net_TX(KB/s)'], 1),
            'Is_Saturated': rps >= saturation_rps if saturation_rps else False
        }
        
        if not df_lat.empty:
            # Latency도 평균 사용 (여러 번 측정했을 수 있음)
            lat_rows = df_lat[df_lat['Target_RPS'] == rps]
            if not lat_rows.empty:
                if 'P50_Latency_ms' in lat_rows.columns:
                    row['P50_ms'] = round(lat_rows['P50_Latency_ms'].mean(), 2)
                if 'P99_Latency_ms' in lat_rows.columns:
                    row['P99_ms'] = round(lat_rows['P99_Latency_ms'].mean(), 2)
                if 'Error_Rate(%)' in lat_rows.columns:
                    row['Error_Rate'] = round(lat_rows['Error_Rate(%)'].mean(), 4)
        
        rps_summary.append(row)
    
    summary_df = pd.DataFrame(rps_summary)
    summary_df.to_csv(f"{output_prefix}baseline_summary.csv", index=False)
    print(f"✓ Saved: {output_prefix}baseline_summary.csv")
    
    return summary_df

def print_summary_report(df: pd.DataFrame, df_lat: pd.DataFrame, summary_df: pd.DataFrame = None, saturation_rps: int = None):
    """콘솔 요약 리포트"""
    print("\n" + "="*70)
    print("EXPERIMENT SUMMARY REPORT")
    print("="*70)
    
    if saturation_rps:
        print(f"\n⚠️  Saturation detected at {saturation_rps} RPS")
    
    # ★ 반복 측정 고려: Service별 평균 → Category별 합계
    svc_avg = df.groupby(['Category', 'Service']).agg({
        'CPU_Total(m)': 'mean',
        'Memory_WorkingSet(Mi)': 'mean',
        'Net_RX(KB/s)': 'mean'
    }).reset_index()
    
    cat_totals = svc_avg.groupby('Category').agg({
        'CPU_Total(m)': 'sum',
        'Memory_WorkingSet(Mi)': 'sum',
        'Net_RX(KB/s)': 'sum'
    }).round(1)
    
    print("\n[Resource Usage by Category (averaged across repetitions)]")
    print(cat_totals.to_string())
    
    if df['CPU_Sidecar(m)'].sum() > 0:
        print("\n[Istio Overhead]")
        app_df = df[df['Category'] == 'application']
        svc_avg_istio = app_df.groupby('Service').agg({
            'CPU_App(m)': 'mean',
            'CPU_Sidecar(m)': 'mean'
        }).reset_index()
        
        total_app_cpu = svc_avg_istio['CPU_App(m)'].sum()
        total_sidecar_cpu = svc_avg_istio['CPU_Sidecar(m)'].sum()
        
        cp_df = df[df['Category'] == 'istio-control-plane']
        if not cp_df.empty:
            cp_svc_avg = cp_df.groupby('Service')['CPU_Total(m)'].mean()
            total_cp_cpu = cp_svc_avg.sum()
        else:
            total_cp_cpu = 0
        
        print(f"  App CPU:          {total_app_cpu:>10.0f} m")
        print(f"  Sidecar CPU:      {total_sidecar_cpu:>10.0f} m ({total_sidecar_cpu/max(total_app_cpu,1)*100:.1f}%)")
        print(f"  Control Plane:    {total_cp_cpu:>10.0f} m")
    
    if not df_lat.empty and 'P99_Latency_ms' in df_lat.columns:
        print("\n[Latency Summary]")
        print(f"  Max P99: {df_lat['P99_Latency_ms'].max():.2f} ms")
        if 'Error_Rate(%)' in df_lat.columns:
            print(f"  Max Error Rate: {df_lat['Error_Rate(%)'].max():.2f}%")
    
    if summary_df is not None:
        print("\n[Per-RPS Summary]")
        print(summary_df.to_string(index=False))
    
    print("\n" + "="*70)

# ============================================================
# 메인
# ============================================================
def main():
    metrics_file = sys.argv[1] if len(sys.argv) > 1 else "k8s_full_metrics.csv"
    latency_file = sys.argv[2] if len(sys.argv) > 2 else "latency_stats.csv"
    output_prefix = sys.argv[3] if len(sys.argv) > 3 else ""
    
    print("Loading data...")
    df = load_metrics(metrics_file)
    df_lat = load_latency(latency_file)
    
    if df is None:
        print("Cannot proceed without metrics data")
        sys.exit(1)
    
    print(f"Loaded {len(df)} records")
    print(f"  Categories: {df['Category'].unique().tolist()}")
    print(f"  RPS values: {sorted(df['RPS'].unique().tolist())}")
    
    # 포화 지점 감지
    saturation_rps = detect_saturation_point(df, df_lat)
    if saturation_rps:
        print(f"\n⚠️  Saturation detected at {saturation_rps} RPS")
    
    print("\n[1/7] Generating category overview...")
    plot_category_overview(df, output_prefix, saturation_rps)
    
    print("[2/7] Generating normal range graphs...")
    plot_category_normal_range(df, output_prefix, saturation_rps)
    
    print("[3/7] Generating Istio overhead detail...")
    plot_istio_overhead_detail(df, output_prefix)
    
    print("[4/7] Generating service breakdown...")
    plot_service_breakdown(df, output_prefix)
    
    print("[5/7] Generating latency analysis...")
    if not df_lat.empty:
        plot_latency_analysis(df_lat, output_prefix, saturation_rps)
    else:
        print("  Skipped (no latency data)")
    
    print("[6/7] Generating efficiency analysis...")
    plot_efficiency_analysis(df, output_prefix)
    
    print("[7/7] Generating scalability analysis...")
    plot_scalability_analysis(df, df_lat, output_prefix, saturation_rps)
    
    summary_df = generate_baseline_summary(df, df_lat, output_prefix, saturation_rps)
    print_summary_report(df, df_lat, summary_df, saturation_rps)
    
    print("\n✅ All visualizations complete!")

if __name__ == "__main__":
    main()