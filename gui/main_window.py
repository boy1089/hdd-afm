"""
main_window.py — HDD Controller GUI  (PyQt5)
"""

from __future__ import annotations

from PyQt5.QtCore    import Qt, QTimer
from PyQt5.QtGui     import QFont, QColor, QPalette
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton,
    QComboBox, QTextEdit, QSplitter, QFormLayout,
    QSpinBox, QStatusBar, QMessageBox, QSizePolicy,
)

from arduino_comm import ArduinoComm


# ---------------------------------------------------------------------------
#  파라미터 정의 (key, label, min, max, default)
# ---------------------------------------------------------------------------
SPEED_PARAMS: list[tuple] = [
    ("maxS",         "Max Speed (0-255)",       0,   255, 110),
    ("startStepD",   "Start Step Delay (ms)",   1,  5000,  50),
    ("targetStepD",  "Target Step Delay (ms)",  1,  5000,  10),
]

ARM_PARAMS: list[tuple] = [
    ("minArmPWM",  "Min Arm PWM",   0, 255,  20),
    ("maxArmPWM",  "Max Arm PWM",   0, 255,  60),
    ("pwmStep",    "Arm PWM Step",  1,  50,   1),
]

SCAN_PARAMS: list[tuple] = [
    ("rotationsPerMove", "Rotations Per Move", 1, 9999, 40),
]

KICK_PARAMS: list[tuple] = [
    ("kickAmount",   "Kick Amount",          0, 255, 50),
    ("kickDuration", "Kick Duration (ms)",   0, 999, 100),
]

ALL_PARAM_GROUPS: list[tuple] = [
    ("Speed",   SPEED_PARAMS),
    ("Arm PWM", ARM_PARAMS),
    ("Scan",    SCAN_PARAMS),
    ("Kick",    KICK_PARAMS),
]


