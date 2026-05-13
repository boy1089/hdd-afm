#!/usr/bin/env python3
"""
build_upload.py — arduino-cli 를 이용한 Arduino 스케치 빌드/업로드 래퍼

사용법:
  # 컴파일 + 업로드 (포트 자동 감지)
  python scripts/build_upload.py

  # 포트/보드 지정
  python scripts/build_upload.py --port /dev/cu.usbmodem1401 --fqbn arduino:avr:uno

  # 컴파일만
  python scripts/build_upload.py --compile-only

  # 업로드만 (이미 빌드된 경우)
  python scripts/build_upload.py --upload-only --port /dev/cu.usbmodem1401
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
#  경로 설정
# ---------------------------------------------------------------------------
REPO_ROOT   = Path(__file__).parent.parent.resolve()
SKETCH_PATH = REPO_ROOT / "arduino" / "hdd_controller"
DEFAULT_FQBN = "arduino:avr:uno"


# ---------------------------------------------------------------------------
#  유틸리티
# ---------------------------------------------------------------------------
def _check_arduino_cli() -> str:
    """arduino-cli 실행 파일 경로 반환. 없으면 오류 출력 후 종료."""
    cli = shutil.which("arduino-cli")
    if not cli:
        print(
            "[ERROR] arduino-cli 를 찾을 수 없습니다.\n"
            "        scripts/setup.sh 를 먼저 실행하거나\n"
            "        https://arduino.github.io/arduino-cli/installation/ 을 참고하세요.",
            file=sys.stderr,
        )
        sys.exit(1)
    return cli


def _run(cmd: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    print(f"[RUN] {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
    )
    if not capture:
        pass  # 실시간 출력은 이미 터미널에 표시됨
    if result.returncode != 0 and not capture:
        print(f"[ERROR] 명령 실패 (exit {result.returncode})", file=sys.stderr)
        sys.exit(result.returncode)
    return result


# ---------------------------------------------------------------------------
#  포트 자동 감지
# ---------------------------------------------------------------------------
def find_arduino_port(cli: str, fqbn: str) -> str | None:
    """arduino-cli board list --format json 으로 연결된 Arduino 포트 탐색."""
    result = _run([cli, "board", "list", "--format", "json"], capture=True)
    if result.returncode != 0:
        return None
    try:
        boards = json.loads(result.stdout)
        # arduino-cli 0.35+ 는 배열 반환, 이전 버전은 {"detected_ports": [...]}
        if isinstance(boards, dict):
            boards = boards.get("detected_ports", [])
        for entry in boards:
            # 보드 정보가 있는 포트 우선
            matching_boards = entry.get("matching_boards", [])
            if matching_boards:
                port = entry.get("port", {}).get("address") or entry.get("address")
                if port:
                    board_fqbn = matching_boards[0].get("fqbn", "")
                    if board_fqbn == fqbn or not board_fqbn:
                        return port
        # 보드 인식 실패 시 첫 번째 포트라도 반환
        if boards:
            first = boards[0]
            return first.get("port", {}).get("address") or first.get("address")
    except (json.JSONDecodeError, KeyError, IndexError):
        pass
    return None


# ---------------------------------------------------------------------------
#  컴파일
# ---------------------------------------------------------------------------
def compile_sketch(cli: str, fqbn: str) -> None:
    print(f"\n[COMPILE] fqbn={fqbn}  sketch={SKETCH_PATH}")
    _run([
        cli, "compile",
        "--fqbn", fqbn,
        str(SKETCH_PATH),
    ])
    print("[COMPILE] 성공")


# ---------------------------------------------------------------------------
#  업로드
# ---------------------------------------------------------------------------
def upload_sketch(cli: str, fqbn: str, port: str) -> None:
    print(f"\n[UPLOAD] fqbn={fqbn}  port={port}  sketch={SKETCH_PATH}")
    _run([
        cli, "upload",
        "--fqbn", fqbn,
        "--port", port,
        str(SKETCH_PATH),
    ])
    print("[UPLOAD] 성공")


# ---------------------------------------------------------------------------
#  main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Arduino 스케치 빌드/업로드")
    parser.add_argument("--port",         default=None,        help="시리얼 포트 (예: /dev/cu.usbmodem1401)")
    parser.add_argument("--fqbn",         default=DEFAULT_FQBN, help=f"보드 FQBN (기본: {DEFAULT_FQBN})")
    parser.add_argument("--compile-only", action="store_true",  help="컴파일만 수행")
    parser.add_argument("--upload-only",  action="store_true",  help="업로드만 수행 (사전에 컴파일 필요)")
    args = parser.parse_args()

    cli = _check_arduino_cli()

    if args.upload_only and not args.compile_only:
        # 업로드만
        port = args.port or find_arduino_port(cli, args.fqbn)
        if not port:
            print("[ERROR] Arduino 포트를 찾을 수 없습니다. --port 로 직접 지정하세요.", file=sys.stderr)
            sys.exit(1)
        upload_sketch(cli, args.fqbn, port)

    elif args.compile_only:
        # 컴파일만
        compile_sketch(cli, args.fqbn)

    else:
        # 컴파일 + 업로드 (기본)
        compile_sketch(cli, args.fqbn)
        port = args.port or find_arduino_port(cli, args.fqbn)
        if not port:
            print("[ERROR] Arduino 포트를 찾을 수 없습니다. --port 로 직접 지정하세요.", file=sys.stderr)
            sys.exit(1)
        upload_sketch(cli, args.fqbn, port)

    print("\n[DONE]")


if __name__ == "__main__":
    main()
