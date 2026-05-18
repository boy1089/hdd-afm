#!/usr/bin/env python3
"""
headless_scan.py — GUI 없이 HDD 스캔을 실행하고 polar plot 이미지를 저장한다.

사용법:
    python3 scripts/headless_scan.py \
        --arduino-port /dev/cu.usbserial-1120 \
        --esp32-port   /dev/cu.usbserial-0001 \
        --output       scans/scan_latest.png

흐름:
    1. Arduino + ESP32 시리얼 연결
    2. Arduino CMD RESET → 가속 완료 대기
    3. Arduino CMD SCAN 전송
    4. ESP32 REV: 메시지를 polar_dict에 누적
    5. Arduino "SCAN COMPLETE" 수신 → 루프 종료
    6. polar plot 생성 및 PNG 저장
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import threading
import time
from datetime import datetime
from typing import Optional

import numpy as np
import serial

# matplotlib은 GUI 없이 사용
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ── 프로토콜 상수 (Arduino / ESP32 펌웨어와 일치) ─────────────────────────────
ARDUINO_BAUD   = 9600
ESP32_BAUD     = 460800
STEPS_PER_REV  = 18

# ── 타임아웃 ─────────────────────────────────────────────────────────────────
RESET_TIMEOUT_S   = 30    # CMD RESET ACK 대기
SPEED_TIMEOUT_S   = 60    # Constant speed 메시지 대기
SCAN_TIMEOUT_S    = 600   # SCAN COMPLETE 대기 (암 전체 이동 시간)


# ─────────────────────────────────────────────────────────────────────────────
#  시리얼 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def send_cmd(ser: serial.Serial, cmd: str) -> None:
    ser.write((cmd + "\n").encode())
    ser.flush()
    print(f"  → {cmd}")


def wait_for(ser: serial.Serial, keyword: str, timeout: float,
             echo_prefix: Optional[str] = None) -> bool:
    """시리얼 라인을 읽으며 keyword가 포함된 줄을 timeout 초 내에 기다린다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = ser.readline()
        except serial.SerialException as exc:
            print(f"[ERROR] readline: {exc}", file=sys.stderr)
            return False
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line and echo_prefix:
            print(f"  {echo_prefix} {line}")
        if keyword in line:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  ESP32 REV: 파싱 (esp32_comm.py의 _parse_rev 와 동일)
# ─────────────────────────────────────────────────────────────────────────────

def parse_rev(line: str) -> Optional[dict]:
    try:
        parts = line[len("REV:"):].split()
        d: dict = {}
        values: list[int] = []
        for p in parts:
            if "=" not in p:
                continue
            k, v = p.split("=", 1)
            if k == "data":
                values = [int(x) for x in v.split(",") if x]
            else:
                d[k] = int(v)
        if "r" not in d or "n" not in d:
            return None
        d["data"] = values
        return d
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  ESP32 수신 스레드 — REV: 를 polar_dict 에 누적
# ─────────────────────────────────────────────────────────────────────────────

class Esp32Reader(threading.Thread):
    def __init__(self, ser: serial.Serial):
        super().__init__(daemon=True)
        self.ser        = ser
        self.polar_dict: dict[tuple, float] = {}
        self.rev_count  = 0
        self._stop      = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self.ser.readline()
            except serial.SerialException:
                break
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            if line.startswith("REV:"):
                d = parse_rev(line)
                if d:
                    r    = d["r"]
                    vals = d["data"]
                    for tidx, sv in enumerate(vals):
                        self.polar_dict[(r, float(tidx))] = sv
                    self.rev_count += 1
                    print(f"  [ESP32] REV r={r}  total_rings={self.rev_count}")

    def stop(self) -> None:
        self._stop.set()


# ─────────────────────────────────────────────────────────────────────────────
#  Polar plot 생성 및 저장
# ─────────────────────────────────────────────────────────────────────────────

