"""
Module: main.py
Meaning: The entry point of the Music Studio Manager application.
Working Procedure: Initializes the PyQt6 application environment, applies a modern UI theme, 
instantiates the MainWindow, and starts the event loop.
"""
import os
import ctypes
import sys
from PyQt6.QtWidgets import QApplication

# --- ENVIRONMENT FIXES ---
# Disable Numba's coverage tracing to avoid conflicts with system 'coverage' package
# Fixes error: module 'coverage.types' has no attribute 'Tracer'
os.environ["NUMBA_DISABLE_COVERAGE"] = "1"
# Disables the annoying Wayland logs related to textinput
os.environ["QT_LOGGING_RULES"] = "qt.qpa.wayland.textinput=false"

from PyQt6.QtWidgets import QApplication
from gui import MainWindow

# Gets the folder where the main.py file is physically located
basedir = os.path.dirname(os.path.abspath(__file__))
# Changes the current working directory to the file's directory
os.chdir(basedir)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setDesktopFileName("music-studio-manager")
    
    # Optional style to make the app look dark/modern natively
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.showMaximized()
    
    sys.exit(app.exec())