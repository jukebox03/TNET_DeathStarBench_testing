#!/usr/bin/env python3
"""
Istio vs No-Istio 비교 분석 스크립트 v2
- 현재 measure_step.py / plot_results.py 구조에 맞춤
- Category별 분석 (application, istio-control-plane, kubernetes-system)
- Sidecar 오버헤드 상세 분석
- 자동 파일 경로 탐지
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import os
import sys
import glob

plt.rcParams['figure.dpi'] = 120
plt.rcParams['savefig.dpi'] = 150
plt.rcParams['font.size'] = 10
sns.set_style("whitegrid")

# ============================================================
# 데이터 로딩
# ============================================================
def find_latest_results(base_dir="results"):
    """최신 no_istio / with_istio 결과 디렉토리 자동 탐지"""
    no_istio_dirs = sorted(glob.glob(f"{base_dir}/no_istio_*"))
    with_istio_dirs = sorted(glob.glob(f"{base_dir}/with_istio_*"))
    
    latest_no_istio = no_istio_dirs[-1] if no_istio_dirs else None
    latest_with_istio = with_istio_dirs[-1] if with_istio_dirs else None
    
    return latest_no_istio, latest_with_istio

def load_metrics(filepath: str) -> pd.DataFrame:
    """메트릭 CSV 로드 및 정규화"""
    if not os.path.exists(filepath):
        return None
    
    df = pd.read_csv(filepath)
    
    # 컬럼명 정규화
    column_mapping = {
        'CPU(m)': 'CPU_Total(m)',
        'Memory(Mi)': 'Memory_WorkingSet(Mi)',
    }
    df.rename(columns=column_mapping, inplace=True)
    
    # 필수 컬럼 기본값
    if 'Category' not in df.columns:
        df['Category'] = 'application'
    if 'CPU_App(m)' not in df.columns:
        df['CPU_App(m)'] = df['CPU_Total(m)']
    if 'CPU_Sidecar(m)' not in df.columns:
        df['CPU_Sidecar(m)'] = 0
    
    return df

def load_latency(filepath: str) -> pd.DataFrame:
    """Latency CSV 로드 및 정규화"""
    if not os.path.exists(filepath):
        return pd.DataFrame()
    
    df = pd.read_csv(filepath)
    
    # 컬럼명 정규화
    if 'RPS' in df.columns and 'Target_RPS' not in df.columns:
        df.rename(columns={'RPS': 'Target_RPS'}, inplace=True)
    
    # Latency 파싱
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
# 오버헤드 계산
# ============================================================
def calculate_overhead(df_no_istio: pd.DataFrame, df_with_istio: pd.DataFrame) -> pd.DataFrame:
    """서비스별 오버헤드 계산"""
    
    # Application만 비교 (kubernetes-system은 제외)
    app_no = df_no_istio[df_no_istio['Category'] == 'application'].copy()
    app_with = df_with_istio[df_with_istio['Category'] == 'application'].copy()
    
    # RPS별 서비스별 평균
    grouped_no = app_no.groupby(['RPS', 'Service']).agg({
        'CPU_Total(m)': 'mean',
        'Memory_WorkingSet(Mi)': 'mean',
        'Net_RX(KB/s)': 'mean',
        'Net_TX(KB/s)': 'mean'
    }).reset_index()
    
    grouped_with = app_with.groupby(['RPS', 'Service']).agg({
        'CPU_Total(m)': 'mean',
        'CPU_App(m)': 'mean',
        'CPU_Sidecar(m)': 'mean',
        'Memory_WorkingSet(Mi)': 'mean',
        'Net_RX(KB/s)': 'mean',
        'Net_TX(KB/s)': 'mean'
    }).reset_index()
    
    # 오버헤드 계산
    overhead_data = []
    
    for rps in grouped_no['RPS'].unique():
        for service in grouped_no['Service'].unique():
            no_row = grouped_no[(grouped_no['RPS'] == rps) & (grouped_no['Service'] == service)]
            with_row = grouped_with[(grouped_with['RPS'] == rps) & (grouped_with['Service'] == service)]
            
            if len(no_row) == 0 or len(with_row) == 0:
                continue
            
            no_cpu = no_row['CPU_Total(m)'].values[0]
            with_cpu = with_row['CPU_Total(m)'].values[0]
            with_app_cpu = with_row['CPU_App(m)'].values[0]
            with_sidecar_cpu = with_row['CPU_Sidecar(m)'].values[0]
            
            no_mem = no_row['Memory_WorkingSet(Mi)'].values[0]
            with_mem = with_row['Memory_WorkingSet(Mi)'].values[0]
            
            no_net = no_row['Net_RX(KB/s)'].values[0] + no_row['Net_TX(KB/s)'].values[0]
            with_net = with_row['Net_RX(KB/s)'].values[0] + with_row['Net_TX(KB/s)'].values[0]
            
            cpu_overhead = with_cpu - no_cpu
            cpu_overhead_pct = (cpu_overhead / no_cpu * 100) if no_cpu > 0 else 0
            
            overhead_data.append({
                'RPS': rps,
                'Service': service,
                'No_Istio_CPU': no_cpu,
                'With_Istio_CPU': with_cpu,
                'App_CPU': with_app_cpu,
                'Sidecar_CPU': with_sidecar_cpu,
                'CPU_Overhead(m)': cpu_overhead,
                'CPU_Overhead(%)': cpu_overhead_pct,
                'Sidecar_Ratio(%)': (with_sidecar_cpu / with_cpu * 100) if with_cpu > 0 else 0,
                'Memory_Overhead(Mi)': with_mem - no_mem,
                'Network_Overhead(KB/s)': with_net - no_net
            })
    
    return pd.DataFrame(overhead_data)

def calculate_category_overhead(df_no_istio: pd.DataFrame, df_with_istio: pd.DataFrame) -> pd.DataFrame:
    """카테고리별 총 오버헤드 계산"""
    
    # No Istio (application + kubernetes-system만)
    cat_no = df_no_istio.groupby(['RPS', 'Category']).agg({
        'CPU_Total(m)': 'sum',
        'Memory_WorkingSet(Mi)': 'sum'
    }).reset_index()
    cat_no['Environment'] = 'No Istio'
    
    # With Istio (application + istio-control-plane + kubernetes-system)
    cat_with = df_with_istio.groupby(['RPS', 'Category']).agg({
        'CPU_Total(m)': 'sum',
        'CPU_Sidecar(m)': 'sum',
        'Memory_WorkingSet(Mi)': 'sum'
    }).reset_index()
    cat_with['Environment'] = 'With Istio'
    
    return cat_no, cat_with

# ============================================================
# 시각화
# ============================================================
def plot_comparison(df_no: pd.DataFrame, df_with: pd.DataFrame, 
                   lat_no: pd.DataFrame, lat_with: pd.DataFrame,
                   overhead_df: pd.DataFrame, output_prefix: str = ""):
    """비교 그래프 생성"""
    
    fig, axes = plt.subplots(2, 3, figsize=(20, 12))
    
    palette = {'No Istio': '#2ecc71', 'With Istio': '#e74c3c'}
    
    # === [1] 카테고리별 총 CPU 비교 ===
    ax = axes[0, 0]
    
    cat_no, cat_with = calculate_category_overhead(df_no, df_with)
    
    # RPS별 총 CPU
    total_no = cat_no.groupby('RPS')['CPU_Total(m)'].sum().reset_index()
    total_no['Environment'] = 'No Istio'
    total_with = cat_with.groupby('RPS')['CPU_Total(m)'].sum().reset_index()
    total_with['Environment'] = 'With Istio'
    total_combined = pd.concat([total_no, total_with])
    
    sns.lineplot(data=total_combined, x='RPS', y='CPU_Total(m)', 
                 hue='Environment', marker='o', linewidth=2, ax=ax, palette=palette)
    ax.set_title("1. Total Cluster CPU Comparison")
    ax.set_ylabel("CPU (millicores)")
    ax.legend()
    
    # === [2] P99 Latency 비교 ===
    ax = axes[0, 1]
    
    if not lat_no.empty and not lat_with.empty:
        lat_no_copy = lat_no.copy()
        lat_with_copy = lat_with.copy()
        lat_no_copy['Environment'] = 'No Istio'
        lat_with_copy['Environment'] = 'With Istio'
        
        p99_col = 'P99_Latency_ms' if 'P99_Latency_ms' in lat_no_copy.columns else None
        
        if p99_col:
            lat_combined = pd.concat([
                lat_no_copy[['Target_RPS', p99_col, 'Environment']],
                lat_with_copy[['Target_RPS', p99_col, 'Environment']]
            ])
            
            sns.lineplot(data=lat_combined, x='Target_RPS', y=p99_col,
                        hue='Environment', marker='o', linewidth=2, ax=ax, palette=palette)
            ax.set_title("2. P99 Latency Comparison")
            ax.set_xlabel("RPS")
            ax.set_ylabel("P99 Latency (ms)")
            ax.legend()
    else:
        ax.text(0.5, 0.5, 'No latency data', ha='center', va='center', transform=ax.transAxes)
        ax.set_title("2. P99 Latency Comparison")
    
    # === [3] CPU 오버헤드 히트맵 ===
    ax = axes[0, 2]
    
    if not overhead_df.empty:
        # Top 10 서비스만
        top_services = overhead_df.groupby('Service')['CPU_Overhead(%)'].mean().abs().nlargest(10).index
        overhead_top = overhead_df[overhead_df['Service'].isin(top_services)]
        
        pivot = overhead_top.pivot(index='Service', columns='RPS', values='CPU_Overhead(%)')
        sns.heatmap(pivot, annot=True, fmt='.1f', cmap='RdYlGn_r', center=0, ax=ax)
        ax.set_title("3. CPU Overhead % by Service")
    
    # === [4] Sidecar CPU 비율 ===
    ax = axes[1, 0]
    
    if overhead_df['Sidecar_CPU'].sum() > 0:
        sns.boxplot(data=overhead_df, x='RPS', y='Sidecar_Ratio(%)', ax=ax)
        ax.axhline(y=50, color='red', linestyle='--', alpha=0.5, label='50% threshold')
        ax.set_title("4. Sidecar CPU Ratio Distribution")
        ax.set_ylabel("Sidecar CPU / Total CPU (%)")
        ax.legend()
    else:
        ax.text(0.5, 0.5, 'No Sidecar data', ha='center', va='center', transform=ax.transAxes)
        ax.set_title("4. Sidecar CPU Ratio")
    
    # === [5] Istio Control Plane 오버헤드 ===
    ax = axes[1, 1]
    
    istio_cp = df_with[df_with['Category'] == 'istio-control-plane']
    if not istio_cp.empty:
        cp_by_rps = istio_cp.groupby('RPS')['CPU_Total(m)'].sum().reset_index()
        ax.bar(cp_by_rps['RPS'].astype(str), cp_by_rps['CPU_Total(m)'], color='#e74c3c')
        ax.set_title("5. Istio Control Plane CPU")
        ax.set_xlabel("RPS")
        ax.set_ylabel("CPU (millicores)")
    else:
        ax.text(0.5, 0.5, 'No istio-system data\n(Use --all-namespaces)', 
                ha='center', va='center', transform=ax.transAxes)
        ax.set_title("5. Istio Control Plane CPU")
    
    # === [6] 총 오버헤드 요약 ===
    ax = axes[1, 2]
    
    # RPS별 총 오버헤드
    total_overhead = overhead_df.groupby('RPS').agg({
        'CPU_Overhead(m)': 'sum',
        'Sidecar_CPU': 'sum'
    }).reset_index()
    
    # Control Plane 추가
    if not istio_cp.empty:
        cp_cpu = istio_cp.groupby('RPS')['CPU_Total(m)'].sum().reset_index()
        cp_cpu.columns = ['RPS', 'CP_CPU']
        total_overhead = total_overhead.merge(cp_cpu, on='RPS', how='left').fillna(0)
    else:
        total_overhead['CP_CPU'] = 0
    
    x = np.arange(len(total_overhead))
    width = 0.25
    
    ax.bar(x - width, total_overhead['Sidecar_CPU'], width, label='All Sidecars', color='#f39c12')
    ax.bar(x, total_overhead['CP_CPU'], width, label='Control Plane', color='#e74c3c')
    ax.bar(x + width, total_overhead['CPU_Overhead(m)'], width, label='Total Overhead', color='#3498db')
    
    ax.set_xticks(x)
    ax.set_xticklabels(total_overhead['RPS'])
    ax.set_title("6. Istio Overhead Breakdown")
    ax.set_xlabel("RPS")
    ax.set_ylabel("CPU (millicores)")
    ax.legend()
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}istio_comparison.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}istio_comparison.png")
    plt.close()

def plot_detailed_service_comparison(df_no: pd.DataFrame, df_with: pd.DataFrame,
                                     overhead_df: pd.DataFrame, output_prefix: str = ""):
    """서비스별 상세 비교"""
    
    # Top 5 오버헤드 서비스
    top5_services = overhead_df.groupby('Service')['CPU_Overhead(%)'].mean().abs().nlargest(5).index
    
    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    axes = axes.flatten()
    
    palette = {'No Istio': '#2ecc71', 'With Istio': '#e74c3c'}
    
    for idx, service in enumerate(top5_services):
        if idx >= 5:
            break
        
        ax = axes[idx]
        
        # No Istio
        svc_no = df_no[(df_no['Service'] == service) & (df_no['Category'] == 'application')]
        svc_no_avg = svc_no.groupby('RPS')['CPU_Total(m)'].mean().reset_index()
        svc_no_avg['Environment'] = 'No Istio'
        
        # With Istio
        svc_with = df_with[(df_with['Service'] == service) & (df_with['Category'] == 'application')]
        svc_with_avg = svc_with.groupby('RPS').agg({
            'CPU_App(m)': 'mean',
            'CPU_Sidecar(m)': 'mean',
            'CPU_Total(m)': 'mean'
        }).reset_index()
        
        # 스택 바 차트
        x = np.arange(len(svc_with_avg))
        width = 0.35
        
        # No Istio
        ax.bar(x - width/2, svc_no_avg['CPU_Total(m)'], width, label='No Istio', color='#2ecc71')
        
        # With Istio (App + Sidecar stacked)
        ax.bar(x + width/2, svc_with_avg['CPU_App(m)'], width, label='App (Istio)', color='#3498db')
        ax.bar(x + width/2, svc_with_avg['CPU_Sidecar(m)'], width, 
               bottom=svc_with_avg['CPU_App(m)'], label='Sidecar', color='#e74c3c')
        
        ax.set_xticks(x)
        ax.set_xticklabels(svc_with_avg['RPS'])
        ax.set_title(f"{service}")
        ax.set_xlabel("RPS")
        ax.set_ylabel("CPU (m)")
        
        if idx == 0:
            ax.legend(loc='upper left', fontsize='small')
    
    # 마지막 칸에 요약 텍스트
    ax = axes[5]
    ax.axis('off')
    
    summary_text = "Top 5 Overhead Services\n" + "="*30 + "\n\n"
    for service in top5_services:
        avg_overhead = overhead_df[overhead_df['Service'] == service]['CPU_Overhead(%)'].mean()
        avg_sidecar = overhead_df[overhead_df['Service'] == service]['Sidecar_Ratio(%)'].mean()
        summary_text += f"{service}:\n"
        summary_text += f"  CPU Overhead: {avg_overhead:+.1f}%\n"
        summary_text += f"  Sidecar Ratio: {avg_sidecar:.1f}%\n\n"
    
    ax.text(0.1, 0.9, summary_text, transform=ax.transAxes, fontsize=10,
            verticalalignment='top', fontfamily='monospace',
            bbox=dict(boxstyle='round', facecolor='lightgray', alpha=0.5))
    
    plt.tight_layout()
    plt.savefig(f"{output_prefix}service_comparison_detail.png", bbox_inches='tight')
    print(f"✓ Saved: {output_prefix}service_comparison_detail.png")
    plt.close()

# ============================================================
# 리포트 생성
# ============================================================
def generate_report(df_no: pd.DataFrame, df_with: pd.DataFrame,
                   lat_no: pd.DataFrame, lat_with: pd.DataFrame,
                   overhead_df: pd.DataFrame, output_prefix: str = ""):
    """분석 리포트 생성"""
    
    report = []
    report.append("=" * 70)
    report.append("ISTIO OVERHEAD ANALYSIS REPORT")
    report.append("=" * 70)
    
    # [1] 총 리소스 비교
    report.append("\n[1] Total Resource Comparison")
    report.append("-" * 50)
    
    app_no = df_no[df_no['Category'] == 'application']
    app_with = df_with[df_with['Category'] == 'application']
    
    for rps in sorted(app_no['RPS'].unique()):
        no_cpu = app_no[app_no['RPS'] == rps]['CPU_Total(m)'].sum()
        with_cpu = app_with[app_with['RPS'] == rps]['CPU_Total(m)'].sum()
        overhead = with_cpu - no_cpu
        overhead_pct = (overhead / no_cpu * 100) if no_cpu > 0 else 0
        
        report.append(f"  RPS {rps:5d}: {no_cpu:8.0f}m → {with_cpu:8.0f}m ({overhead:+.0f}m, {overhead_pct:+.1f}%)")
    
    # [2] Sidecar 오버헤드
    report.append("\n[2] Sidecar Overhead Summary")
    report.append("-" * 50)
    
    total_sidecar = overhead_df.groupby('RPS')['Sidecar_CPU'].sum()
    total_app = overhead_df.groupby('RPS')['With_Istio_CPU'].sum()
    
    for rps in sorted(total_sidecar.index):
        sidecar = total_sidecar[rps]
        total = total_app[rps]
        ratio = (sidecar / total * 100) if total > 0 else 0
        report.append(f"  RPS {rps:5d}: Sidecar {sidecar:8.0f}m / Total {total:8.0f}m ({ratio:.1f}%)")
    
    # [3] Control Plane 오버헤드
    istio_cp = df_with[df_with['Category'] == 'istio-control-plane']
    if not istio_cp.empty:
        report.append("\n[3] Control Plane Overhead")
        report.append("-" * 50)
        
        cp_by_rps = istio_cp.groupby('RPS')['CPU_Total(m)'].sum()
        for rps in sorted(cp_by_rps.index):
            report.append(f"  RPS {rps:5d}: {cp_by_rps[rps]:8.0f}m")
    
    # [4] Latency 비교
    if not lat_no.empty and not lat_with.empty:
        report.append("\n[4] Latency Overhead")
        report.append("-" * 50)
        
        p99_col = 'P99_Latency_ms' if 'P99_Latency_ms' in lat_no.columns else None
        
        if p99_col:
            for rps in sorted(lat_no['Target_RPS'].unique()):
                no_lat = lat_no[lat_no['Target_RPS'] == rps][p99_col].mean()
                with_lat = lat_with[lat_with['Target_RPS'] == rps][p99_col].mean()
                diff = with_lat - no_lat
                diff_pct = (diff / no_lat * 100) if no_lat > 0 else 0
                report.append(f"  RPS {rps:5d}: P99 {no_lat:.2f}ms → {with_lat:.2f}ms ({diff:+.2f}ms, {diff_pct:+.1f}%)")
    
    # [5] Top 오버헤드 서비스
    report.append("\n[5] Top 5 Overhead Services (by CPU %)")
    report.append("-" * 50)
    
    avg_overhead = overhead_df.groupby('Service')['CPU_Overhead(%)'].mean().sort_values(ascending=False)
    for service in avg_overhead.head(5).index:
        pct = avg_overhead[service]
        sidecar_ratio = overhead_df[overhead_df['Service'] == service]['Sidecar_Ratio(%)'].mean()
        report.append(f"  {service:30s}: {pct:+6.1f}% (Sidecar: {sidecar_ratio:.1f}%)")
    
    # [6] 최적화 제안
    report.append("\n[6] Optimization Recommendations")
    report.append("-" * 50)
    
    high_overhead = avg_overhead[avg_overhead > 30].index.tolist()
    if high_overhead:
        report.append(f"  ⚠️  High overhead services: {', '.join(high_overhead)}")
        report.append("     → Consider: Sidecar resource limits, mTLS exemption")
    
    high_sidecar = overhead_df.groupby('Service')['Sidecar_Ratio(%)'].mean()
    high_sidecar_services = high_sidecar[high_sidecar > 40].index.tolist()
    if high_sidecar_services:
        report.append(f"  ⚠️  High sidecar ratio: {', '.join(high_sidecar_services)}")
        report.append("     → Consider: Protocol optimization, access log disable")
    
    report.append("\n  General recommendations:")
    report.append("    - Disable access logging: meshConfig.accessLogFile: \"\"")
    report.append("    - Reduce tracing: meshConfig.defaultConfig.tracing.sampling: 1")
    report.append("    - Protocol ports: Explicit port naming (http-xxx, grpc-xxx)")
    
    report.append("\n" + "=" * 70)
    
    report_text = "\n".join(report)
    print(report_text)
    
    report_file = f"{output_prefix}istio_analysis_report.txt"
    with open(report_file, "w") as f:
        f.write(report_text)
    print(f"\n✓ Report saved to '{report_file}'")

def generate_summary_csv(overhead_df: pd.DataFrame, output_prefix: str = ""):
    """비교 요약 CSV 생성"""
    
    summary = overhead_df.groupby('RPS').agg({
        'No_Istio_CPU': 'sum',
        'With_Istio_CPU': 'sum',
        'Sidecar_CPU': 'sum',
        'CPU_Overhead(m)': 'sum',
        'CPU_Overhead(%)': 'mean',
        'Memory_Overhead(Mi)': 'sum'
    }).reset_index()
    
    summary['Sidecar_Ratio(%)'] = summary['Sidecar_CPU'] / summary['With_Istio_CPU'] * 100
    
    summary.to_csv(f"{output_prefix}overhead_summary.csv", index=False)
    print(f"✓ Saved: {output_prefix}overhead_summary.csv")

# ============================================================
# 메인
# ============================================================
def main():
    print("="*60)
    print("Istio vs No-Istio Comparison Analysis")
    print("="*60)
    
    # 인자 파싱
    if len(sys.argv) >= 3:
        no_istio_dir = sys.argv[1]
        with_istio_dir = sys.argv[2]
        output_prefix = sys.argv[3] if len(sys.argv) > 3 else ""
    else:
        # 자동 탐지
        print("\nAuto-detecting result directories...")
        no_istio_dir, with_istio_dir = find_latest_results()
        output_prefix = ""
    
    if not no_istio_dir or not with_istio_dir:
        print("\nError: Could not find result directories.")
        print("\nUsage:")
        print("  python3 compare_istio.py <no_istio_dir> <with_istio_dir> [output_prefix]")
        print("\nExample:")
        print("  python3 compare_istio.py results/no_istio_20251229_111800 results/with_istio_20251229_140000 comparison_")
        return
    
    print(f"\n  No Istio:   {no_istio_dir}")
    print(f"  With Istio: {with_istio_dir}")
    
    # 파일 경로
    no_metrics = f"{no_istio_dir}/k8s_full_metrics.csv"
    with_metrics = f"{with_istio_dir}/k8s_full_metrics.csv"
    no_latency = f"{no_istio_dir}/latency_stats.csv"
    with_latency = f"{with_istio_dir}/latency_stats.csv"
    
    # 데이터 로드
    print("\nLoading data...")
    df_no = load_metrics(no_metrics)
    df_with = load_metrics(with_metrics)
    lat_no = load_latency(no_latency)
    lat_with = load_latency(with_latency)
    
    if df_no is None:
        print(f"Error: {no_metrics} not found")
        return
    if df_with is None:
        print(f"Error: {with_metrics} not found")
        return
    
    print(f"  No Istio:   {len(df_no)} records")
    print(f"  With Istio: {len(df_with)} records")
    
    # 오버헤드 계산
    print("\nCalculating overhead...")
    overhead_df = calculate_overhead(df_no, df_with)
    
    # 그래프 생성
    print("\nGenerating comparison graphs...")
    plot_comparison(df_no, df_with, lat_no, lat_with, overhead_df, output_prefix)
    plot_detailed_service_comparison(df_no, df_with, overhead_df, output_prefix)
    
    # 요약 CSV
    generate_summary_csv(overhead_df, output_prefix)
    
    # 리포트 생성
    print("\nGenerating report...")
    generate_report(df_no, df_with, lat_no, lat_with, overhead_df, output_prefix)
    
    print("\n" + "="*60)
    print("✅ Comparison analysis complete!")
    print("="*60)

if __name__ == "__main__":
    main()