# ---------------------------------------------------------------------------
#  MainWindow
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, auto_port: str | None = None):
        super().__init__()
        self.setWindowTitle("HDD Controller")
        self.resize(1000, 720)

        self._comm = ArduinoComm(self)
        self._comm.data_received.connect(self._on_data_received)
        self._comm.connection_changed.connect(self._on_connection_changed)
        self._comm.error_occurred.connect(self._on_error)

        self._param_spinboxes: dict[str, QSpinBox] = {}
        self._scanning = False
        self._auto_port = auto_port

        self._build_ui()
        self._setup_port_refresh_timer()

        if self._auto_port:
            QTimer.singleShot(500, self._auto_connect)

    # -----------------------------------------------------------------------
    #  UI 구성
    # -----------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # ── 상단 연결 바 ────────────────────────────────────────────────────
        root_layout.addWidget(self._build_connection_bar())

        # ── 중간 영역 (컨트롤 | 파라미터 || 로그) ───────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        left_layout.addWidget(self._build_control_panel())
        left_layout.addWidget(self._build_param_panel())
        left_layout.addStretch()

        splitter.addWidget(left_panel)
        splitter.addWidget(self._build_log_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        root_layout.addWidget(splitter, stretch=1)

        # ── 상태바 ──────────────────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("연결 안 됨")

    # ── 연결 바 ──────────────────────────────────────────────────────────────
    def _build_connection_bar(self) -> QGroupBox:
        box = QGroupBox("Connection")
        layout = QHBoxLayout(box)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Port:"))
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(160)
        layout.addWidget(self._port_combo)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setFixedWidth(70)
        self._refresh_btn.clicked.connect(self._refresh_ports)
        layout.addWidget(self._refresh_btn)

        layout.addWidget(QLabel("Baud:"))
        self._baud_combo = QComboBox()
        for b in ("9600", "19200", "38400", "57600", "115200"):
            self._baud_combo.addItem(b)
        self._baud_combo.setCurrentText("9600")
        layout.addWidget(self._baud_combo)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setFixedWidth(80)
        self._connect_btn.setCheckable(True)
        self._connect_btn.clicked.connect(self._toggle_connection)
        layout.addWidget(self._connect_btn)

        self._conn_label = QLabel("●  Disconnected")
        self._conn_label.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(self._conn_label)

        layout.addStretch()
        return box

    # ── 제어 패널 ─────────────────────────────────────────────────────────────
    def _build_control_panel(self) -> QGroupBox:
        box = QGroupBox("Control")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        self._scan_btn = QPushButton("▶  SCAN")
        self._scan_btn.setMinimumHeight(44)
        self._scan_btn.setCheckable(True)
        self._scan_btn.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; font-size: 14px; border-radius: 6px; }"
            "QPushButton:checked { background-color: #558b2f; }"
            "QPushButton:disabled { background-color: #555; }"
        )
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        layout.addWidget(self._scan_btn)

        reset_btn = QPushButton("↺  RESET")
        reset_btn.setMinimumHeight(36)
        reset_btn.setStyleSheet(
            "QPushButton { background-color: #1565c0; color: white; font-size: 13px; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #555; }"
        )
        reset_btn.clicked.connect(self._on_reset_clicked)
        layout.addWidget(reset_btn)

        stop_btn = QPushButton("■  STOP")
        stop_btn.setMinimumHeight(44)
        stop_btn.setStyleSheet(
            "QPushButton { background-color: #b71c1c; color: white; font-size: 14px; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #555; }"
        )
        stop_btn.clicked.connect(self._on_stop_clicked)
        layout.addWidget(stop_btn)

        status_btn = QPushButton("? GET STATUS")
        status_btn.setMinimumHeight(30)
        status_btn.clicked.connect(self._on_get_status_clicked)
        layout.addWidget(status_btn)

        # 버튼 참조 보관 (연결 상태 따라 enable/disable)
        self._control_buttons = [self._scan_btn, reset_btn, stop_btn, status_btn]
        for btn in self._control_buttons:
            btn.setEnabled(False)

        return box

    # ── 파라미터 패널 ─────────────────────────────────────────────────────────
    def _build_param_panel(self) -> QGroupBox:
        box = QGroupBox("Parameters")
        outer = QVBoxLayout(box)
        outer.setSpacing(6)

        for group_name, params in ALL_PARAM_GROUPS:
            grp = QGroupBox(group_name)
            form = QFormLayout(grp)
            form.setSpacing(4)
            for key, label, lo, hi, default in params:
                sb = QSpinBox()
                sb.setRange(lo, hi)
                sb.setValue(default)
                sb.setMinimumWidth(80)
                form.addRow(label + ":", sb)
                self._param_spinboxes[key] = sb
            outer.addWidget(grp)

        self._apply_btn = QPushButton("Apply Parameters")
        self._apply_btn.setMinimumHeight(34)
        self._apply_btn.setStyleSheet(
            "QPushButton { background-color: #e65100; color: white; font-size: 13px; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #555; }"
        )
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply_params)
        outer.addWidget(self._apply_btn)

        return box

    # ── 로그 패널 ─────────────────────────────────────────────────────────────
    def _build_log_panel(self) -> QGroupBox:
        box = QGroupBox("Serial Log")
        layout = QVBoxLayout(box)

        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setFont(QFont("Courier New", 10))
        self._log_edit.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4;")
        layout.addWidget(self._log_edit, stretch=1)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedWidth(70)
        clear_btn.clicked.connect(self._log_edit.clear)
        btn_row.addStretch()
        btn_row.addWidget(clear_btn)
        layout.addLayout(btn_row)

        return box

    # -----------------------------------------------------------------------
    #  포트 갱신
    # -----------------------------------------------------------------------
    def _auto_connect(self):
        port = self._auto_port
        # 콤보박스에 해당 포트가 없으면 추가
        if self._port_combo.findText(port) == -1:
            self._port_combo.insertItem(0, port)
        self._port_combo.setCurrentText(port)
        self._connect_btn.setChecked(True)
        self._toggle_connection(True)

    def _setup_port_refresh_timer(self):
        self._refresh_ports()
        timer = QTimer(self)
        timer.timeout.connect(self._refresh_ports)
        timer.start(3000)  # 3초마다 자동 갱신

    def _refresh_ports(self):
        current = self._port_combo.currentText()
        ports = ArduinoComm.list_ports()
        self._port_combo.clear()
        if ports:
            self._port_combo.addItems(ports)
            if current in ports:
                self._port_combo.setCurrentText(current)
        else:
            self._port_combo.addItem("(없음)")

    # -----------------------------------------------------------------------
    #  연결 토글
    # -----------------------------------------------------------------------
    def _toggle_connection(self, checked: bool):
        if checked:
            port = self._port_combo.currentText()
            if port in ("(없음)", ""):
                self._connect_btn.setChecked(False)
                QMessageBox.warning(self, "포트 없음", "연결할 포트가 없습니다.")
                return
            baud = int(self._baud_combo.currentText())
            ok = self._comm.connect(port, baud)
            if not ok:
                self._connect_btn.setChecked(False)
        else:
            self._comm.disconnect()

    # -----------------------------------------------------------------------
    #  컨트롤 버튼 핸들러
    # -----------------------------------------------------------------------
    def _on_scan_clicked(self, checked: bool):
        if checked:
            self._comm.cmd_reset()
            self._log("→ CMD RESET 전송 (스캔 시작 전 초기화)")
            self._comm.cmd_scan()
            self._scan_btn.setText("⏸  SCANNING…")
            self._log("→ CMD SCAN 전송")
        else:
            # scan 중지 = STOP 후 재가속하지 않음 (사용자가 명시적으로 STOP 눌러야 함)
            self._scan_btn.setText("▶  SCAN")
            self._log("→ Scan button released (로터는 계속 회전)")

    def _on_reset_clicked(self):
        self._scan_btn.setChecked(False)
        self._scan_btn.setText("▶  SCAN")
        self._comm.cmd_reset()
        self._log("→ CMD RESET 전송")

    def _on_stop_clicked(self):
        self._scan_btn.setChecked(False)
        self._scan_btn.setText("▶  SCAN")
        self._comm.cmd_stop()
        self._log("→ CMD STOP 전송")

    def _on_get_status_clicked(self):
        self._comm.get_status()
        self._log("→ GET STATUS 전송")

    # -----------------------------------------------------------------------
    #  파라미터 Apply
    # -----------------------------------------------------------------------
    def _on_apply_params(self):
        sent = []
        for key, sb in self._param_spinboxes.items():
            self._comm.set_param(key, sb.value())
            sent.append(f"{key}={sb.value()}")
        self._log(f"→ Parameters applied: {', '.join(sent)}")

    # -----------------------------------------------------------------------
    #  Signal 핸들러
    # -----------------------------------------------------------------------
    def _on_data_received(self, line: str):
        self._log(f"← {line}", color="#a8e6a3")

    def _on_connection_changed(self, connected: bool):
        if connected:
            port = self._port_combo.currentText()
            self._conn_label.setText(f"●  Connected  ({port})")
            self._conn_label.setStyleSheet("color: #4caf50; font-weight: bold;")
            self._connect_btn.setText("Disconnect")
            self._status_bar.showMessage(f"연결됨: {port}")
            for btn in self._control_buttons:
                btn.setEnabled(True)
            self._apply_btn.setEnabled(True)
        else:
            self._conn_label.setText("●  Disconnected")
            self._conn_label.setStyleSheet("color: red; font-weight: bold;")
            self._connect_btn.setText("Connect")
            self._connect_btn.setChecked(False)
            self._status_bar.showMessage("연결 안 됨")
            for btn in self._control_buttons:
                btn.setEnabled(False)
            self._apply_btn.setEnabled(False)
            self._scan_btn.setChecked(False)
            self._scan_btn.setText("▶  SCAN")

    def _on_error(self, msg: str):
        self._log(f"[ERROR] {msg}", color="#ef9a9a")
        self._status_bar.showMessage(f"오류: {msg}")

    # -----------------------------------------------------------------------
    #  로그 헬퍼
    # -----------------------------------------------------------------------
    def _log(self, text: str, color: str = "#d4d4d4"):
        self._log_edit.append(
            f'<span style="color:{color};">{text}</span>'
        )
        # 자동 스크롤
        sb = self._log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    # -----------------------------------------------------------------------
    #  종료 처리
    # -----------------------------------------------------------------------
    def closeEvent(self, event):
        self._comm.disconnect()
        event.accept()
