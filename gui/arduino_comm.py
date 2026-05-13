"""
arduino_comm.py — Arduino Serial 통신 모듈 (PyQt5 QThread 기반)
"""

from __future__ import annotations

import serial
import serial.tools.list_ports
from PyQt5.QtCore import QObject, QThread, pyqtSignal


# ---------------------------------------------------------------------------
#  Background reader thread
# ---------------------------------------------------------------------------
class _ReaderThread(QThread):
    """Serial 포트를 블로킹 read 로 지속 수신하는 백그라운드 스레드."""

    line_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, ser: serial.Serial, parent: QObject | None = None):
        super().__init__(parent)
        self._ser = ser
        self._running = True

    def run(self) -> None:
        while self._running:
            try:
                if self._ser and self._ser.is_open:
                    raw = self._ser.readline()          # blocks up to timeout
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
class ArduinoComm(QObject):
    """Arduino 와의 Serial 통신을 관리하는 Qt 객체.

    Signals
    -------
    data_received(str)       수신된 한 줄의 텍스트
    connection_changed(bool) 연결 상태 변화 (True=연결됨, False=끊김)
    error_occurred(str)      오류 메시지
    """

    data_received    = pyqtSignal(str)
    connection_changed = pyqtSignal(bool)
    error_occurred   = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._ser: serial.Serial | None = None
        self._reader: _ReaderThread | None = None

    # ------------------------------------------------------------------
    #  연결 / 해제
    # ------------------------------------------------------------------
    def connect(self, port: str, baud: int = 9600) -> bool:
        """지정된 포트로 연결한다. 성공 시 True 반환."""
        self.disconnect()
        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=baud,
                timeout=1.0,          # readline timeout (초)
            )
            self._reader = _ReaderThread(self._ser, self)
            self._reader.line_received.connect(self.data_received)
            self._reader.error_occurred.connect(self._on_reader_error)
            self._reader.start()
            self.connection_changed.emit(True)
            return True
        except serial.SerialException as exc:
            self.error_occurred.emit(f"연결 실패: {exc}")
            self._ser = None
            return False

    def disconnect(self) -> None:
        """현재 연결을 닫는다."""
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
    #  송신
    # ------------------------------------------------------------------
    def send_command(self, cmd: str) -> bool:
        """커맨드를 Arduino 에 전송한다 (자동으로 \\n 추가).
        성공 시 True 반환."""
        if not self.is_connected:
            self.error_occurred.emit("연결되지 않은 상태에서 커맨드를 보낼 수 없습니다.")
            return False
        try:
            self._ser.write((cmd.strip() + "\n").encode("utf-8"))
            return True
        except serial.SerialException as exc:
            self.error_occurred.emit(f"전송 오류: {exc}")
            return False

    # 편의 메서드 -----------------------------------------------------------
    def set_param(self, key: str, value) -> bool:
        return self.send_command(f"SET {key} {value}")

    def cmd_scan(self) -> bool:
        return self.send_command("CMD SCAN")

    def cmd_stop(self) -> bool:
        return self.send_command("CMD STOP")

    def cmd_reset(self) -> bool:
        return self.send_command("CMD RESET")

    def get_status(self) -> bool:
        return self.send_command("GET STATUS")

    # ------------------------------------------------------------------
    #  포트 목록 (static)
    # ------------------------------------------------------------------
    @staticmethod
    def list_ports() -> list[str]:
        """현재 시스템에서 사용 가능한 Serial 포트 목록을 반환한다."""
        ports = serial.tools.list_ports.comports()
        return [p.device for p in sorted(ports, key=lambda x: x.device)]

    # ------------------------------------------------------------------
    #  내부
    # ------------------------------------------------------------------
    def _on_reader_error(self, msg: str) -> None:
        self.error_occurred.emit(msg)
        self.disconnect()
