#!/bin/bash

SERVER="rapids4.snu.ac.kr"
PORT="12789"
USER="jukebox"
REMOTE_PATH="/home/jukebox/DeathStarBench/DeathStarBench/experiment"
SOCKET="/tmp/ssh_mux_$SERVER_$PORT"

mkdir -p ./code

echo "Connecting to $SERVER..."
echo "Enter password once to establish master connection."

ssh -M -S "$SOCKET" -f -N -p "$PORT" "$USER@$SERVER"

if [ $? -ne 0 ]; then
    echo "❌ Error: Could not establish connection."
    exit 1
fi

echo "------------------------------------------------------"

echo "[1/2] Downloading script files..."
if scp -o ControlPath="$SOCKET" -P "$PORT" "$USER@$SERVER:$REMOTE_PATH/"{compare_istio.py,plot_results.py,parse_wrk.py,measure_step.py,run_experiment.sh,aggregate_results.py,collect_jaeger_trace.py,README.md} ./code; then
    echo "✅ Success: Script files downloaded."
else
    echo "❌ Error: Failed to download script files."
fi

echo "------------------------------------------------------"

echo "[2/2] Downloading results..."
if scp -r -o ControlPath="$SOCKET" -P "$PORT" "$USER@$SERVER:$REMOTE_PATH/results" .; then
    echo "✅ Success: results downloaded."
else
    echo "❌ Error: Failed to download results."
fi

echo "------------------------------------------------------"
echo "Closing master connection..."
ssh -S "$SOCKET" -O exit "$USER@$SERVER" > /dev/null 2>&1

echo "Job Finished."