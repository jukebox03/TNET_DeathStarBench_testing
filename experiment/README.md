# DeathStarBench: HotelReservation Kubernetes Deployment Guide (No-Limits Version)

이 가이드는 **DeathStarBench HotelReservation**을 Bare-metal Kubernetes 환경에 배포하기 위한 완벽한 절차입니다.
기존 소스 코드의 **경로** 수정과 고성능 서버(36코어 등) 활용을 위한 **리소스 제한 해제** 과정이 포함되어 있습니다.

## 사전 요구 사항
* **OS:** Ubuntu 20.04+ (권장)
* **Kubernetes:** v1.19+
* **Tools:** `git`, `kubectl`, `sed`, `make`, `gcc`

---

## 1. 소스 코드 다운로드 및 준비

Helm Chart가 아닌 **원본 Manifest**를 사용하여 배포합니다.

```bash
# 1. 리포지토리 클론
git clone [https://github.com/delimitrou/DeathStarBench.git](https://github.com/delimitrou/DeathStarBench.git)

# 2. Manifest 폴더로 이동
cd DeathStarBench/hotelReservation/kubernetes

```

---

## 2. Manifest 자동 수정 (원클릭 스크립트)

아래 명령어 블록을 한 번에 복사해서 실행하세요. 4가지 핵심 수정 사항이 일괄 적용됩니다.

1. **실행 경로 수정:** `./binary` → `binary` (시스템 경로 인식 문제 해결)
2. **리소스 제한 해제:** `cpu:`, `memory:` 설정 삭제 (36코어 풀 성능 활용을 위해 필수)

```bash
# [Fix 1] 실행 경로 수정 ("./cmd" -> "cmd")
find . -name "*.yaml" -exec sed -i 's|command: \["\./|command: ["|g' {} +

# [Fix 2] 리소스 제한(Limits/Requests) 제거
# 고사양 서버에서 스로틀링 없이 최대 성능을 내기 위해 제한을 풉니다.
find . -name "*.yaml" -exec sed -i '/resources:/d' {} +
find . -name "*.yaml" -exec sed -i '/limits:/d' {} +
find . -name "*.yaml" -exec sed -i '/requests:/d' {} +
find . -name "*.yaml" -exec sed -i '/cpu:/d' {} +
find . -name "*.yaml" -exec sed -i '/memory:/d' {} +


```

> **확인:** 위 명령어를 실행한 후 에러 메시지가 없어야 합니다.

---

## 3. 애플리케이션 배포

수정된 설정 파일로 애플리케이션을 배포합니다.

```bash
# 1. 네임스페이스 생성
kubectl create namespace hotel-res

# 2. 전체 배포 (하위 폴더 포함)
kubectl apply -R -f . -n hotel-res

```

---

## 4. 배포 상태 확인 (안정화)

배포 초기에는 의존성 문제로 일부 파드가 `Error` 상태일 수 있습니다. 이는 정상입니다.

```bash
# 실시간 상태 확인
watch -n 1 kubectl get pods -n hotel-res

```

**[성공 기준]**

* 약 **1~2분 대기** 후 모든 파드의 STATUS가 **`Running`** 이어야 합니다.
* `READY` 컬럼이 모두 `1/1` 이어야 합니다.

---

## 5. 외부 접속 설정 (NodePort)

로컬 머신이나 외부에서 부하 테스트(`wrk`)를 하기 위해 Frontend를 외부에 노출합니다.

```bash
# 1. Frontend 서비스 타입을 NodePort로 변경
kubectl patch svc frontend -n hotel-res -p '{"spec": {"type": "NodePort"}}'

# 2. 접속 포트 확인
kubectl get svc frontend -n hotel-res

```

> **출력 확인:** `PORT(S)` 컬럼의 `80:3xxxx/TCP` 에서 **`3xxxx`** 숫자를 기억하세요. (예: 31643)

---

## 6. 부하 테스트 도구 (wrk2) 설치

로컬 머신(부하 생성기)에서 수행하세요.

```bash
# 1. 필수 라이브러리 설치
sudo apt-get update && sudo apt-get install -y build-essential libssl-dev zlib1g-dev git

# 2. wrk2 소스 폴더로 이동 (이미 클론한 DeathStarBench 내부에 있음)
cd ~/DeathStarBench/wrk2

# 3. 서브모듈 업데이트 (LuaJIT 빈 폴더 문제 해결)
git submodule update --init --recursive

# 4. 빌드
make

# 5. 실행 파일 확인
./wrk -v
# (R 옵션이 보이면 wrk2 설치 성공)

```

---

## 7. 트러블슈팅 (문제 발생 시)

만약 꼬였을 경우, 하나씩 수정하지 말고 **싹 지우고 다시 배포**하는 것이 가장 빠릅니다.

```bash
# 1. 모든 리소스 삭제
kubectl delete -R -f . -n hotel-res

# 2. (확실하게 하기 위해) 네임스페이스 삭제 후 재생성
kubectl delete namespace hotel-res
kubectl create namespace hotel-res

# 3. 재배포
kubectl apply -R -f . -n hotel-res

```