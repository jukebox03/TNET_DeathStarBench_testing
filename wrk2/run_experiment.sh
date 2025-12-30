#!/bin/bash
# ============================================================
# DeathStarBench 실험 실행 스크립트 v2
# - 다중 namespace 지원 (istio-system, kube-system 포함)
# - 반복 실험 지원
# - Istio 모드 지원
# ============================================================

set -e

# ============================================================
# 설정
# ============================================================
RATES=(200 400 600 800 1000)
DURATION="60s"
WARMUP_TIME=15
COOLDOWN_TIME=15
MEASURE_DURATION=30
REPETITIONS=3

TARGET="http://192.168.49.2:30918"
SCRIPT_PATH="/home/jukebox/DeathStarBench/DeathStarBench/hotelReservation/wrk2/scripts/hotel-reservation/mixed-workload_type_1.lua"
WRK_PATH="./wrk"

# ============================================================
# 인자 파싱
# ============================================================
ISTIO_MODE=""
ALL_NS_MODE=""
EXPERIMENT_NAME="no_istio"

for arg in "$@"; do
    case $arg in
        --istio)
            ISTIO_MODE="--istio"
            EXPERIMENT_NAME="with_istio"
            ;;
        --all-namespaces)
            ALL_NS_MODE="--all-namespaces"
            ;;
    esac
done

# 결과 디렉토리
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULT_DIR="results/${EXPERIMENT_NAME}_${TIMESTAMP}"

# ============================================================
# 유틸리티 함수
# ============================================================
log_info() {
    echo "[$(date '+%H:%M:%S')] [INFO] $1"
}

log_warn() {
    echo "[$(date '+%H:%M:%S')] [WARN] $1"
}

log_error() {
    echo "[$(date '+%H:%M:%S')] [ERROR] $1" >&2
}

# ============================================================
# 사전 검증
# ============================================================
verify_prerequisites() {
    log_info "Verifying prerequisites..."
    
    if ! curl -s http://127.0.0.1:8001/api/v1/nodes > /dev/null 2>&1; then
        log_error "kubectl proxy is not running!"
        log_info "Run: kubectl proxy &"
        exit 1
    fi
    log_info "  ✓ kubectl proxy is running"
    
    if [ ! -x "$WRK_PATH" ]; then
        log_error "wrk not found at $WRK_PATH"
        exit 1
    fi
    log_info "  ✓ wrk found"
    
    if [ ! -f "$SCRIPT_PATH" ]; then
        log_error "Lua script not found: $SCRIPT_PATH"
        exit 1
    fi
    log_info "  ✓ Lua script found"
    
    if ! curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$TARGET" | grep -q "200\|301\|302"; then
        log_warn "Target may not be responding: $TARGET"
        read -p "Continue anyway? (y/N): " confirm
        if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
            exit 1
        fi
    else
        log_info "  ✓ Target is reachable"
    fi
    
    python3 -c "import requests, csv" 2>/dev/null || {
        log_error "Python dependencies missing. Run: pip install requests"
        exit 1
    }
    log_info "  ✓ Python dependencies OK"
    
    # Istio 확인 (--istio 모드일 때)
    if [ -n "$ISTIO_MODE" ]; then
        if kubectl get namespace istio-system > /dev/null 2>&1; then
            log_info "  ✓ istio-system namespace exists"
            
            # istiod 확인
            if kubectl get pods -n istio-system -l app=istiod --no-headers 2>/dev/null | grep -q Running; then
                log_info "  ✓ istiod is running"
            else
                log_warn "  istiod may not be running properly"
            fi
        else
            log_warn "  istio-system namespace not found (running with --istio flag)"
        fi
    fi
}

# ============================================================
# Pre-warmup (Cold Start 방지)
# ============================================================
pre_warmup() {
    log_info "Pre-warming the system (30s)..."
    
    # 낮은 부하로 30초 동안 시스템 깨우기
    $WRK_PATH -t 2 -c 10 -d 30s -R 50 "$TARGET" > /dev/null 2>&1
    
    sleep 5
    log_info "Pre-warmup completed"
}

