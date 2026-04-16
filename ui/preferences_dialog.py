import mido
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QStackedWidget,
                             QWidget, QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox,
                             QFormLayout, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
                             QColorDialog, QMessageBox)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from ui.themes import THEMES
from translations import TRANSLATIONS
from dialogs import MappingDialog

class PreferencesDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.data_manager = main_window.data_manager
        self.sync_engine = main_window.sync_engine
        
        self.setWindowTitle("Preferences")
        self.resize(900, 600)
        
        # Main Layout
        outer_layout = QVBoxLayout(self)
        
        content_layout = QHBoxLayout()
        # Sidebar
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(200)
        content_layout.addWidget(self.sidebar)
        
        # Content Area
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, stretch=1)
        outer_layout.addLayout(content_layout)
        
        # Load configs
        self.config = self.data_manager.config
        
        # Add Pages
        self.add_page("Appearance", self.create_appearance_page())
        self.add_page("Audio & Transition", self.create_audio_page())
        self.add_page("MIDI & Sync", self.create_midi_page())
        self.add_page("Mixer (OSC)", self.create_mixer_page())
        self.add_page("DMX Lights", self.create_dmx_page())
        self.add_page("Web Users", self.create_web_users_page())
        self.add_page("Audio AI & Cloud", self.create_ai_page())
        self.add_page("Input Mappings", self.create_mappings_page())
        
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        
        # Bottom Buttons
        btn_layout = QHBoxLayout()
        btn_apply = QPushButton("Apply")
        btn_apply.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold;")
        btn_apply.clicked.connect(self.apply_settings)
        
        btn_ok = QPushButton("OK")
        btn_ok.setStyleSheet("background-color: #28a745; color: white; font-weight: bold;")
        btn_ok.clicked.connect(self.accept_settings)
        
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_apply)
        btn_layout.addWidget(btn_ok)
        outer_layout.addLayout(btn_layout)

    def add_page(self, title, widget):
        self.sidebar.addItem(title)
        self.stack.addWidget(widget)

    # --- Appearance Page ---
    def create_appearance_page(self):
        page = QWidget()
        layout = QFormLayout(page)
        
        self.combo_theme = QComboBox()
        self.combo_theme.addItems(list(THEMES.keys()))
        curr_theme = self.config.get("theme", "Modern Dark")
        if curr_theme in THEMES:
            self.combo_theme.setCurrentText(curr_theme)
        
        self.combo_lang = QComboBox()
        self.combo_lang.addItems(list(TRANSLATIONS.keys()))
        curr_lang = self.config.get("language", "English")
        if curr_lang in TRANSLATIONS:
            self.combo_lang.setCurrentText(curr_lang)
            
        layout.addRow("Theme:", self.combo_theme)
        layout.addRow("Language:", self.combo_lang)
        return page

    # --- Audio Page ---
    def create_audio_page(self):
        page = QWidget()
        layout = QFormLayout(page)
        sound_settings = self.config.get("sound_settings", {})
        
        self.delay_input = QDoubleSpinBox()
        self.delay_input.setRange(0.0, 10.0)
        self.delay_input.setSingleStep(0.5)
        self.delay_input.setSuffix(" sec")
        self.delay_input.setValue(sound_settings.get("transition_delay", 2.0))
        
        self.tick_input = QDoubleSpinBox()
        self.tick_input.setRange(1.0, 60.0)
        self.tick_input.setSuffix(" sec")
        self.tick_input.setValue(sound_settings.get("tick_disable_duration", 10.0))
        
        self.blocksize_input = QComboBox()
        self.blocksizes = [64, 128, 256, 512, 1024, 2048, 4096]
        self.blocksize_input.addItems([str(b) for b in self.blocksizes])
        current_bs = sound_settings.get("blocksize", 4096)
        if current_bs in self.blocksizes:
            self.blocksize_input.setCurrentText(str(current_bs))
            
        self.click_freq_accent_input = QSpinBox()
        self.click_freq_accent_input.setRange(200, 5000)
        self.click_freq_accent_input.setSuffix(" Hz")
        self.click_freq_accent_input.setValue(sound_settings.get("click_freq_accent", 1000))

        self.click_freq_normal_input = QSpinBox()
        self.click_freq_normal_input.setRange(200, 5000)
        self.click_freq_normal_input.setSuffix(" Hz")
        self.click_freq_normal_input.setValue(sound_settings.get("click_freq_normal", 800))
        
        self.combo_click_type = QComboBox()
        self.combo_click_type.addItems(["Sine", "Wood", "Metal", "Electronic"])
        self.combo_click_type.setCurrentText(sound_settings.get("click_type", "Sine"))

        layout.addRow("Transition Delay:", self.delay_input)
        layout.addRow("Tick Auto-Mute Duration:", self.tick_input)
        layout.addRow("Audio Buffer (Blocksize):", self.blocksize_input)
        layout.addRow("Accent Click Freq:", self.click_freq_accent_input)
        layout.addRow("Normal Click Freq:", self.click_freq_normal_input)
        layout.addRow("Click Sound Type:", self.combo_click_type)
        return page

    # --- MIDI & Sync Page ---
    def create_midi_page(self):
        page = QWidget()
        layout = QFormLayout(page)
        
        self.combo_midi_port = QComboBox()
        self.combo_midi_port.addItem("None / Disabled")
        self.combo_midi_port.addItems(mido.get_output_names())
        curr_port = self.config.get("midi_port")
        if curr_port in [self.combo_midi_port.itemText(i) for i in range(self.combo_midi_port.count())]:
            self.combo_midi_port.setCurrentText(curr_port)
            
        self.fps_input = QComboBox()
        self.fps_input.addItems(["24", "25", "30 (Non-Drop)"])
        fps = self.config.get("sound_settings", {}).get("mtc_fps", 30)
        idx = 0
        if fps == 25: idx = 1
        elif fps == 30: idx = 2
        self.fps_input.setCurrentIndex(idx)
        
        layout.addRow("MIDI Output Port:", self.combo_midi_port)
        layout.addRow("MTC Frame Rate:", self.fps_input)
        return page

    # --- Mixer Page ---
    def create_mixer_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        mixer_settings = self.config.get("mixer_settings", {})
        
        ip_layout = QFormLayout()
        self.osc_ip_input = QLineEdit(mixer_settings.get("osc_ip", "192.168.1.1"))
        ip_layout.addRow("Mixer IP Address:", self.osc_ip_input)
        layout.addLayout(ip_layout)
        
        layout.addWidget(QLabel("<b>BPM Sync Targets (FX / Parameters)</b>"))
        self.bpm_targets_table = QTableWidget(0, 2)
        self.bpm_targets_table.setHorizontalHeaderLabels(["FX ID (1-4)", "Param ID"])
        self.bpm_targets_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.bpm_targets_table)
        
        for tgt in mixer_settings.get("bpm_sync_targets", []):
            self.add_bpm_row(tgt.get("fx", 2), tgt.get("param", 2))
            
        btn_layout = QHBoxLayout()
        btn_add = QPushButton("+ Add Target")
        btn_add.clicked.connect(lambda: self.add_bpm_row(2, 2))
        btn_rem = QPushButton("- Remove Selected")
        btn_rem.clicked.connect(self.remove_bpm_row)
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(btn_rem)
        layout.addLayout(btn_layout)
        return page

    def add_bpm_row(self, fx, param):
        row = self.bpm_targets_table.rowCount()
        self.bpm_targets_table.insertRow(row)
        self.bpm_targets_table.setItem(row, 0, QTableWidgetItem(str(fx)))
        self.bpm_targets_table.setItem(row, 1, QTableWidgetItem(str(param)))

    def remove_bpm_row(self):
        row = self.bpm_targets_table.currentRow()
        if row >= 0: self.bpm_targets_table.removeRow(row)

    # --- DMX Page ---
    def create_dmx_page(self):
        page = QWidget()
        layout = QFormLayout(page)
        dmx = self.config.get("dmx_settings", {})
        
        self.universe_spin = QSpinBox()
        self.universe_spin.setRange(0, 10)
        self.universe_spin.setValue(dmx.get("universe", 1))
        
        self.accent_color = QColor(dmx.get('accent_r', 255), dmx.get('accent_g', 0), dmx.get('accent_b', 0))
        self.std_color = QColor(dmx.get('std_r', 0), dmx.get('std_g', 255), dmx.get('std_b', 0))
        
        self.btn_accent = QPushButton()
        self.btn_accent.setFixedSize(100, 30)
        self.btn_accent.setStyleSheet(f"background-color: {self.accent_color.name()};")
        self.btn_accent.clicked.connect(lambda: self.pick_color('accent'))
        
        self.btn_std = QPushButton()
        self.btn_std.setFixedSize(100, 30)
        self.btn_std.setStyleSheet(f"background-color: {self.std_color.name()};")
        self.btn_std.clicked.connect(lambda: self.pick_color('std'))
        
        layout.addRow("Universe:", self.universe_spin)
        layout.addRow("Accent Beat Color:", self.btn_accent)
        layout.addRow("Standard Beat Color:", self.btn_std)
        return page

    def pick_color(self, target):
        initial = self.accent_color if target == 'accent' else self.std_color
        c = QColorDialog.getColor(initial, self, "Pick DMX Color")
        if c.isValid():
            if target == 'accent':
                self.accent_color = c
                self.btn_accent.setStyleSheet(f"background-color: {c.name()};")
            else:
                self.std_color = c
                self.btn_std.setStyleSheet(f"background-color: {c.name()};")

    # --- Web Users Page ---
    def create_web_users_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        
        layout.addWidget(QLabel("<b>Web Remote Users</b>"))
        self.web_users_table = QTableWidget(0, 3)
        self.web_users_table.setHorizontalHeaderLabels(["Username", "Password", "Role"])
        self.web_users_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.web_users_table)
        
        web_users = self.config.get("web_users", {})
        if not web_users:
            web_users = {"admin": {"password": "admin", "role": "role-tech"}}
            
        for username, data in web_users.items():
            self.add_web_user_row(username, data.get("password", ""), data.get("role", "role-musician"))
            
        btn_layout = QHBoxLayout()
        btn_add = QPushButton("+ Add User")
        btn_add.clicked.connect(lambda: self.add_web_user_row("new_user", "password", "role-musician"))
        btn_rem = QPushButton("- Remove Selected")
        btn_rem.clicked.connect(self.remove_web_user_row)
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(btn_rem)
        layout.addLayout(btn_layout)
        return page

    def add_web_user_row(self, username, password, role):
        row = self.web_users_table.rowCount()
        self.web_users_table.insertRow(row)
        self.web_users_table.setItem(row, 0, QTableWidgetItem(username))
        self.web_users_table.setItem(row, 1, QTableWidgetItem(password))
        
        combo_role = QComboBox()
        combo_role.addItems(["role-tech", "role-singer", "role-musician", "role-drummer"])
        combo_role.setCurrentText(role)
        self.web_users_table.setCellWidget(row, 2, combo_role)

    def remove_web_user_row(self):
        row = self.web_users_table.currentRow()
        if row >= 0: self.web_users_table.removeRow(row)

    # --- AI & Cloud Page ---
    def create_ai_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        
        layout.addWidget(QLabel("<b>Local Analysis Profile</b>"))
        self.combo_profile = QComboBox()
        self.combo_profile.addItems(["Balanced", "Electronic", "Rock", "Pop"])
        self.combo_profile.setCurrentText(self.config.get("analysis_profile", "Balanced"))
        layout.addWidget(self.combo_profile)
        layout.addWidget(QLabel("<small>Adjusts how the AI weights bass vs. brightness for energy detection.</small>"))
        
        layout.addSpacing(20)
        layout.addWidget(QLabel("<b>Spotify Cloud Integration</b>"))
        form = QFormLayout()
        
        self.spotify_id = QLineEdit(self.config.get("spotify_client_id", ""))
        self.spotify_id.setPlaceholderText("Client ID from Spotify Dashboard")
        self.spotify_secret = QLineEdit(self.config.get("spotify_client_secret", ""))
        self.spotify_secret.setPlaceholderText("Client Secret")
        self.spotify_secret.setEchoMode(QLineEdit.EchoMode.Password)
        
        form.addRow("Client ID:", self.spotify_id)
        form.addRow("Client Secret:", self.spotify_secret)
        layout.addLayout(form)
        
        layout.addStretch()
        return page

    # --- Mappings Page ---
    def create_mappings_page(self):
        # Embed MappingDialog
        dlg = MappingDialog(self.main_window.mapping_manager, self)
        # Hide the bottom layout (the Close button)
        dlg.layout().itemAt(dlg.layout().count() - 1).widget().hide() if dlg.layout().itemAt(dlg.layout().count() - 1).widget() else None
        # It's better to just leave it as is, or we can just embed the UI components.
        # But this works!
        return dlg

    # --- Actions ---
    def apply_settings(self):
        # Appearance
        if self.combo_theme.currentText() != self.main_window.current_theme:
            self.main_window.apply_theme(self.combo_theme.currentText())
            self.data_manager.config["theme"] = self.combo_theme.currentText()
        if self.combo_lang.currentText() != self.main_window.current_language:
            self.main_window.current_language = self.combo_lang.currentText()
            self.data_manager.config["language"] = self.combo_lang.currentText()
            self.main_window.retranslate_ui()
            
        # Audio & Sync
        fps_map = {0: 24, 1: 25, 2: 30}
        sound = {
            "transition_delay": self.delay_input.value(),
            "tick_disable_duration": self.tick_input.value(),
            "mtc_fps": fps_map.get(self.fps_input.currentIndex(), 30),
            "blocksize": int(self.blocksize_input.currentText()),
            "click_freq_accent": self.click_freq_accent_input.value(),
            "click_freq_normal": self.click_freq_normal_input.value(),
            "click_type": self.combo_click_type.currentText()
        }
        self.data_manager.config["sound_settings"] = sound
        self.sync_engine.set_mtc_fps(sound["mtc_fps"])
        self.sync_engine.update_click_params(sound["click_freq_accent"], sound["click_freq_normal"], sound["click_type"])
        
        # MIDI
        midi_port = self.combo_midi_port.currentText()
        if midi_port == "None / Disabled": midi_port = None
        self.data_manager.config["midi_port"] = midi_port
        self.sync_engine.set_midi_port(midi_port)
        
        # Mixer
        t = []
        for i in range(self.bpm_targets_table.rowCount()):
            try:
                t.append({"fx": int(self.bpm_targets_table.item(i, 0).text()), "param": int(self.bpm_targets_table.item(i, 1).text())})
            except: pass
        self.data_manager.config["mixer_settings"] = {
            "osc_ip": self.osc_ip_input.text(),
            "bpm_sync_targets": t
        }
        self.sync_engine.update_settings(self.osc_ip_input.text())
        self.sync_engine.set_bpm_sync_targets(t)
        
        # DMX
        dmx = self.config.get("dmx_settings", {})
        dmx.update({
            "universe": self.universe_spin.value(),
            "accent_r": self.accent_color.red(), "accent_g": self.accent_color.green(), "accent_b": self.accent_color.blue(),
            "std_r": self.std_color.red(), "std_g": self.std_color.green(), "std_b": self.std_color.blue()
        })
        self.data_manager.config["dmx_settings"] = dmx
        self.sync_engine.dmx_settings = dmx
        self.sync_engine.dmx_universe = self.universe_spin.value()
        
        # Web Users
        web_users = {}
        for i in range(self.web_users_table.rowCount()):
            u_item = self.web_users_table.item(i, 0)
            p_item = self.web_users_table.item(i, 1)
            r_widget = self.web_users_table.cellWidget(i, 2)
            if u_item and p_item and r_widget:
                username = u_item.text().strip()
                if username:
                    web_users[username] = {
                        "password": p_item.text(),
                        "role": r_widget.currentText()
                    }
        if not web_users:
            web_users = {"admin": {"password": "admin", "role": "role-tech"}}
        self.data_manager.config["web_users"] = web_users
        
        # AI & Spotify
        self.data_manager.config["analysis_profile"] = self.combo_profile.currentText()
        self.data_manager.config["spotify_client_id"] = self.spotify_id.text().strip()
        self.data_manager.config["spotify_client_secret"] = self.spotify_secret.text().strip()

        # Save
        self.data_manager.save_config()
        self.main_window.data_manager.config = self.data_manager.config

    def accept_settings(self):
        self.apply_settings()
        self.accept()
