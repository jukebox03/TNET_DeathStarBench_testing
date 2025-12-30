# Istio Performance Analysis on DeathStarBench (HotelReservation)

본 프로젝트는 마이크로서비스 벤치마크인 **DeathStarBench의 HotelReservation** 애플리케이션을 Kubernetes 환경(Minikube)에 배포하여 성능을 분석한 실험입니다.

주요 목표는 **Istio Service Mesh** 도입 전후의 성능(CPU, Memory, Network, Latency)을 비교하고, 부하(RPS) 변화에 따른 병목 지점(Bottleneck)과 오버헤드(Overhead)를 정량적으로 분석하는 것입니다.

## Overview

### 실험 목적

1. **Baseline 측정:** Vanilla Kubernetes 환경에서의 서비스 별 리소스 사용량 및 Latency 측정.
2. **Istio Overhead 분석:** Sidecar Injection 후 동일 부하에서의 성능 저하 및 리소스 사용량 변화 분석.
3. **Bottleneck 파악:** CPU, Memory, Network I/O, Latency(P50, P99) 분석을 통한 병목 서비스 식별.

### 대상 애플리케이션

- **HotelReservation** (DeathStarBench)
- **Workload:** Mixed Workload (wrk2 사용)

## Environment Setup

실험은 **Minikube**를 단일 노드로 구성하여 진행되었습니다.

- **Cluster:** Minikube (Driver: Docker)
- **Resource Limits:**
- **CPUs**: `36`
- **Memory**: `32GB`

- **Additional Components:** Metrics Server enabled

### Service Resource Configuration

실제 배포 환경을 모사하기 위해 DB 및 Cache 서비스 등에는 CPU Limit을 설정하여 실험을 진행했습니다.

```bash
# 리소스 설정 확인 명령어
kubectl get deployments -n default -o custom-columns="NAME:.metadata.name,CPU_REQUEST:.spec.template.spec.containers[].resources.requests.cpu, CPU_LIMIT:.spec.template.spec.containers[].resources.limits.cpu"
```

---

## Experiment Plan

### Step 1. Baseline (No Istio)

- Istio 없이 순수 Kubernetes 환경에서 부하 테스트 진행.
- **RPS:** 200, 400, 600, 700, 800, 1000
- **Metrics:** K8s CPU/Memory/Network, Latency (P50, Mean, P99)

### Step 2. Istio Integration

- Istio 설치 및 `default` 네임스페이스에 Sidecar Injection 활성화 (`istio-injection=enabled`).
- Step 1과 동일한 시나리오로 부하 테스트 재수행.

### Step 3. Overhead Analysis

- Baseline vs Istio 비교 그래프 작성.
- 서비스별 오버헤드 및 병목 구간 시각화.

### Step 4. Optimization (Future Work)

- mTLS, Access Log, Tracing Sampling Rate 조절, Sidecar Resource Limit 최적화 등 튜닝 적용 후 효과 측정.

## Measurement Methodology

정확한 성능 측정을 위해 자체 개발한 Python/Shell 스크립트를 사용합니다. (`run_experiment.sh`)

### 1. Workflow

1. **Verify Prerequisites:** `kubectl proxy`, `wrk` 실행 파일, Target App 상태 확인.
2. **Run Single Test (90s):**
- **0~15s (Warmup):** 데이터 불안정 구간 제외.
- **15~45s (Measure):** `measure_step.py`를 통해 메트릭 수집 (60초간).
- **45~60s (Cooldown):** 다음 측정을 위한 대기.


3. **Parse & Aggregate:** `wrk` 로그 파싱 및 통계 산출.

### 2. Metric Collection Logic (`measure_step.py`)

Kubernetes API (`http://127.0.0.1:8001`)를 통해 Node 및 Pod 메트릭을 수집합니다.

| Metric | Source | Method | Note |
| --- | --- | --- | --- |
| **CPU** | `/stats/summary` | `usageCoreNanoSeconds` | 누적값 사용 (Delta 계산). Container별(App vs Istio-proxy) 구분 측정. |
| **Memory** | `/stats/summary` | `workingSetBytes` | 현재값(Snapshot) 사용. (Limit 도달 시 OOM Killer 기준이 되는 값) |
| **Network** | `/proc/net/dev` | `kubectl exec` | `cat /proc/net/dev` 명령어를 병렬 실행하여 누적 트래픽(Delta) 계산. |

---

## Usage

### 1. Prerequisites & Cluster Setup

**Minikube 시작**

```bash
minikube start --driver=docker --cpus=36 --memory=32768 --addons=metrics-server
```

**Application 배포 (Baseline)**

```bash
kubectl apply -R -f kubernetes/
watch -n 1 kubectl get pods
```

**K8s Proxy 실행 (데이터 수집용)**
별도의 터미널에서 실행 유지 필요:

```bash
kubectl proxy
```

### 2. Istio Setup (For Step 2)

**Istio 설치 및 Sidecar Injection**

```bash
istioctl install --set profile=default -y
kubectl label namespace default istio-injection=enabled
# 기존 Pod 재배포하여 Sidecar 주입
kubectl get deployments -n default -o name | xargs kubectl rollout restart -n default
```

**Istio 제거 (Reset to Baseline)**

```bash
kubectl label namespace default istio-injection-
kubectl rollout restart deployment -n default
istioctl uninstall --purge -y
kubectl delete namespace istio-system
```

### 3. Running Experiments

**Baseline 실험 실행**

```bash
bash run_experiment.sh --all-namespaces
```

**Istio 환경 실험 실행**

```bash
bash run_experiment.sh --istio --all-namespaces
```

### 4. Result Plotting

실험 결과(CSV)를 시각화합니다.

```bash
# Baseline Plot
python3 plot_results.py results/no_istio_<timestamp>/k8s_full_metrics.csv \
results/no_istio_<timestamp>/latency_stats.csv \
no_istio_

# Istio Plot
python3 plot_results.py results/with_istio_<timestamp>/k8s_full_metrics.csv \
results/with_istio_<timestamp>/latency_stats.csv \
with_istio_

# Compare
python3 compare_istio.py
```

## Trouble Shooting & Utils

* **Manual Request Test (wrk2):**
```bash
./wrk -D exp -t 4 -c 100 -d 60s -L \
-s ./wrk2/scripts/hotel-reservation/mixed-workload_type_1.lua \
http://192.168.49.2:30918 -R 2000
```
- `-R`: Throughput Goal (RPS)
- `-c`: Connections
- `-d`: Duration

### Notes on Metrics

- **Memory Metric:** Kubernetes API에는 메모리 사용량의 '누적값'이 존재하지 않습니다. 따라서 `workingSetBytes` 스냅샷을 주기적으로 폴링하여 기록합니다. `usageBytes`와 달리 `workingSetBytes`는 Inactive File Cache를 포함하지 않아 OOM 판단의 기준이 됩니다.
- **Network Metric:** `metrics-server`나 기본 API에서 Pod 레벨의 정확한 RX/TX 누적값을 얻기 어려워, `kubectl exec`를 통해 컨테이너 내부의 `/proc/net/dev`를 직접 읽는 방식을 사용했습니다.