def save_polar_plot(polar_dict: dict[tuple, float], output_path: str) -> None:
    if not polar_dict:
        print("[WARN] polar_dict가 비어 있습니다. 이미지를 저장하지 않습니다.")
        return

    keys       = list(polar_dict.keys())
    strain_arr = np.array([polar_dict[k] for k in keys], dtype=float)
    theta_arr  = np.array([
        2.0 * math.pi * (k[1] % STEPS_PER_REV) / STEPS_PER_REV
        for k in keys
    ])
    r_arr = np.array([k[0] for k in keys], dtype=float)

    vmin = float(np.percentile(strain_arr, 2))
    vmax = float(np.percentile(strain_arr, 98))
    if vmin == vmax:
        vmax = vmin + 1

    fig  = plt.figure(figsize=(8, 8), facecolor="#1a1a2e")
    ax   = fig.add_subplot(111, projection="polar", facecolor="#16213e")
    sc   = ax.scatter(
        theta_arr, r_arr,
        c=strain_arr, cmap="plasma",
        s=6, alpha=0.85,
        vmin=vmin, vmax=vmax,
        linewidths=0,
    )
    ax.set_rlim(0, float(r_arr.max()) * 1.05 + 1)
    ax.tick_params(colors="white")
    ax.spines["polar"].set_color("#444")
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_color("white")

    cbar = fig.colorbar(sc, ax=ax, pad=0.1, shrink=0.7)
    cbar.ax.yaxis.set_tick_params(color="white")
    cbar.outline.set_edgecolor("white")
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color="white")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ax.set_title(f"HDD Strain Scan\n{ts}\n{len(keys)} points",
                 color="white", pad=20, fontsize=11)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fig.savefig(output_path, dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)

    # 타임스탬프 사본도 저장
    base, ext = os.path.splitext(output_path)
    ts_path   = f"{base}_{datetime.now():%Y%m%d_%H%M%S}{ext}"
    import shutil
    shutil.copy2(output_path, ts_path)

    print(f"SAVED: {output_path}")
    print(f"SAVED: {ts_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  메인
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="HDD headless scan")
    parser.add_argument("--arduino-port", default="/dev/cu.usbserial-1120")
    parser.add_argument("--esp32-port",   default="/dev/cu.usbserial-0001")
    parser.add_argument("--output",       default="scans/scan_latest.png")
    args = parser.parse_args()

    print(f"[headless_scan] Arduino={args.arduino_port}  ESP32={args.esp32_port}")
    print(f"[headless_scan] Output={args.output}")

    # ── 1. 시리얼 포트 열기 ──────────────────────────────────────────────────
    try:
        ard = serial.Serial(args.arduino_port, ARDUINO_BAUD, timeout=2.0)
    except serial.SerialException as exc:
        print(f"[ERROR] Arduino 포트 열기 실패: {exc}", file=sys.stderr)
        return 1

    try:
        esp = serial.Serial(args.esp32_port, ESP32_BAUD, timeout=2.0)
    except serial.SerialException as exc:
        print(f"[ERROR] ESP32 포트 열기 실패: {exc}", file=sys.stderr)
        ard.close()
        return 1

    # Arduino 리셋 후 버퍼 안정화 대기
    time.sleep(2.0)
    ard.reset_input_buffer()
    esp.reset_input_buffer()

    # ── 2. ESP32 수신 스레드 시작 ────────────────────────────────────────────
    reader = Esp32Reader(esp)
    reader.start()

    try:
        # ── 3. Arduino RESET ─────────────────────────────────────────────────
        print("\n[STEP 1] CMD RESET 전송 …")
        send_cmd(ard, "CMD RESET")
        if not wait_for(ard, "ACK CMD RESET", RESET_TIMEOUT_S, "[Arduino]"):
            print("[ERROR] CMD RESET ACK 수신 실패", file=sys.stderr)
            return 1

        # ── 4. 일정 속도 도달 대기 ───────────────────────────────────────────
        print(f"\n[STEP 2] Constant speed 대기 (최대 {SPEED_TIMEOUT_S}s) …")
        if not wait_for(ard, "Constant speed", SPEED_TIMEOUT_S, "[Arduino]"):
            print("[ERROR] Constant speed 메시지 수신 실패", file=sys.stderr)
            return 1

        # ── 5. CMD SCAN ──────────────────────────────────────────────────────
        print("\n[STEP 3] CMD SCAN 전송 …")
        send_cmd(ard, "CMD SCAN")
        if not wait_for(ard, "ACK CMD SCAN", 5.0, "[Arduino]"):
            print("[ERROR] CMD SCAN ACK 수신 실패", file=sys.stderr)
            return 1

        # ── 6. SCAN COMPLETE 대기 ────────────────────────────────────────────
        print(f"\n[STEP 4] SCAN COMPLETE 대기 (최대 {SCAN_TIMEOUT_S}s) …")
        if not wait_for(ard, "SCAN COMPLETE", SCAN_TIMEOUT_S, "[Arduino]"):
            print("[ERROR] SCAN COMPLETE 수신 실패 — 타임아웃", file=sys.stderr)
            # 데이터가 있으면 부분 저장
            if reader.polar_dict:
                print(f"[WARN] 부분 데이터({reader.rev_count}링) 로 이미지 저장")
            else:
                return 1

        print(f"\n[STEP 5] 스캔 완료 — {reader.rev_count}링 수신")

    finally:
        reader.stop()
        ard.close()
        esp.close()

    # ── 7. 이미지 저장 ────────────────────────────────────────────────────────
    print("\n[STEP 6] polar plot 생성 …")
    save_polar_plot(reader.polar_dict, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
