"""
main.py — HDD Controller GUI 진입점
"""

import sys
import os

# gui/ 디렉토리를 Python 경로에 추가 (직접 실행 시)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPalette, QColor
from PyQt5.QtCore import Qt

from main_window import MainWindow


def apply_dark_palette(app: QApplication) -> None:
    """전체 앱에 다크 팔레트 적용."""
    app.setStyle("Fusion")
    palette = QPalette()
    dark   = QColor(45, 45, 45)
    darker = QColor(30, 30, 30)
    mid    = QColor(60, 60, 60)
    text   = QColor(220, 220, 220)
    accent = QColor(100, 160, 255)

    palette.setColor(QPalette.Window,          dark)
    palette.setColor(QPalette.WindowText,      text)
    palette.setColor(QPalette.Base,            darker)
    palette.setColor(QPalette.AlternateBase,   dark)
    palette.setColor(QPalette.ToolTipBase,     text)
    palette.setColor(QPalette.ToolTipText,     text)
    palette.setColor(QPalette.Text,            text)
    palette.setColor(QPalette.Button,          mid)
    palette.setColor(QPalette.ButtonText,      text)
    palette.setColor(QPalette.BrightText,      Qt.red)
    palette.setColor(QPalette.Link,            accent)
    palette.setColor(QPalette.Highlight,       accent)
    palette.setColor(QPalette.HighlightedText, Qt.black)

    app.setPalette(palette)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--arduino-port", default="/dev/cu.usbserial-1120")
    parser.add_argument("--esp32-port",   default="/dev/cu.usbserial-0001")
    args, qt_args = parser.parse_known_args()

    app = QApplication([sys.argv[0]] + qt_args)
    apply_dark_palette(app)
    window = MainWindow(
        arduino_port=args.arduino_port,
        esp32_port=args.esp32_port,
    )
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
