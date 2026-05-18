"""
main_window.py — HDD Controller GUI  (PyQt5)
듀얼 MCU: Arduino (rotor + arm) + ESP32 (strain gauge + Z-axis voice coil)
"""

from __future__ import annotations

import csv
import math
from datetime import datetime

import numpy as np

from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg

from PyQt5.QtCore    import Qt, QTimer
from PyQt5.QtGui     import QFont
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QPushButton, QComboBox,
    QTextEdit, QSplitter, QFormLayout, QSpinBox,
    QStatusBar, QMessageBox, QSizePolicy,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QFileDialog, QTabWidget,
)

from arduino_comm import ArduinoComm
from esp32_comm   import Esp32Comm


# ---------------------------------------------------------------------------
#  Arduino 파라미터 정의 (key, label, min, max, default)
# ---------------------------------------------------------------------------
SPEED_PARAMS: list[tuple] = [
    ("maxS",         "Max Speed (0-255)",       0,   255, 110),
    ("startStepD",   "Start Step Delay (ms)",   1,  5000, 100),
    ("targetStepD",  "Target Step Delay (ms)",  1,  5000,  20),
]
ARM_PARAMS: list[tuple] = [
    ("minArmPWM",  "Min Arm PWM",   0, 799,  63),
    ("maxArmPWM",  "Max Arm PWM",   0, 799, 600),
    ("pwmStep",    "Arm PWM Step",  1,  50,  10),
]
SCAN_PARAMS: list[tuple] = [
    ("rotationsPerMove", "Rotations Per Move", 1, 9999, 6),
]
KICK_PARAMS: list[tuple] = [
    ("kickAmount",   "Kick Amount",          0, 799, 157),
    ("kickDuration", "Kick Duration (ms)",   0, 999, 100),
]
ALL_PARAM_GROUPS: list[tuple] = [
    ("Speed",   SPEED_PARAMS),
    ("Arm PWM", ARM_PARAMS),
    ("Scan",    SCAN_PARAMS),
    ("Kick",    KICK_PARAMS),
]

MERGED_COLUMNS = ["Timestamp", "r (arm PWM)", "θ (rot step)", "Strain avg", "N samples", "Z target"]

# 테이블에는 전체 trigger 중 이 간격마다 1행만 표시 (전체 데이터는 _merged_rows에 보존)
_TABLE_STRIDE = 50


