"""
esp32_comm.py — ESP32 Serial 통신 모듈 (PyQt5 QThread 기반)

ArduinoComm 와 동일한 구조. ESP32-specific 커맨드 메서드만 추가.
"""

from __future__ import annotations

import serial
import serial.tools.list_ports
from PyQt5.QtCore import QObject, QThread, pyqtSignal


# ---------------------------------------------------------------------------
#  Background reader thread (ArduinoComm._ReaderThread 와 동일 구조)
# ---------------------------------------------------------------------------
class _ReaderThread(QThread):
    line_received  = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, ser: serial.Serial, parent: QObject | None = None):
        super().__init__(parent)
        self._ser     = ser
        self._running = True

    def run(self) -> None:
        while self._running:
            try:
                if self._ser and self._ser.is_open:
                    raw = self._ser.readline()
                    if raw:
                        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                        if text:
                            self.line_received.emit(text)
            except serial.SerialException as exc:
                self.error_occurred.emit(str(exc))
                break
            except Exception as exc:  # noqa: BLE001
                self.error_occurred.emit(str(exc))
                break

    def stop(self) -> None:
        self._running = False
        self.wait(2000)


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------
class Esp32Comm(QObject):
    """ESP32 와의 Serial 통신을 관리하는 Qt 객체.

    Signals
    -------
    data_received(str)         수신된 한 줄의 텍스트 (TRIG:, Z_STATUS:, ACK 등)
    trig_received(dict)        TRIG: 메시지를 파싱한 딕셔너리
                               {"r": int, "theta": int, "strain_avg": int, "n": int}
    connection_changed(bool)   연결 상태 변화
    error_occurred(str)        오류 메시지
    """

    data_received      = pyqtSignal(str)
    trig_received      = pyqtSignal(dict)
    connection_changed = pyqtSignal(bool)
    error_occurred     = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._ser:    serial.Serial | None  = None
        self._reader: _ReaderThread | None  = None

    # ------------------------------------------------------------------
    #  연결 / 해제
    # ------------------------------------------------------------------
    def connect(self, port: str, baud: int = 115200) -> bool:
        self.disconnect()
        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=baud,
                timeout=1.0,
            )
            self._reader = _ReaderThread(self._ser, self)
            self._reader.line_received.connect(self._on_line)
            self._reader.error_occurred.connect(self._on_reader_error)
            self._reader.start()
            self.connection_changed.emit(True)
            return True
        except serial.SerialException as exc:
            self.error_occurred.emit(f"연결 실패: {exc}")
            self._ser = None
            return False

    def disconnect(self) -> None:
        if self._reader:
            self._reader.stop()
            self._reader = None
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:  # noqa: BLE001
                pass
        self._ser = None
        self.connection_changed.emit(False)

    @property
    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    # ------------------------------------------------------------------
    #  수신 처리
    # ------------------------------------------------------------------
    def _on_line(self, line: str) -> None:
        self.data_received.emit(line)
        if line.startswith("TRIG:"):
            parsed = self._parse_trig(line)
            if parsed:
                self.trig_received.emit(parsed)

    def _on_reader_error(self, msg: str) -> None:
        self.error_occurred.emit(msg)
        self.connection_changed.emit(False)

    @staticmethod
    def _parse_trig(line: str) -> dict | None:
        """'TRIG: r=<r> theta=<theta> strain_avg=<avg> n=<n>' 파싱."""
        try:
            parts = line[len("TRIG:"):].split()
            d: dict = {}
            for p in parts:
                k, v = p.split("=")
                d[k] = int(v)
            for required in ("r", "theta", "strain_avg", "n"):
                if required not in d:
                    return None
            return d
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    #  송신 헬퍼
    # ------------------------------------------------------------------
    def _send(self, cmd: str) -> bool:
        if not self.is_connected:
            self.error_occurred.emit("ESP32: 연결되지 않은 상태에서 커맨드를 보낼 수 없습니다.")
            return False
        try:
            self._ser.write((cmd.strip() + "\n").encode("utf-8"))
            return True
        except serial.SerialException as exc:
            self.error_occurred.emit(f"ESP32 전송 오류: {exc}")
            return False

    # ------------------------------------------------------------------
    #  ESP32 커맨드 메서드
    # ------------------------------------------------------------------
    def set_z_target(self, value: int) -> bool:
        return self._send(f"SET Z_TARGET {value}")

    def set_kp(self, value_x100: int) -> bool:
        """Kp * 100 정수로 전달 (예: Kp=1.5 → 150)."""
        return self._send(f"SET KP {value_x100}")

    def set_ki(self, value_x100: int) -> bool:
        return self._send(f"SET KI {value_x100}")

    def set_strain_gain(self, value_x1000: int) -> bool:
        return self._send(f"SET STRAIN_GAIN {value_x1000}")

    def set_strain_offset(self, value: int) -> bool:
        return self._send(f"SET STRAIN_OFFSET {value}")

    def set_trig_mode(self, mode: int) -> bool:
        return self._send(f"SET TRIG_MODE {mode}")

    def cmd_stop(self) -> bool:
        return self._send("CMD STOP")

    def cmd_home(self) -> bool:
        return self._send("CMD HOME")

    def get_status(self) -> bool:
        return self._send("GET STATUS")

    # ------------------------------------------------------------------
    #  포트 목록
    # ------------------------------------------------------------------
    @staticmethod
    def list_ports() -> list[str]:
        ports = serial.tools.list_ports.comports()
        return [p.device for p in sorted(ports, key=lambda x: x.device)]
