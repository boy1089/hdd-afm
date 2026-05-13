#!/usr/bin/env bash
# setup.sh — arduino-cli 설치 + Arduino Uno 코어 준비
# 사용: bash scripts/setup.sh
set -euo pipefail

FQBN="arduino:avr"

echo "=========================================="
echo "  HDD Controller — Arduino 환경 설정"
echo "=========================================="

# ── 1. arduino-cli 설치 확인 ──────────────────────────────────────────────
if command -v arduino-cli &>/dev/null; then
  echo "[OK] arduino-cli 이미 설치됨: $(arduino-cli version)"
else
  echo "[INFO] arduino-cli 설치 중..."
  if command -v brew &>/dev/null; then
    brew install arduino-cli
  else
    # 공식 설치 스크립트 (Linux / macOS without Homebrew)
    curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
    # 설치된 경로를 PATH 에 추가
    export PATH="$HOME/bin:$PATH"
    echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.zshrc" 2>/dev/null || true
    echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc" 2>/dev/null || true
  fi
fi

# ── 2. arduino-cli 인덱스 업데이트 ──────────────────────────────────────────
echo "[INFO] 플랫폼 인덱스 업데이트 중..."
arduino-cli core update-index

# ── 3. Arduino AVR 코어 설치 ─────────────────────────────────────────────
if arduino-cli core list | grep -q "$FQBN"; then
  echo "[OK] $FQBN 코어 이미 설치됨"
else
  echo "[INFO] $FQBN 코어 설치 중..."
  arduino-cli core install "$FQBN"
fi

# ── 4. Python 의존성 설치 ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQ_FILE="$SCRIPT_DIR/../gui/requirements.txt"

if [ -f "$REQ_FILE" ]; then
  echo "[INFO] Python 패키지 설치 중..."
  pip3 install -r "$REQ_FILE"
else
  echo "[WARN] gui/requirements.txt 를 찾을 수 없습니다."
fi

# ── 5. 연결된 보드 목록 출력 ────────────────────────────────────────────
echo ""
echo "[INFO] 현재 연결된 Arduino 보드:"
arduino-cli board list || true

echo ""
echo "=========================================="
echo "  설정 완료!"
echo "  빌드/업로드: python scripts/build_upload.py"
echo "  GUI 실행:    python gui/main.py"
echo "=========================================="
