#!/usr/bin/env bash
# run.sh — 환경 설정 → 펌웨어 빌드/업로드 → GUI 실행 (한 번에)
# 사용: bash run.sh [--port /dev/cu.usbmodem1401] [--skip-setup] [--skip-upload]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_SETUP=false
SKIP_UPLOAD=false
PORT_ARG=""
MANUAL_PORT=""

# ── 인수 파싱 ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)         MANUAL_PORT="$2"; shift 2 ;;
    --skip-setup)   SKIP_SETUP=true;  shift   ;;
    --skip-upload)  SKIP_UPLOAD=true; shift   ;;
    *) echo "[WARN] 알 수 없는 옵션: $1"; shift ;;
  esac
done

# ── 포트 선택 ─────────────────────────────────────────────────────────────
if [ "$SKIP_UPLOAD" = false ]; then
  if [ -n "$MANUAL_PORT" ]; then
    PORT_ARG="--port $MANUAL_PORT"
  else
    echo ""
    echo "[포트 선택] 연결된 시리얼 포트 목록:"
    PORTS=()
    while IFS= read -r p; do PORTS+=("$p"); done < <(ls /dev/cu.* 2>/dev/null | grep -v -E 'debug-console|wlan-debug')
    if [ ${#PORTS[@]} -eq 0 ]; then
      echo "  (감지된 포트 없음)"
    else
      for i in "${!PORTS[@]}"; do
        echo "  [$((i+1))] ${PORTS[$i]}"
      done
    fi
    echo ""
    DEFAULT_PORT="/dev/cu.usbserial-110"
    printf "포트 번호 입력 (직접 경로 입력도 가능, 기본값: %s): " "$DEFAULT_PORT"
    read -r PORT_INPUT
    if [[ "$PORT_INPUT" =~ ^[0-9]+$ ]]; then
      IDX=$((PORT_INPUT - 1))
      if [ "$IDX" -ge 0 ] && [ "$IDX" -lt "${#PORTS[@]}" ]; then
        PORT_ARG="--port ${PORTS[$IDX]}"
        echo "[INFO] 선택된 포트: ${PORTS[$IDX]}"
      else
        echo "[WARN] 잘못된 번호, 기본 포트로 진행: $DEFAULT_PORT"
        PORT_ARG="--port $DEFAULT_PORT"
      fi
    elif [ -n "$PORT_INPUT" ]; then
      PORT_ARG="--port $PORT_INPUT"
      echo "[INFO] 선택된 포트: $PORT_INPUT"
    else
      PORT_ARG="--port $DEFAULT_PORT"
      echo "[INFO] 기본 포트 사용: $DEFAULT_PORT"
    fi
  fi
fi

echo "=========================================="
echo "  HDD Controller — 통합 실행 스크립트"
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
  # shellcheck disable=SC2086
  python3 "$REPO_ROOT/scripts/build_upload.py" $PORT_ARG
else
  echo "[STEP 2/3] 펌웨어 업로드 건너뜀 (--skip-upload)"
fi

# ── 3. GUI 실행 ──────────────────────────────────────────────────────────
echo ""
echo "[STEP 3/3] GUI 실행..."
# shellcheck disable=SC2086
python3 "$REPO_ROOT/gui/main.py" $PORT_ARG
