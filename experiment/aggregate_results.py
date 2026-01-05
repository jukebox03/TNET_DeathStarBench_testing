#!/usr/bin/env python3
"""
aggregate_results.py v2.0
- Compatible with measure_step.py v14.0
- Aggregates per-pod metrics into Cluster Totals.
- Summarizes Node-level metrics (Disk Latency, Mem BW, Max Core Util).
"""

import csv
import sys
import os
from collections import defaultdict
from typing import List, Dict

INPUT_FILE = "k8s_full_metrics.csv"
OUTPUT_FILE = "final_summary.csv"

def load_data(filename):
    if not os.path.exists(filename):
        print(f"Error: {filename} not found.")
        sys.exit(1)
        
    with open(filename, 'r') as f:
        reader = csv.DictReader(f)
        data = list(reader)
    return data

def aggregate_by_experiment(data: List[Dict]):
    # Group by (Timestamp, RPS) -> This identifies a single experiment run
    experiments = defaultdict(list)
    for row in data:
        key = (row['Timestamp'], row['RPS'])
        experiments[key].append(row)
    
    summary_list = []
    
    for (ts, rps), rows in experiments.items():
        # 1. Sum up Pod Metrics (Cluster Total)
        total_cpu_m = 0
        total_mem_mi = 0
        total_disk_mbps = 0.0
        
        # 2. Node Metrics (Same for all rows in this experiment, just take the first one)
        # Note: Handle missing values safely
        try:
            first_row = rows[0]
            node_disk_r_lat = float(first_row.get('Node_Disk_Lat_R(ms)', 0))
            node_disk_w_lat = float(first_row.get('Node_Disk_Lat_W(ms)', 0))
            node_disk_util = float(first_row.get('Node_Disk_Util(%)', 0))
            mem_bw_pct = float(first_row.get('Mem_BW(%)', 0))
        except ValueError:
            continue

        # 3. Core Metrics Analysis
        # Find the max core usage across all cores in this run
        max_core_util = 0.0
        hottest_core_id = ""
        
        # Extract Core_XX columns from the first row
        core_cols = [k for k in first_row.keys() if k.startswith('Core_')]
        for col in core_cols:
            try:
                val = float(first_row.get(col, 0))
                if val > max_core_util:
                    max_core_util = val
                    hottest_core_id = col
            except ValueError:
                pass

        # Summing Pod Metrics
        for row in rows:
            try:
                total_cpu_m += float(row.get('CPU(m)', 0))
                total_mem_mi += float(row.get('Mem(Mi)', 0))
                total_disk_mbps += float(row.get('Disk(MB/s)', 0))
            except ValueError:
                pass
        
        summary = {
            'Timestamp': ts,
            'RPS': int(rps),
            'Total_CPU(m)': int(total_cpu_m),
            'Total_Mem(Mi)': int(total_mem_mi),
            'Total_Disk(MB/s)': round(total_disk_mbps, 2),
            'Disk_Lat_R(ms)': node_disk_r_lat,
            'Disk_Lat_W(ms)': node_disk_w_lat,
            'Disk_Util(%)': node_disk_util,
            'Mem_BW(%)': mem_bw_pct,
            'Max_Core_Util(%)': max_core_util,
            'Hottest_Core': hottest_core_id
        }
        summary_list.append(summary)
        
    # Sort by RPS for better viewing
    summary_list.sort(key=lambda x: x['RPS'])
    return summary_list

def print_table(summary_list):
    print("\n" + "="*100)
    print(f"{'RPS':<8} | {'CPU(m)':<10} | {'Mem(Mi)':<10} | {'Disk(MB/s)':<12} | {'Lat R/W(ms)':<15} | {'MemBW%':<8} | {'MaxCore%':<10}")
    print("-" * 100)
    
    for s in summary_list:
        lat_str = f"{s['Disk_Lat_R(ms)']}/{s['Disk_Lat_W(ms)']}"
        print(f"{s['RPS']:<8} | {s['Total_CPU(m)']:<10} | {s['Total_Mem(Mi)']:<10} | {s['Total_Disk(MB/s)']:<12} | {lat_str:<15} | {s['Mem_BW(%)']:<8} | {s['Max_Core_Util(%)']:<10}")
    print("="*100 + "\n")

def save_csv(summary_list):
    if not summary_list: return
    
    keys = summary_list[0].keys()
    with open(OUTPUT_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(summary_list)
    print(f" [INFO] Aggregated summary saved to {OUTPUT_FILE}")

def main():
    print(f"Reading {INPUT_FILE}...")
    data = load_data(INPUT_FILE)
    
    summary = aggregate_by_experiment(data)
    
    print_table(summary)
    save_csv(summary)

if __name__ == "__main__":
    main()