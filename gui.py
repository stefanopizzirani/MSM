"""
Module: gui.py
Description: The main GUI for Music Studio Manager 2.
Now refactored into a modular architecture using the 'ui/' package.
"""
import os
import sys
import logging
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow

# --- LOGGING ---
logger = logging.getLogger("MSM2_GUI")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%H:%M:%S')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Environment fixes
os.environ["QT_LOGGING_RULES"] = "qt.qpa.wayland.textinput=false"

# --- MAIN EXECUTION BLOCK ---
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion") # Dark Mode friendly style
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec())