# ---------------------------------------------------------------------------
#  MainWindow
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, arduino_port: str | None = None, esp32_port: str | None = None):
        super().__init__()
        self.setWindowTitle("HDD Controller")
        self.resize(1280, 800)

        # ── Arduino comm ──────────────────────────────────────────────────
        self._arduino = ArduinoComm(self)
        self._arduino.data_received.connect(self._on_arduino_data)
        self._arduino.connection_changed.connect(self._on_arduino_conn_changed)
        self._arduino.error_occurred.connect(
            lambda m: self._log_arduino(f"[ERROR] {m}", "#ef9a9a"))

        # ── ESP32 comm ────────────────────────────────────────────────────
        self._esp32 = Esp32Comm(self)
        self._esp32.data_received.connect(self._on_esp32_data)
        self._esp32.trig_received.connect(self._on_trig_received)
        self._esp32.dtrig_received.connect(self._on_dtrig_received)
        self._esp32.rev_received.connect(self._on_rev_received)
        self._esp32.connection_changed.connect(self._on_esp32_conn_changed)
        self._esp32.error_occurred.connect(
            lambda m: self._log_esp32(f"[ERROR] {m}", "#ef9a9a"))

        self._param_spinboxes: dict[str, QSpinBox] = {}
        self._arduino_port = arduino_port
        self._esp32_port   = esp32_port
        self._merged_rows: list[tuple] = []
        self._current_z_target: int = 0

        # polar plot data — dict[(r, theta_rounded)] = strain (좌표당 1개 값만 유지)
        self._polar_dict: dict[tuple, float] = {}
        self._polar_dirty = False
        self._prev_trig_theta: float | None = None  # for inter-trigger theta interpolation
        self._prev_trig_r:     int   | None = None  # r 변경 감지용
        self._trig_count = 0  # 수신된 trigger 총 횟수 (테이블 표시 throttle용)

        self._build_ui()
        self._setup_port_refresh_timer()

        # polar redraw timer (300 ms)
        self._polar_timer = QTimer(self)
        self._polar_timer.timeout.connect(self._flush_polar_plot)
        self._polar_timer.start(300)

        if self._arduino_port:
            QTimer.singleShot(500, self._auto_connect_arduino)
        if self._esp32_port:
            QTimer.singleShot(700, self._auto_connect_esp32)

    # =======================================================================
    #  UI 구성
    # =======================================================================
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        conn_row = QHBoxLayout()
        conn_row.addWidget(self._build_arduino_conn_bar())
        conn_row.addWidget(self._build_esp32_conn_bar())
        root.addLayout(conn_row)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_arduino_tab(), "Arduino  (Rotor + Arm)")
        self._tabs.addTab(self._build_esp32_tab(),   "ESP32  (Z-axis + Strain)")
        self._tabs.addTab(self._build_merged_tab(),  "Merged Data")
        root.addWidget(self._tabs, stretch=1)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("연결 안 됨")

    # ── Arduino 연결 바 ───────────────────────────────────────────────────────
    def _build_arduino_conn_bar(self) -> QGroupBox:
        box = QGroupBox("Arduino  (usbserial-1120)")
        layout = QHBoxLayout(box)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Port:"))
        self._arduino_port_combo = QComboBox()
        self._arduino_port_combo.setMinimumWidth(160)
        layout.addWidget(self._arduino_port_combo)

        r = QPushButton("↻"); r.setFixedWidth(28)
        r.clicked.connect(self._refresh_ports)
        layout.addWidget(r)

        layout.addWidget(QLabel("Baud:"))
        self._arduino_baud_combo = QComboBox()
        for b in ("9600", "19200", "57600", "115200"):
            self._arduino_baud_combo.addItem(b)
        self._arduino_baud_combo.setCurrentText("9600")
        layout.addWidget(self._arduino_baud_combo)

        self._arduino_conn_btn = QPushButton("Connect")
        self._arduino_conn_btn.setFixedWidth(85)
        self._arduino_conn_btn.setCheckable(True)
        self._arduino_conn_btn.clicked.connect(self._toggle_arduino_conn)
        layout.addWidget(self._arduino_conn_btn)

        self._arduino_status_lbl = QLabel("●  Disconnected")
        self._arduino_status_lbl.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(self._arduino_status_lbl)
        layout.addStretch()
        return box

    # ── ESP32 연결 바 ─────────────────────────────────────────────────────────
    def _build_esp32_conn_bar(self) -> QGroupBox:
        box = QGroupBox("ESP32  (usbserial-0001)")
        layout = QHBoxLayout(box)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Port:"))
        self._esp32_port_combo = QComboBox()
        self._esp32_port_combo.setMinimumWidth(160)
        layout.addWidget(self._esp32_port_combo)

        r = QPushButton("↻"); r.setFixedWidth(28)
        r.clicked.connect(self._refresh_ports)
        layout.addWidget(r)

        layout.addWidget(QLabel("Baud:"))
        self._esp32_baud_combo = QComboBox()
        for b in ("9600", "19200", "57600", "115200", "460800", "921600"):
            self._esp32_baud_combo.addItem(b)
        self._esp32_baud_combo.setCurrentText("460800")
        layout.addWidget(self._esp32_baud_combo)

        self._esp32_conn_btn = QPushButton("Connect")
        self._esp32_conn_btn.setFixedWidth(85)
        self._esp32_conn_btn.setCheckable(True)
        self._esp32_conn_btn.clicked.connect(self._toggle_esp32_conn)
        layout.addWidget(self._esp32_conn_btn)

        self._esp32_status_lbl = QLabel("●  Disconnected")
        self._esp32_status_lbl.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(self._esp32_status_lbl)
        layout.addStretch()
        return box

    # ── Arduino 탭 ───────────────────────────────────────────────────────────
    def _build_arduino_tab(self) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self._build_arduino_control_panel())
        ll.addWidget(self._build_param_panel())
        ll.addStretch()

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self._build_arduino_log_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)
        return w

    def _build_arduino_control_panel(self) -> QGroupBox:
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

        self._arduino_ctrl_btns = [self._scan_btn, reset_btn, stop_btn, status_btn]
        for btn in self._arduino_ctrl_btns:
            btn.setEnabled(False)
        return box

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

    def _build_arduino_log_panel(self) -> QGroupBox:
        box = QGroupBox("Arduino Serial Log")
        layout = QVBoxLayout(box)
        self._arduino_log = QTextEdit()
        self._arduino_log.setReadOnly(True)
        self._arduino_log.setFont(QFont("Courier New", 10))
        self._arduino_log.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4;")
        self._arduino_log.document().setMaximumBlockCount(500)
        layout.addWidget(self._arduino_log, stretch=1)
        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear"); clear_btn.setFixedWidth(70)
        clear_btn.clicked.connect(self._arduino_log.clear)
        btn_row.addStretch(); btn_row.addWidget(clear_btn)
        layout.addLayout(btn_row)
        return box

    # ── ESP32 탭 ─────────────────────────────────────────────────────────────
    def _build_esp32_tab(self) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self._build_esp32_control_panel())
        ll.addWidget(self._build_esp32_pi_panel())
        ll.addStretch()

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self._build_esp32_log_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)
        return w

    def _build_esp32_control_panel(self) -> QGroupBox:
        box = QGroupBox("Z-Axis Control  (Voice Coil)")
        form = QFormLayout(box)
        form.setSpacing(8)

        self._z_target_sb = QSpinBox()
        self._z_target_sb.setRange(0, 4095)
        self._z_target_sb.setValue(0)
        self._z_target_sb.setMinimumWidth(100)
        form.addRow("Z Target (ADC units):", self._z_target_sb)

        self._strain_lbl = QLabel("—")
        self._strain_lbl.setStyleSheet("color: #4fc3f7; font-weight: bold; font-size: 13px;")
        form.addRow("Strain (live):", self._strain_lbl)

        self._z_apply_btn = QPushButton("Set Z Target")
        self._z_apply_btn.setMinimumHeight(34)
        self._z_apply_btn.setStyleSheet(
            "QPushButton { background-color: #6a1b9a; color: white; font-size: 13px; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #555; }"
        )
        self._z_apply_btn.setEnabled(False)
        self._z_apply_btn.clicked.connect(self._on_set_z_target)
        form.addRow(self._z_apply_btn)

        btn_row = QHBoxLayout()
        self._z_stop_btn = QPushButton("■ STOP")
        self._z_stop_btn.setStyleSheet(
            "QPushButton { background-color: #b71c1c; color: white; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #555; }"
        )
        self._z_stop_btn.setEnabled(False)
        self._z_stop_btn.clicked.connect(self._esp32.cmd_stop)
        btn_row.addWidget(self._z_stop_btn)

        self._z_home_btn = QPushButton("⌂ HOME")
        self._z_home_btn.setStyleSheet(
            "QPushButton { background-color: #1565c0; color: white; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #555; }"
        )
        self._z_home_btn.setEnabled(False)
        self._z_home_btn.clicked.connect(self._on_z_home)
        btn_row.addWidget(self._z_home_btn)

        self._esp32_status_btn = QPushButton("? STATUS")
        self._esp32_status_btn.setEnabled(False)
        self._esp32_status_btn.clicked.connect(self._esp32.get_status)
        btn_row.addWidget(self._esp32_status_btn)
        form.addRow(btn_row)

        self._esp32_ctrl_btns = [self._z_apply_btn, self._z_stop_btn,
                                  self._z_home_btn, self._esp32_status_btn]
        return box

    def _build_esp32_pi_panel(self) -> QGroupBox:
        box = QGroupBox("PI Parameters")
        form = QFormLayout(box)
        form.setSpacing(6)

        self._kp_sb = QSpinBox()
        self._kp_sb.setRange(0, 10000); self._kp_sb.setValue(100)
        self._kp_sb.setSuffix("  (×0.01)")
        form.addRow("Kp:", self._kp_sb)

        self._ki_sb = QSpinBox()
        self._ki_sb.setRange(0, 10000); self._ki_sb.setValue(5)
        self._ki_sb.setSuffix("  (×0.01)")
        form.addRow("Ki:", self._ki_sb)

        self._strain_gain_sb = QSpinBox()
        self._strain_gain_sb.setRange(1, 100000); self._strain_gain_sb.setValue(1000)
        self._strain_gain_sb.setSuffix("  (×0.001)")
        form.addRow("Strain Gain:", self._strain_gain_sb)

        self._strain_offset_sb = QSpinBox()
        self._strain_offset_sb.setRange(-4095, 4095); self._strain_offset_sb.setValue(0)
        form.addRow("Strain Offset (ADC):", self._strain_offset_sb)

        self._trig_mode_combo = QComboBox()
        self._trig_mode_combo.addItem("0 — trigger log only")
        self._trig_mode_combo.addItem("1 — continuous + trigger")
        form.addRow("Trigger Mode:", self._trig_mode_combo)

        self._pi_apply_btn = QPushButton("Apply PI Params")
        self._pi_apply_btn.setStyleSheet(
            "QPushButton { background-color: #e65100; color: white; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #555; }"
        )
        self._pi_apply_btn.setEnabled(False)
        self._pi_apply_btn.clicked.connect(self._on_apply_pi_params)
        form.addRow(self._pi_apply_btn)
        return box

    def _build_esp32_log_panel(self) -> QGroupBox:
        box = QGroupBox("ESP32 Serial Log")
        layout = QVBoxLayout(box)
        self._esp32_log = QTextEdit()
        self._esp32_log.setReadOnly(True)
        self._esp32_log.setFont(QFont("Courier New", 10))
        self._esp32_log.setStyleSheet("background-color: #1a1a2e; color: #d4d4d4;")
        self._esp32_log.document().setMaximumBlockCount(500)
        layout.addWidget(self._esp32_log, stretch=1)
        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear"); clear_btn.setFixedWidth(70)
        clear_btn.clicked.connect(self._esp32_log.clear)
        btn_row.addStretch(); btn_row.addWidget(clear_btn)
        layout.addLayout(btn_row)
        return box

    # ── Merged Data 탭 ────────────────────────────────────────────────────────
    def _build_merged_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)

        info = QLabel(
            "Arduino phase 변화마다 trigger 신호가 발생합니다. "
            "ESP32는 trigger 수신 시 직전 구간의 strain gauge 평균값을 r/θ 위치와 함께 전송합니다."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(info)

        # ── splitter: 테이블(좌) | polar graph(우) ─────────────────────
        splitter = QSplitter(Qt.Horizontal)

        # 좌: 테이블
        self._merged_table = QTableWidget()
        self._merged_table.setColumnCount(len(MERGED_COLUMNS))
        self._merged_table.setHorizontalHeaderLabels(MERGED_COLUMNS)
        self._merged_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._merged_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._merged_table.setAlternatingRowColors(True)
        self._merged_table.setStyleSheet(
            "background-color: #1e1e1e; color: #d4d4d4; "
            "alternate-background-color: #252525;"
        )
        splitter.addWidget(self._merged_table)

        # 우: polar graph
        polar_wrap = QWidget()
        polar_vbox = QVBoxLayout(polar_wrap)
        polar_vbox.setContentsMargins(4, 0, 0, 0)

        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("Steps/Rev:"))
        self._steps_per_rev_sb = QSpinBox()
        self._steps_per_rev_sb.setRange(1, 999999)
        self._steps_per_rev_sb.setValue(18)
        self._steps_per_rev_sb.setToolTip("1회전당 trigger 횟수 (theta 정규화에 사용)")
        ctrl_row.addWidget(self._steps_per_rev_sb)
        ctrl_row.addStretch()
        polar_vbox.addLayout(ctrl_row)

        self._polar_fig = Figure(facecolor="#1e1e1e")
        self._polar_ax  = self._polar_fig.add_subplot(111, projection="polar")
        self._polar_ax.set_facecolor("#1e1e1e")
        self._polar_ax.tick_params(colors="#888", labelsize=7)
        self._polar_ax.spines["polar"].set_color("#444")
        self._polar_ax.set_theta_zero_location("N")
        self._polar_ax.set_theta_direction(-1)
        self._polar_fig.tight_layout(pad=1.0)

        # scatter 객체를 한 번만 생성, 이후 set_offsets/set_array로 재사용
        # 더미 점 1개로 초기화해야 colormap이 제대로 바인딩됨
        self._scatter = self._polar_ax.scatter(
            [0], [0], c=[0], cmap="plasma", vmin=0, vmax=1,
            s=12, alpha=0.85, linewidths=0,
        )
        self._scatter.set_visible(False)  # 더미 점 숨김
        self._polar_ax.set_rlim(0, 800)   # arm PWM 범위 고정 (0-799)

        self._polar_canvas = FigureCanvasQTAgg(self._polar_fig)
        self._polar_canvas.setStyleSheet("background-color: #1e1e1e;")
        polar_vbox.addWidget(self._polar_canvas, stretch=1)

        splitter.addWidget(polar_wrap)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

        # ── 하단 버튼 열 ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._row_count_lbl = QLabel("0 rows")
        self._row_count_lbl.setStyleSheet("color: #aaa;")
        btn_row.addWidget(self._row_count_lbl)
        btn_row.addStretch()

        clear_tbl_btn = QPushButton("Clear Table"); clear_tbl_btn.setFixedWidth(100)
        clear_tbl_btn.clicked.connect(self._clear_merged_table)
        btn_row.addWidget(clear_tbl_btn)

        export_btn = QPushButton("Export CSV…"); export_btn.setFixedWidth(110)
        export_btn.setStyleSheet(
            "QPushButton { background-color: #1b5e20; color: white; border-radius: 4px; }"
        )
        export_btn.clicked.connect(self._export_csv)
        btn_row.addWidget(export_btn)
        layout.addLayout(btn_row)
        return w

    # ── polar plot 갱신 ───────────────────────────────────────────────────
    def _flush_polar_plot(self):
        if not self._polar_dirty or not self._polar_dict:
            return
        self._polar_dirty = False

        steps = max(1, self._steps_per_rev_sb.value())
        keys        = list(self._polar_dict.keys())
        strain_arr  = np.array([self._polar_dict[k] for k in keys], dtype=float)
        theta_arr   = np.array([2.0 * math.pi * (k[1] % steps) / steps for k in keys])
        r_arr       = np.array([k[0] for k in keys], dtype=float)

        vmin = float(np.percentile(strain_arr, 2))
        vmax = float(np.percentile(strain_arr, 98))
        if vmin == vmax:
            vmax = vmin + 1

        # ax.clear() 없이 scatter 객체 데이터만 교체
        self._scatter.set_offsets(np.column_stack([theta_arr, r_arr]))
        self._scatter.set_array(strain_arr)
        self._scatter.set_clim(vmin, vmax)
        self._scatter.set_visible(True)
        self._polar_ax.set_rlim(0, max(float(r_arr.max()) * 1.05 + 1, 10))
        self._polar_canvas.draw_idle()

    # =======================================================================
    #  창 닫기 — scan 중지 및 연결 해제
    # =======================================================================
    def closeEvent(self, event):
        # Arduino: scan 중이면 정지
        if self._scan_btn.isChecked():
            self._arduino.cmd_stop()
        self._arduino.disconnect()
        self._esp32.disconnect()
        event.accept()

    # =======================================================================
    #  포트 갱신
    # =======================================================================
    def _setup_port_refresh_timer(self):
        self._refresh_ports()
        timer = QTimer(self)
        timer.timeout.connect(self._refresh_ports)
        timer.start(3000)

    def _refresh_ports(self):
        ports = ArduinoComm.list_ports()
        for combo in (self._arduino_port_combo, self._esp32_port_combo):
            current = combo.currentText()
            combo.clear()
            if ports:
                combo.addItems(ports)
                if current in ports:
                    combo.setCurrentText(current)
            else:
                combo.addItem("(없음)")

    # =======================================================================
    #  자동 연결
    # =======================================================================
    def _auto_connect_arduino(self):
        port = self._arduino_port
        if self._arduino_port_combo.findText(port) == -1:
            self._arduino_port_combo.insertItem(0, port)
        self._arduino_port_combo.setCurrentText(port)
        self._arduino_conn_btn.setChecked(True)
        self._toggle_arduino_conn(True)

    def _auto_connect_esp32(self):
        port = self._esp32_port
        if self._esp32_port_combo.findText(port) == -1:
            self._esp32_port_combo.insertItem(0, port)
        self._esp32_port_combo.setCurrentText(port)
        self._esp32_conn_btn.setChecked(True)
        self._toggle_esp32_conn(True)

    # =======================================================================
    #  연결 토글
    # =======================================================================
    def _toggle_arduino_conn(self, checked: bool):
        if checked:
            port = self._arduino_port_combo.currentText()
            if port in ("(없음)", ""):
                self._arduino_conn_btn.setChecked(False)
                QMessageBox.warning(self, "포트 없음", "Arduino 포트가 없습니다.")
                return
            baud = int(self._arduino_baud_combo.currentText())
            if not self._arduino.connect(port, baud):
                self._arduino_conn_btn.setChecked(False)
        else:
            self._arduino.disconnect()

    def _toggle_esp32_conn(self, checked: bool):
        if checked:
            port = self._esp32_port_combo.currentText()
            if port in ("(없음)", ""):
                self._esp32_conn_btn.setChecked(False)
                QMessageBox.warning(self, "포트 없음", "ESP32 포트가 없습니다.")
                return
            baud = int(self._esp32_baud_combo.currentText())
            if not self._esp32.connect(port, baud):
                self._esp32_conn_btn.setChecked(False)
        else:
            self._esp32.disconnect()

    # =======================================================================
    #  Arduino 버튼 핸들러
    # =======================================================================
    def _on_scan_clicked(self, checked: bool):
        if checked:
            self._arduino.cmd_scan()
            self._scan_btn.setText("⏸  SCANNING…")
            self._log_arduino("→ CMD SCAN 전송")
        else:
            self._scan_btn.setText("▶  SCAN")

    def _on_reset_clicked(self):
        self._scan_btn.setChecked(False)
        self._scan_btn.setText("▶  SCAN")
        self._arduino.cmd_reset()
        self._log_arduino("→ CMD RESET 전송")

    def _on_stop_clicked(self):
        self._scan_btn.setChecked(False)
        self._scan_btn.setText("▶  SCAN")
        self._arduino.cmd_stop()
        self._log_arduino("→ CMD STOP 전송")

    def _on_get_status_clicked(self):
        self._arduino.get_status()
        self._log_arduino("→ GET STATUS 전송")

    def _on_apply_params(self):
        sent = []
        for key, sb in self._param_spinboxes.items():
            self._arduino.set_param(key, sb.value())
            sent.append(f"{key}={sb.value()}")
        self._log_arduino(f"→ Parameters applied: {', '.join(sent)}")

    # =======================================================================
    #  ESP32 버튼 핸들러
    # =======================================================================
    def _on_set_z_target(self):
        val = self._z_target_sb.value()
        self._current_z_target = val
        self._esp32.set_z_target(val)
        self._log_esp32(f"→ SET Z_TARGET {val}")

    def _on_z_home(self):
        self._z_target_sb.setValue(0)
        self._current_z_target = 0
        self._esp32.cmd_home()
        self._log_esp32("→ CMD HOME")

    def _on_apply_pi_params(self):
        self._esp32.set_kp(self._kp_sb.value())
        self._esp32.set_ki(self._ki_sb.value())
        self._esp32.set_strain_gain(self._strain_gain_sb.value())
        self._esp32.set_strain_offset(self._strain_offset_sb.value())
        self._esp32.set_trig_mode(self._trig_mode_combo.currentIndex())
        self._log_esp32(
            f"→ PI params: kp={self._kp_sb.value()} ki={self._ki_sb.value()} "
            f"gain={self._strain_gain_sb.value()} "
            f"offset={self._strain_offset_sb.value()} "
            f"trig_mode={self._trig_mode_combo.currentIndex()}"
        )

    # =======================================================================
    #  Merged data 핸들러
    # =======================================================================
    def _on_trig_received(self, data: dict):
        """ESP32 TRIG: 수신 → 로그/테이블만 기록. polar dict는 REV:만 담당."""
        ts         = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        r          = data["r"]
        theta      = data["theta"]
        strain_avg = data["strain_avg"]
        n          = data["n"]
        z_tgt      = self._current_z_target

        self._trig_count += 1
        self._merged_rows.append((ts, r, theta, strain_avg, n, z_tgt))
        if len(self._merged_rows) > 5000:
            self._merged_rows = self._merged_rows[2500:]

        if self._trig_count % _TABLE_STRIDE == 1:
            row_idx = self._merged_table.rowCount()
            self._merged_table.insertRow(row_idx)
            for col, val in enumerate([ts, r, theta, strain_avg, n, z_tgt]):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignCenter)
                self._merged_table.setItem(row_idx, col, item)
            self._merged_table.scrollToBottom()

        self._row_count_lbl.setText(f"{len(self._merged_rows)} rows (table: every {_TABLE_STRIDE}th)")
        self._strain_lbl.setText(str(strain_avg))
        # polar plot은 REV: 핸들러에서만 갱신 → TRIG:는 polar dict 건드리지 않음

    def _on_rev_received(self, data: dict):
        """ESP32 REV: 수신 → polar dict에 18개 점 직접 기록 (보간 없음)."""
        ts    = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        r     = data["r"]
        vals  = data["data"]
        z_tgt = self._current_z_target

        for theta_idx, sv in enumerate(vals):
            self._polar_dict[(r, float(theta_idx))] = sv

        self._trig_count += len(vals)
        for theta_idx, sv in enumerate(vals):
            self._merged_rows.append((ts, r, theta_idx, sv, 1, z_tgt))
        if len(self._merged_rows) > 5000:
            self._merged_rows = self._merged_rows[2500:]

        self._row_count_lbl.setText(
            f"{len(self._merged_rows)} rows (table: every {_TABLE_STRIDE}th)")
        self._polar_dirty = True
        self._flush_polar_plot()   # 링 완성 즉시 그리기 (DTRIG 누적 포함)

    def _on_dtrig_received(self, data: dict):
        """ESP32 DTRIG: 수신 → data rev 구간 샘플을 sub-theta 보간으로 polar dict에 기록.

        tidx 구간: 모터가 thetaStep=tidx에서 thetaStep=tidx+1로 이동하는 동안 수집된 샘플.
        샘플 k (0-based) 는 선형 보간으로 theta = tidx + (k+0.5)/n 에 할당.
        """
        r       = data["r"]
        tidx    = data["tidx"]
        samples = data["samples"]
        n       = len(samples)
        if n == 0:
            return
        steps = max(1, self._steps_per_rev_sb.value())

        for k, sv in enumerate(samples):
            sub_idx = (tidx + (k + 0.5) / n) % steps  # float 0..steps
            self._polar_dict[(r, round(sub_idx, 3))] = sv

        # _polar_dirty는 설정하지 않음 — 링 완성(REV:) 때만 갱신

    def _clear_merged_table(self):
        self._merged_table.setRowCount(0)
        self._merged_rows.clear()
        self._polar_dict.clear()
        self._prev_trig_theta = None
        self._prev_trig_r     = None
        self._trig_count = 0
        self._scatter.set_offsets(np.empty((0, 2)))
        self._scatter.set_array(np.array([]))
        self._polar_canvas.draw_idle()
        self._row_count_lbl.setText("0 rows")

    def _export_csv(self):
        if not self._merged_rows:
            QMessageBox.information(self, "없음", "내보낼 데이터가 없습니다.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV 저장",
            f"hdd_scan_{datetime.now():%Y%m%d_%H%M%S}.csv",
            "CSV Files (*.csv)"
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(MERGED_COLUMNS)
            writer.writerows(self._merged_rows)
        self._status_bar.showMessage(f"CSV 저장됨: {path}")

    # =======================================================================
    #  Signal 핸들러 — Arduino
    # =======================================================================
    def _on_arduino_data(self, line: str):
        self._log_arduino(f"← {line}", "#a8e6a3")

    def _on_arduino_conn_changed(self, connected: bool):
        if connected:
            port = self._arduino_port_combo.currentText()
            self._arduino_status_lbl.setText(f"●  {port}")
            self._arduino_status_lbl.setStyleSheet("color: #4caf50; font-weight: bold;")
            self._arduino_conn_btn.setText("Disconnect")
            self._status_bar.showMessage(f"Arduino 연결됨: {port}")
            for btn in self._arduino_ctrl_btns:
                btn.setEnabled(True)
            self._apply_btn.setEnabled(True)
        else:
            self._arduino_status_lbl.setText("●  Disconnected")
            self._arduino_status_lbl.setStyleSheet("color: red; font-weight: bold;")
            self._arduino_conn_btn.setText("Connect")
            self._arduino_conn_btn.setChecked(False)
            self._status_bar.showMessage("Arduino 연결 끊김")
            for btn in self._arduino_ctrl_btns:
                btn.setEnabled(False)
            self._apply_btn.setEnabled(False)
            self._scan_btn.setChecked(False)
            self._scan_btn.setText("▶  SCAN")

    # =======================================================================
    #  Signal 핸들러 — ESP32
    # =======================================================================
    def _on_esp32_data(self, line: str):
        if line.startswith("Z_STATUS:"):
            for part in line[len("Z_STATUS:"):].split():
                if part.startswith("actual="):
                    self._strain_lbl.setText(part.split("=")[1])
        self._log_esp32(f"← {line}", "#80cbc4")

    def _on_esp32_conn_changed(self, connected: bool):
        if connected:
            port = self._esp32_port_combo.currentText()
            self._esp32_status_lbl.setText(f"●  {port}")
            self._esp32_status_lbl.setStyleSheet("color: #4caf50; font-weight: bold;")
            self._esp32_conn_btn.setText("Disconnect")
            self._status_bar.showMessage(f"ESP32 연결됨: {port}")
            for btn in self._esp32_ctrl_btns:
                btn.setEnabled(True)
            self._pi_apply_btn.setEnabled(True)
        else:
            self._esp32_status_lbl.setText("●  Disconnected")
            self._esp32_status_lbl.setStyleSheet("color: red; font-weight: bold;")
            self._esp32_conn_btn.setText("Connect")
            self._esp32_conn_btn.setChecked(False)
            self._status_bar.showMessage("ESP32 연결 끊김")
            for btn in self._esp32_ctrl_btns:
                btn.setEnabled(False)
            self._pi_apply_btn.setEnabled(False)
            self._strain_lbl.setText("—")

    # =======================================================================
    #  로그 헬퍼
    # =======================================================================
    def _log_arduino(self, text: str, color: str = "#d4d4d4"):
        self._arduino_log.append(f'<span style="color:{color};">{text}</span>')
        sb = self._arduino_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _log_esp32(self, text: str, color: str = "#d4d4d4"):
        self._esp32_log.append(f'<span style="color:{color};">{text}</span>')
        sb = self._esp32_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    # =======================================================================
    #  종료 처리
    # =======================================================================
    def closeEvent(self, event):
        self._arduino.disconnect()
        self._esp32.disconnect()
        event.accept()
