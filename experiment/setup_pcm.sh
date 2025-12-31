#!/bin/bash
# ============================================================
# Intel PCM (Performance Counter Monitor) 설치 스크립트
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ============================================================
# PCM이 측정하는 것
# ============================================================
print_pcm_info() {
    echo ""
    echo "============================================================"
    echo "Intel PCM (Performance Counter Monitor)"
    echo "============================================================"
    echo ""
    echo "PCM measures hardware-level metrics:"
    echo ""
    echo "  1. Memory Bandwidth (GB/s)"
    echo "     - DDR memory read/write throughput"
    echo "     - Helps identify memory-bound workloads"
    echo ""
    echo "  2. LLC (Last Level Cache) Metrics"
    echo "     - L3 cache hit/miss rates"
    echo "     - Cache contention between pods"
    echo ""
    echo "  3. Instructions Per Cycle (IPC)"
    echo "     - CPU efficiency metric"
    echo ""
    echo "Why is this useful for Istio analysis?"
    echo "  - Istio sidecar adds network processing overhead"
    echo "  - This can increase memory bandwidth usage"
    echo "  - Cache contention may occur with many sidecars"
    echo ""
    echo "============================================================"
}

# ============================================================
# 의존성 설치
# ============================================================
install_dependencies() {
    log_info "Installing dependencies..."
    
    if command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y git cmake build-essential
    elif command -v yum &> /dev/null; then
        sudo yum install -y git cmake gcc-c++ make
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y git cmake gcc-c++ make
    else
        log_error "Unsupported package manager. Please install git, cmake, and build tools manually."
        exit 1
    fi
}

# ============================================================
# PCM 빌드
# ============================================================
build_pcm() {
    local install_dir="${1:-$HOME/pcm}"
    
    log_info "Cloning PCM repository..."
    if [ -d "$install_dir" ]; then
        log_warn "Directory $install_dir already exists. Updating..."
        cd "$install_dir"
        git pull
    else
        git clone https://github.com/intel/pcm.git "$install_dir"
        cd "$install_dir"
    fi
    
    log_info "Building PCM..."
    mkdir -p build
    cd build
    cmake ..
    make -j$(nproc)
    
    log_info "PCM built successfully!"
    echo ""
    echo "Binaries are in: $install_dir/build/bin/"
    ls -la "$install_dir/build/bin/"
}

# ============================================================
# PCM 설치 (복사)
# ============================================================
install_pcm() {
    local install_dir="${1:-$HOME/pcm}"
    local target_dir="${2:-.}"
    
    if [ ! -f "$install_dir/build/bin/pcm" ]; then
        log_error "PCM binary not found. Run build first."
        exit 1
    fi
    
    log_info "Copying PCM binary to $target_dir..."
    cp "$install_dir/build/bin/pcm" "$target_dir/pcm.x"
    chmod +x "$target_dir/pcm.x"
    
    log_info "PCM installed to $target_dir/pcm.x"
}

# ============================================================
# MSR 모듈 로드 (PCM 실행에 필요)
# ============================================================
setup_msr() {
    log_info "Setting up MSR module..."
    
    # MSR 모듈 로드
    if ! lsmod | grep -q msr; then
        log_info "Loading MSR module..."
        sudo modprobe msr
    else
        log_info "MSR module already loaded"
    fi
    
    # 부팅 시 자동 로드 설정
    if ! grep -q "^msr$" /etc/modules 2>/dev/null; then
        echo "msr" | sudo tee -a /etc/modules > /dev/null
        log_info "Added MSR to /etc/modules for auto-load"
    fi
}

# ============================================================
# 테스트
# ============================================================
test_pcm() {
    local pcm_path="${1:-./pcm.x}"
    
    if [ ! -x "$pcm_path" ]; then
        log_error "PCM not found at $pcm_path"
        exit 1
    fi
    
    log_info "Testing PCM..."
    echo ""
    
    # 짧은 테스트 실행
    sudo "$pcm_path" 1 -i=2 2>&1 | head -50
    
    if [ $? -eq 0 ]; then
        echo ""
        log_info "PCM is working correctly!"
    else
        log_error "PCM test failed. Check:"
        echo "  1. Are you running on bare metal (not VM)?"
        echo "  2. Is MSR module loaded? (sudo modprobe msr)"
        echo "  3. Do you have sudo access?"
    fi
}

# ============================================================
# Sudo 설정 (passwordless for pcm)
# ============================================================
setup_sudo() {
    log_info "Setting up passwordless sudo for PCM..."
    
    local pcm_path=$(readlink -f "${1:-./pcm.x}")
    local sudoers_file="/etc/sudoers.d/pcm"
    
    echo "$USER ALL=(ALL) NOPASSWD: $pcm_path" | sudo tee "$sudoers_file" > /dev/null
    sudo chmod 440 "$sudoers_file"
    
    log_info "Added passwordless sudo for $pcm_path"
    log_warn "This allows running PCM without password. Remove with:"
    echo "  sudo rm $sudoers_file"
}

# ============================================================
# 메인
# ============================================================
main() {
    local command="${1:-help}"
    
    case $command in
        info)
            print_pcm_info
            ;;
        deps)
            install_dependencies
            ;;
        build)
            install_dependencies
            build_pcm "${2:-$HOME/pcm}"
            ;;
        install)
            install_pcm "${2:-$HOME/pcm}" "${3:-.}"
            ;;
        setup-msr)
            setup_msr
            ;;
        setup-sudo)
            setup_sudo "${2:-./pcm.x}"
            ;;
        test)
            test_pcm "${2:-./pcm.x}"
            ;;
        all)
            print_pcm_info
            install_dependencies
            build_pcm "$HOME/pcm"
            install_pcm "$HOME/pcm" "."
            setup_msr
            test_pcm "./pcm.x"
            echo ""
            log_info "PCM setup complete!"
            echo ""
            echo "Optional: Run './setup_pcm.sh setup-sudo' for passwordless execution"
            ;;
        help|*)
            echo "Usage: $0 <command> [options]"
            echo ""
            echo "Commands:"
            echo "  info        Show what PCM measures and why it's useful"
            echo "  deps        Install build dependencies"
            echo "  build       Clone and build PCM"
            echo "  install     Copy PCM binary to current directory"
            echo "  setup-msr   Load MSR kernel module (required for PCM)"
            echo "  setup-sudo  Configure passwordless sudo for PCM"
            echo "  test        Test PCM functionality"
            echo "  all         Do everything (deps, build, install, setup-msr, test)"
            echo ""
            echo "Example:"
            echo "  $0 all              # Full installation"
            echo "  $0 test ./pcm.x     # Test specific binary"
            ;;
    esac
}

main "$@"
