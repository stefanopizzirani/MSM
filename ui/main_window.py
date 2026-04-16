import os
import json
import threading
import logging
from PyQt6.QtWidgets import (QMainWindow, QWidget, QTabWidget, QMessageBox, QStyle)
from PyQt6.QtGui import QAction, QKeySequence, QKeyEvent, QShortcut, QUndoStack
from PyQt6.QtCore import Qt, QTimer
from dialogs import PatchLibraryDialog
from ui.themes import THEMES
from ui.setlist_tab import SetlistTab
from ui.diagnostics_tab import DiagnosticsTab
from ui.optimizer_tab import SetlistOptimizerTab
from translations import TRANSLATIONS

# Core Engines
from data_manager import DataManager
from sync_engine import SyncEngine
from audio_engine import AudioEngine
from mapping_manager import MappingManager

# Stem Creator / ChordPro
from Stem_create import DemucsGUI    
from ChordPro_create import ChordProGUI    

# Web Server components
try:
    from web_server import start_web_server, web_bridge, push_web_state
except ImportError:
    web_bridge = None
    start_web_server = None
    push_web_state = lambda: None 

#from confidence_server import start_confidence_monitor

logger = logging.getLogger("MSM2_MAIN")
logger.setLevel(logging.INFO)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Music Studio Manager")
        
        self.resize(1450, 850)
        self.showMaximized()
        logger.info("MSM2: Initializing core systems...")

        # 1. Initialize Core Engines
        self.data_manager = DataManager()
        self.sync_engine = SyncEngine()
        self.audio_engine = AudioEngine()
        self.mapping_manager = MappingManager(self.data_manager)
        self.mapping_manager.action_triggered.connect(self.handle_mapped_action)
        self.mapping_manager.start()
        
        self.undo_stack = QUndoStack(self)
        
        # DMX setup
        saved_dmx_config = self.data_manager.get_dmx_config()
        self.sync_engine.dmx_settings = saved_dmx_config
        self.sync_engine.dmx_universe = saved_dmx_config.get('universe', 0)
        
        # Load Sync / Sound / Mixer Settings
        mixer_settings = self.data_manager.config.get("mixer_settings", {})
        self.osc_ip = mixer_settings.get("osc_ip", "192.168.1.1")
        self.bpm_sync_targets = mixer_settings.get("bpm_sync_targets", [{"fx": 2, "param": 2}, {"fx": 4, "param": 2}])
        self.sync_engine.update_settings(self.osc_ip)
        self.sync_engine.set_bpm_sync_targets(self.bpm_sync_targets)

        # 2. State
        self.current_theme = self.data_manager.config.get("theme", "Modern Dark")
        self.current_language = self.data_manager.config.get("language", "English")

        # 3. Web Server Integration
        if web_bridge:
            self.web_thread = threading.Thread(target=start_web_server, args=(self.data_manager,), daemon=True)
            self.web_thread.start()
            
        #self.monitor_thread = threading.Thread(target=start_confidence_monitor, daemon=True)
        #self.monitor_thread.start()

        # 4. UI Setup
        self.init_ui()
        self.retranslate_ui()
        self.apply_theme(self.current_theme)
        self.setlist_tab.load_initial_data()
        logger.info(f"MSM2: Startup complete. Theme: {self.current_theme}, Language: {self.current_language}")
        
        # 5. Global Shortcuts
        self.fullscreen_shortcut = QShortcut(QKeySequence("F11"), self)
        self.fullscreen_shortcut.activated.connect(self.toggle_fullscreen)
        
        # 6. Timers
        self.playback_timer = QTimer(self)
        self.playback_timer.timeout.connect(self.setlist_tab.update_playback_ui)
        self.playback_timer.start(100)
        
        self.create_menu()  

    def init_ui(self):
        self.main_tabs = QTabWidget()
        self.setCentralWidget(self.main_tabs)
        
        # 1. SETLIST TAB
        self.setlist_tab = SetlistTab(self.data_manager, self.sync_engine, self.audio_engine, self.mapping_manager, self.undo_stack, self)
        self.main_tabs.addTab(self.setlist_tab, "Setlist & Player")
        
        # 2. OPTIMIZER TAB
        self.optimizer_tab = SetlistOptimizerTab(self.data_manager, self)
        self.main_tabs.addTab(self.optimizer_tab, "🪄 Setlist Optimizer")
        
        # 3. DIAGNOSTICS TAB
        self.diagnostics_tab = DiagnosticsTab()
        self.main_tabs.addTab(self.diagnostics_tab, "🔍 Diagnostics")
        
        self.main_tabs.setStyleSheet("QTabBar::tab { height: 30px; width: 160px; font-weight: bold; }")

    def create_menu(self):
        menubar = self.menuBar()
        
        edit_menu = menubar.addMenu("Edit")
        undo_action = self.undo_stack.createUndoAction(self, "Undo")
        undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        redo_action = self.undo_stack.createRedoAction(self, "Redo")
        redo_action.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)
        
        settings_menu = menubar.addMenu("Settings")
        pref_action = QAction("Preferences", self)
        pref_action.setShortcut(QKeySequence("Ctrl+P"))
        pref_action.triggered.connect(self.open_preferences)
        settings_menu.addAction(pref_action)

        patch_action = QAction("Patch Library", self)
        patch_action.triggered.connect(self.open_patch_library)
        settings_menu.addAction(patch_action)

        tools_menu = menubar.addMenu("Tools")
        stem_action = QAction("Open Stem Creator", self)
        stem_action.triggered.connect(self.open_stem_creator)
        tools_menu.addAction(stem_action)
        
        chord_action = QAction("Open Chord Creator", self)
        chord_action.triggered.connect(self.open_chord_creator)
        tools_menu.addAction(chord_action)

        tools_menu.addSeparator()
        live_action = QAction("🔴  Toggle Live Mode", self)
        live_action.setShortcut(QKeySequence("Ctrl+L"))
        live_action.triggered.connect(self.toggle_live_mode)
        tools_menu.addAction(live_action)

    def open_patch_library(self):
        dlg = PatchLibraryDialog(self.data_manager, self.sync_engine, self)
        if dlg.exec():
            dlg.save_current_table_to_dict()
            self.data_manager.patches = dlg.patches
            self.data_manager.save_patches()

    def handle_mapped_action(self, action):
        """Dispatches mapped actions to the setlist tab or engines."""
        if action in ["NEXT_SONG", "PREV_SONG", "SCROLL_DOWN", "SCROLL_UP", "PLAY_PAUSE", "STOP", "MUTE_CLICK", "TOGGLE_AUTOSCROLL"]:
            if action == "PLAY_PAUSE": self.setlist_tab.btn_play_pause.click()
            elif action == "STOP": self.setlist_tab.stop_audio_playback()
            elif action == "MUTE_CLICK": self.setlist_tab.chk_click_mute.click()
            elif action == "TOGGLE_AUTOSCROLL": self.setlist_tab.btn_scroll.click()
            else: 
                logger.info(f"MSM2: Executing mapped pedal action: {action}")
                self.setlist_tab.handle_pedal_input(action)
        elif action == "MUTE_MASTER":
            self.audio_engine.set_master_mute(not self.audio_engine.master_mute)
        elif action.startswith("MUTE_GROUP_"):
            idx = int(action.split("_")[-1]) - 1
            if idx < len(self.setlist_tab.mg_buttons): self.setlist_tab.mg_buttons[idx].click()
        elif action.startswith("MUTE_FX_"):
            idx = int(action.split("_")[-1]) - 1
            if idx < len(self.setlist_tab.fx_buttons): self.setlist_tab.fx_buttons[idx].click()

    def keyPressEvent(self, event: QKeyEvent):
        if not self.mapping_manager.handle_qt_key_press(event):
            super().keyPressEvent(event)

    def retranslate_ui(self):
        tr = TRANSLATIONS.get(self.current_language, TRANSLATIONS["English"])
        self.main_tabs.setTabText(0, tr["SETLIST_PLAYER"])
        
        menubar = self.menuBar()
        actions = menubar.actions()
        if len(actions) >= 1 and actions[0].menu(): actions[0].menu().setTitle(tr["SETTINGS"])
        if len(actions) >= 2 and actions[1].menu(): actions[1].menu().setTitle(tr["TOOLS"])
        
        self.setlist_tab.retranslate_ui(tr)

    def apply_theme(self, theme_name):
        colors = THEMES.get(theme_name)
        if not colors: return
        self.current_theme = theme_name
        logger.info(f"MSM2: Theme applied: {theme_name}")
        
        qss = f"""
        QMainWindow, QDialog, QWidget {{ background-color: {colors['primary']}; color: {colors['text']}; }}
        QFrame, QGroupBox {{ background-color: {colors['secondary']}; border: 1px solid {colors['border']}; border-radius: 5px; }}
        QPushButton {{ background-color: {colors['button']}; color: {colors['text']}; border: 1px solid {colors['border']}; padding: 5px; border-radius: 4px; }}
        QPushButton:hover {{ background-color: {colors['accent']}; color: white; }}
        QLineEdit, QComboBox, QTextBrowser, QTreeWidget {{ background-color: {colors['card']}; color: {colors['text']}; border: 1px solid {colors['border']}; selection-background-color: {colors['accent']}; }}
        QHeaderView::section {{ background-color: {colors['secondary']}; color: {colors['text']}; border: 1px solid {colors['border']}; }}
        QScrollBar:vertical {{ border: none; background: {colors['primary']}; width: 10px; }}
        QScrollBar::handle:vertical {{ background: {colors['accent']}; min-height: 20px; border-radius: 5px; }}
        QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {colors['secondary']}; }}
        QSlider::sub-page:horizontal {{ background: {colors['accent']}; border-radius: 2px; }}
        QSlider::add-page:horizontal {{ background: {colors['border']}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {colors['text']}; width: 10px; margin-top: -3px; margin-bottom: -3px; border-radius: 5px; }}
        QProgressBar {{ border: 1px solid {colors['border']}; border-radius: 2px; background-color: {colors['secondary']}; }} 
        QProgressBar::chunk {{ background-color: {colors['accent']}; }}
        QCheckBox {{ spacing: 8px; background: transparent; }}
        QCheckBox::indicator {{ width: 18px; height: 18px; border: 2px solid {colors['border']}; border-radius: 4px; background-color: {colors['card']}; }}
        QCheckBox::indicator:hover {{ border: 2px solid {colors['accent']}; }}
        QCheckBox::indicator:checked {{ background-color: {colors['accent']}; border: 2px solid {colors['accent']}; image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23ffffff' stroke-width='4' stroke-linecap='round' stroke-linejoin='round'><polyline points='20 6 9 17 4 12'/></svg>"); }}
        """
        self.setStyleSheet(qss)
        self.setlist_tab.apply_theme(colors)
        self.optimizer_tab.set_theme(colors)

    def restart_metronome_after_delay(self):
        tr = TRANSLATIONS.get(self.current_language, TRANSLATIONS["English"])
        if not self.setlist_tab.btn_sync.isChecked():
            self.setlist_tab.btn_sync.setChecked(True)
            self.setlist_tab.btn_sync.setText(tr["STOP_METRONOME"])
            self.sync_engine.start(with_delay=True)
            self.setlist_tab.refresh_autoscroll_timer()

    def toggle_fullscreen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()

    def open_preferences(self):
        from ui.preferences_dialog import PreferencesDialog
        dialog = PreferencesDialog(self)
        dialog.exec()

    def toggle_live_mode(self):
        """Toggle live performance mode on/off."""
        is_live = self.setlist_tab.toggle_live_mode()
        # Show/hide Optimizer (idx 1) and Diagnostics (idx 2) tabs
        self.main_tabs.setTabVisible(1, not is_live)
        self.main_tabs.setTabVisible(2, not is_live)
        logger.info(f"MSM2: Live Mode {'ACTIVATED' if is_live else 'DEACTIVATED'}")
        if is_live:
            self.main_tabs.setCurrentIndex(0)


    def open_stem_creator(self):
        if not hasattr(self, 'stem_window') or self.stem_window is None:
            self.stem_window = DemucsGUI()
        if self.current_theme in THEMES:
            self.stem_window.apply_theme(THEMES[self.current_theme])
        self.stem_window.show(); self.stem_window.raise_(); self.stem_window.activateWindow()
        
    def open_chord_creator(self):
        if not hasattr(self, 'chord_window') or self.chord_window is None:
            self.chord_window = ChordProGUI()
        if self.current_theme in THEMES:
            self.chord_window.apply_theme(THEMES[self.current_theme])
        self.chord_window.show(); self.chord_window.raise_(); self.chord_window.activateWindow()
            
    def closeEvent(self, event):
        logger.info("MSM2: Initiating graceful shutdown...")
        if hasattr(self, 'audio_engine'): self.audio_engine.cleanup()
        if hasattr(self, 'sync_engine'): self.sync_engine.cleanup()
        if hasattr(self, 'playback_timer') and self.playback_timer.isActive(): self.playback_timer.stop()
        if hasattr(self, 'setlist_tab'):
            if self.setlist_tab.scroll_timer.isActive(): self.setlist_tab.scroll_timer.stop()
            if self.setlist_tab.waveform_viewer: self.setlist_tab.waveform_viewer.stop()
        logger.info("MSM2: Hardware released. Goodbye!")
        event.accept()
