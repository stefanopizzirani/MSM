import psutil
import os
import logging
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QTextEdit, QProgressBar, QPushButton, QFrame)
from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QTextCursor, QColor, QFont


class LogSignalRelay(QObject):
    """Bridge to help relay log messages from background threads to the UI thread."""
    message_received = pyqtSignal(str)


class QtLogHandler(logging.Handler):
    """Custom logging handler that emits records to a QTextEdit."""
    def __init__(self, text_widget: QTextEdit):
        super().__init__()
        self.text_widget = text_widget
        self.relay = LogSignalRelay()
        self.relay.message_received.connect(self._safe_append)
        self.setFormatter(logging.Formatter(
            '%(asctime)s  [%(levelname)s]  %(name)s - %(message)s',
            datefmt='%H:%M:%S'
        ))
        self.level_colors = {
            logging.DEBUG:    "#909090",
            logging.INFO:     "#d4d4d4",
            logging.WARNING:  "#f0c040",
            logging.ERROR:    "#f04040",
            logging.CRITICAL: "#ff0000",
        }

    def emit(self, record):
        try:
            msg = self.format(record)
            color = self.level_colors.get(record.levelno, "#d4d4d4")
            html = f'<span style="color:{color}; font-family: monospace;">{msg}</span><br>'
            # Relay to main thread for safe UI update
            self.relay.message_received.emit(html.replace('\n', '<br>'))
        except Exception:
            self.handleError(record)

    def _safe_append(self, html):
        self.text_widget.append(html)
        # Auto-scroll to bottom
        self.text_widget.moveCursor(QTextCursor.MoveOperation.End)


class MetricBar(QFrame):
    """A labeled progress bar for CPU/RAM display."""
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self.lbl = QLabel(label)
        self.lbl.setFixedWidth(70)
        self.lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.lbl)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%p%")
        self.bar.setFixedHeight(18)
        layout.addWidget(self.bar)

        self.val_lbl = QLabel("0%")
        self.val_lbl.setFixedWidth(46)
        self.val_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.val_lbl)

    def set_value(self, pct: float, extra=""):
        v = int(pct)
        self.bar.setValue(v)
        self.val_lbl.setText(extra or f"{v}%")
        # Color gradient: green -> yellow -> red
        if v < 60:
            color = "#4caf50"
        elif v < 80:
            color = "#f0c040"
        else:
            color = "#f04040"
        self.bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {color}; border-radius: 2px; }}"
        )


class DiagnosticsTab(QWidget):
    """
    A dedicated tab that:
      - Intercepts all Python logging and renders it with color coding.
      - Displays live CPU/RAM usage via psutil.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = psutil.Process(os.getpid())

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # ── System Metrics row ─────────────────────────────────────────────
        metrics_frame = QFrame()
        metrics_frame.setFrameShape(QFrame.Shape.StyledPanel)
        mf_layout = QVBoxLayout(metrics_frame)
        mf_layout.setContentsMargins(8, 6, 8, 6)

        metrics_header = QLabel("⚡  System Metrics")
        metrics_header.setStyleSheet("font-weight: bold; font-size: 13px;")
        mf_layout.addWidget(metrics_header)

        self.cpu_bar = MetricBar("CPU:")
        self.ram_bar = MetricBar("RAM:")
        self.proc_bar = MetricBar("Process:")
        mf_layout.addWidget(self.cpu_bar)
        mf_layout.addWidget(self.ram_bar)
        mf_layout.addWidget(self.proc_bar)

        main_layout.addWidget(metrics_frame)

        # ── Log Viewer ────────────────────────────────────────────────────
        log_header_row = QHBoxLayout()
        log_label = QLabel("📋  Application Log")
        log_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        log_header_row.addWidget(log_label)
        log_header_row.addStretch()

        btn_clear = QPushButton("🗑  Clear")
        btn_clear.setFixedWidth(90)
        btn_clear.clicked.connect(self._clear_log)
        log_header_row.addWidget(btn_clear)
        main_layout.addLayout(log_header_row)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Monospace", 9))
        self.log_view.setStyleSheet(
            "background-color: #1a1a1a; color: #d4d4d4;"
            "border: 1px solid #333; border-radius: 4px;"
        )
        main_layout.addWidget(self.log_view, stretch=1)

        # ── Attach to root logger ─────────────────────────────────────────
        self._log_handler = QtLogHandler(self.log_view)
        self._log_handler.setLevel(logging.DEBUG)
        
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(self._log_handler)
        
        logging.info("⚡ Diagnostics: System Log Interceptor started and set to INFO.")

        # ── Timer ─────────────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(2000)   # every 2 s
        self._timer.timeout.connect(self._refresh_metrics)
        self._timer.start()
        self._refresh_metrics()         # prime on startup

    # ── slots ──────────────────────────────────────────────────────────────
    def _refresh_metrics(self):
        try:
            cpu = psutil.cpu_percent(interval=None)
            vm = psutil.virtual_memory()
            proc_mem = self._process.memory_info().rss / (1024 ** 2)

            self.cpu_bar.set_value(cpu)
            self.ram_bar.set_value(vm.percent, f"{vm.used//(1024**2)} MB / {vm.total//(1024**2)} MB")
            self.proc_bar.set_value(proc_mem / (vm.total / 1024**2) * 100, f"{proc_mem:.1f} MB")
        except Exception:
            pass

    def _clear_log(self):
        self.log_view.clear()

    def closeEvent(self, event):
        self._timer.stop()
        logging.getLogger().removeHandler(self._log_handler)
        super().closeEvent(event)