# ============================================================
# 메타데이터 저장
# ============================================================
save_metadata() {
    local metadata_file="$RESULT_DIR/metadata.json"
    
    # istio-system pods 수 (있으면)
    local istio_pods=0
    if kubectl get namespace istio-system > /dev/null 2>&1; then
        istio_pods=$(kubectl get pods -n istio-system --no-headers 2>/dev/null | wc -l || echo 0)
    fi
    
    cat > "$metadata_file" << EOF
{
    "experiment_name": "$EXPERIMENT_NAME",
    "timestamp": "$TIMESTAMP",
    "rates": [$(IFS=,; echo "${RATES[*]}")],
    "duration": "$DURATION",
    "warmup_time": $WARMUP_TIME,
    "cooldown_time": $COOLDOWN_TIME,
    "measure_duration": $MEASURE_DURATION,
    "repetitions": $REPETITIONS,
    "target": "$TARGET",
    "istio_enabled": $([ -n "$ISTIO_MODE" ] && echo "true" || echo "false"),
    "all_namespaces": $([ -n "$ALL_NS_MODE" ] && echo "true" || echo "false"),
    "node_info": "$(kubectl get nodes -o jsonpath='{.items[0].status.nodeInfo.kubeletVersion}' 2>/dev/null || echo 'N/A')",
    "default_pod_count": $(kubectl get pods -n default --no-headers 2>/dev/null | wc -l || echo 0),
    "istio_system_pod_count": $istio_pods,
    "kube_system_pod_count": $(kubectl get pods -n kube-system --no-headers 2>/dev/null | wc -l || echo 0)
}
EOF
    log_info "Metadata saved to $metadata_file"
}

# ============================================================
# 단일 테스트 실행
# ============================================================
run_single_test() {
    local rps=$1
    local rep=$2
    local wrk_log="$RESULT_DIR/wrk_logs/wrk_${rps}rps_rep${rep}.log"
    
    log_info "Starting test: ${rps} RPS (repetition $rep/$REPETITIONS)"
    
    # wrk 실행
    $WRK_PATH -D exp -t 4 -c 100 -d $DURATION -L -s "$SCRIPT_PATH" "$TARGET" -R $rps > "$wrk_log" 2>&1 &
    local wrk_pid=$!
    
    # Warmup
    log_info "  Warming up for ${WARMUP_TIME}s..."
    sleep $WARMUP_TIME
    
    # 메트릭 수집 (namespace 옵션 전달)
    log_info "  Measuring for ${MEASURE_DURATION}s..."
    python3 measure_step.py $rps $ISTIO_MODE $ALL_NS_MODE --duration=$MEASURE_DURATION
    
    # wrk 종료 대기
    wait $wrk_pid || true
    log_info "  Load generation completed"
    
    # Latency 파싱
    python3 parse_wrk.py $rps "$wrk_log"
    
    # Cooldown
    log_info "  Cooling down for ${COOLDOWN_TIME}s..."
    sleep $COOLDOWN_TIME
}

# ============================================================
# 결과 집계
# ============================================================
aggregate_results() {
    log_info "Aggregating results..."
    python3 aggregate_results.py
}

# ============================================================
# 메인
# ============================================================
main() {
    echo "=========================================="
    echo " DeathStarBench Experiment Runner v2"
    echo " Mode: $EXPERIMENT_NAME"
    [ -n "$ALL_NS_MODE" ] && echo " Namespaces: default, istio-system, kube-system"
    echo "=========================================="
    
    verify_prerequisites

    pre_warmup
    
    mkdir -p "$RESULT_DIR/wrk_logs"
    log_info "Results will be saved to: $RESULT_DIR"
    
    save_metadata
    
    rm -f k8s_full_metrics.csv latency_stats.csv collection_failures.log
    
    for rep in $(seq 1 $REPETITIONS); do
        echo ""
        echo "=========================================="
        echo " Repetition $rep / $REPETITIONS"
        echo "=========================================="
        
        for rps in "${RATES[@]}"; do
            echo "------------------------------------------"
            run_single_test $rps $rep
        done
    done
    
    aggregate_results
    
    mv -f k8s_full_metrics.csv "$RESULT_DIR/" 2>/dev/null || true
    mv -f latency_stats.csv "$RESULT_DIR/" 2>/dev/null || true
    mv -f metrics_summary.csv "$RESULT_DIR/" 2>/dev/null || true
    mv -f latency_summary.csv "$RESULT_DIR/" 2>/dev/null || true
    mv -f collection_failures.log "$RESULT_DIR/" 2>/dev/null || true
    
    echo ""
    echo "=========================================="
    echo " Experiment Complete!"
    echo " Results: $RESULT_DIR"
    echo "=========================================="
}

main "$@"