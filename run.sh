#!/usr/bin/env bash
# run.sh — 빌드/업로드 → GUI 실행
# 사용: bash run.sh [--skip-setup] [--skip-upload]
#                   [--arduino-port <port>] [--esp32-port <port>]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_SETUP=false
SKIP_UPLOAD=false
ARDUINO_PORT="/dev/cu.usbserial-120"
ESP32_PORT="/dev/cu.usbserial-0001"

# ── 인수 파싱 ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-setup)    SKIP_SETUP=true;      shift   ;;
    --skip-upload)   SKIP_UPLOAD=true;     shift   ;;
    --arduino-port)  ARDUINO_PORT="$2";    shift 2 ;;
    --esp32-port)    ESP32_PORT="$2";      shift 2 ;;
    *) echo "[WARN] 알 수 없는 옵션: $1";  shift   ;;
  esac
done

echo "=========================================="
echo "  HDD Controller — 통합 실행 스크립트"
echo "  Arduino : $ARDUINO_PORT"
echo "  ESP32   : $ESP32_PORT"
echo "=========================================="

# ── 1. 환경 설정 ─────────────────────────────────────────────────────────
if [ "$SKIP_SETUP" = false ]; then
  echo ""
  echo "[STEP 1/3] 환경 설정..."
  bash "$REPO_ROOT/scripts/setup.sh"
else
  echo "[STEP 1/3] 환경 설정 건너뜀 (--skip-setup)"
fi

# ── 2. 펌웨어 빌드 & 업로드 ──────────────────────────────────────────────
if [ "$SKIP_UPLOAD" = false ]; then
  echo ""
  echo "[STEP 2/3] 펌웨어 빌드 & 업로드..."

  echo "  [Arduino] 빌드 & 업로드 → $ARDUINO_PORT"
  python3 "$REPO_ROOT/scripts/build_upload.py" \
    --device arduino --port "$ARDUINO_PORT"

  echo "  [ESP32] 빌드 & 업로드 → $ESP32_PORT"
  python3 "$REPO_ROOT/scripts/build_upload.py" \
    --device esp32 --port "$ESP32_PORT"
else
  echo "[STEP 2/3] 펌웨어 업로드 건너뜀 (--skip-upload)"
fi

# ── 3. GUI 실행 ──────────────────────────────────────────────────────────
echo ""
echo "[STEP 3/3] GUI 실행..."
python3 "$REPO_ROOT/gui/main.py" \
  --arduino-port "$ARDUINO_PORT" \
  --esp32-port   "$ESP32_PORT"
