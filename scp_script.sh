#!/bin/bash

SERVER="rapids4.snu.ac.kr"
PORT="12789"
USER="jukebox"
REMOTE_PATH="/home/jukebox/DeathStarBench/DeathStarBench/wrk2"
SOCKET="/tmp/ssh_mux_$SERVER_$PORT"

mkdir -p ./code ./no_istio ./with_istio

echo "Connecting to $SERVER..."
echo "Enter password once to establish master connection."

ssh -M -S "$SOCKET" -f -N -p "$PORT" "$USER@$SERVER"

if [ $? -ne 0 ]; then
    echo "❌ Error: Could not establish connection."
    exit 1
fi

echo "------------------------------------------------------"

echo "[1/3] Downloading script files..."
if scp -o ControlPath="$SOCKET" -P "$PORT" "$USER@$SERVER:$REMOTE_PATH/{compare_istio.py,plot_results.py,parse_wrk.py,measure_step.py,run_experiment.sh,aggregate_results.py}" ./code; then
    echo "✅ Success: Script files downloaded."
else
    echo "❌ Error: Failed to download script files."
fi

echo "------------------------------------------------------"

echo "[2/3] Downloading No-Istio analysis images..."
if scp -o ControlPath="$SOCKET" -P "$PORT" "$USER@$SERVER:$REMOTE_PATH/{no_istio_category_normal_range.png,no_istio_category_overview.png,no_istio_istio_overhead_detail.png,no_istio_latency_analysis.png,no_istio_service_breakdown.png,no_istio_efficiency_analysis.png,no_istio_scalability_analysis.png,no_istio_baseline_summary.csv}" ./no_istio; then
    echo "✅ Success: No-Istio images downloaded."
else
    echo "❌ Error: Failed to download No-Istio images."
fi

echo "------------------------------------------------------"

echo "[3/3] Downloading With-Istio analysis images..."
if scp -o ControlPath="$SOCKET" -P "$PORT" "$USER@$SERVER:$REMOTE_PATH/{with_istio_category_overview.png,with_istio_istio_overhead_detail.png,with_istio_latency_analysis.png,with_istio_service_breakdown.png,with_istio_efficiency_analysis.png,with_istio_scalability_analysis.png,with_istio_baseline_summary.csv}" ./with_istio; then
    echo "✅ Success: With-Istio images downloaded."
else
    echo "❌ Error: Failed to download With-Istio images."
fi

echo "------------------------------------------------------"
echo "Closing master connection..."
ssh -S "$SOCKET" -O exit "$USER@$SERVER" > /dev/null 2>&1

echo "Job Finished."