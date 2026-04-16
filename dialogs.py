"""
Module: dialogs.py
Meaning: Defines the popup dialog windows for application settings and configuration.
Working Procedure: Uses PyQt6 classes to build forms for editing setlists, song metadata, 
and hardware routes (OSC/MIDI configurations).
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QFormLayout, QMessageBox, 
                             QListWidget, QAbstractItemView, QComboBox, QColorDialog, 
                             QSpinBox, QDoubleSpinBox, QSlider, QWidget, QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, QGroupBox)
from PyQt6.QtCore import pyqtSignal, Qt            
from PyQt6.QtGui import QColor
from translations import TRANSLATIONS
import copy
import mido
import numpy as np

class SoundSettingsDialog(QDialog):
    def __init__(self, current_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sound / Transition Settings")
        self.layout = QFormLayout(self)
        
        self.delay_input = QDoubleSpinBox()
        self.delay_input.setRange(0.0, 10.0)
        self.delay_input.setSingleStep(0.5)
        self.delay_input.setSuffix(" seconds")
        self.delay_input.setValue(current_settings.get("transition_delay", 2.0))
        
        self.layout.addRow("Song Transition Delay:", self.delay_input)
        
        self.tick_duration_input = QDoubleSpinBox()
        self.tick_duration_input.setRange(1.0, 60.0)
        self.tick_duration_input.setSingleStep(1.0)
        self.tick_duration_input.setSuffix(" seconds")
        self.tick_duration_input.setValue(current_settings.get("tick_disable_duration", 10.0))
        self.layout.addRow("Tick Disable Duration:", self.tick_duration_input)
        
        self.fps_input = QComboBox()
        self.fps_input.addItems(["24", "25", "30 (Non-Drop)"])
        fps_val = current_settings.get("mtc_fps", 30)
        idx = 0
        if fps_val == 25: idx = 1
        elif fps_val == 30: idx = 2
        self.fps_input.setCurrentIndex(idx)
        self.layout.addRow("MTC Frame Rate (FPS):", self.fps_input)

        # --- NEW: BLOCKSIZE SELECTION ---
        self.blocksize_input = QComboBox()
        self.blocksizes = [64, 128, 256, 512, 1024, 2048, 4096]
        self.blocksize_input.addItems([str(b) for b in self.blocksizes])
        
        current_bs = current_settings.get("blocksize", 4096)
        if current_bs in self.blocksizes:
            self.blocksize_input.setCurrentText(str(current_bs))
        else:
            self.blocksize_input.setCurrentText("4096")
        
        self.layout.addRow("Audio Buffer Size (Blocksize):", self.blocksize_input)
        
        self.click_freq_accent_input = QSpinBox()
        self.click_freq_accent_input.setRange(200, 5000)
        self.click_freq_accent_input.setSuffix(" Hz")
        self.click_freq_accent_input.setValue(current_settings.get("click_freq_accent", 1000))
        self.layout.addRow("Accent Click Freq:", self.click_freq_accent_input)

        self.click_freq_normal_input = QSpinBox()
        self.click_freq_normal_input.setRange(200, 5000)
        self.click_freq_normal_input.setSuffix(" Hz")
        self.click_freq_normal_input.setValue(current_settings.get("click_freq_normal", 800))
        self.layout.addRow("Normal Click Freq:", self.click_freq_normal_input)
        
        self.click_type_input = QComboBox()
        self.click_type_input.addItems(["Sine", "Wood", "Metal", "Electronic"])
        self.click_type_input.setCurrentText(current_settings.get("click_type", "Sine"))
        self.layout.addRow("Click Sound Type:", self.click_type_input)
        
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 5px;")
        self.save_btn.clicked.connect(self.accept)
        self.layout.addWidget(self.save_btn)
        
    def get_settings(self):
        fps_map = {0: 24, 1: 25, 2: 30}
        return {
            "transition_delay": self.delay_input.value(),
            "mtc_fps": fps_map.get(self.fps_input.currentIndex(), 30),
            "tick_disable_duration": self.tick_duration_input.value(),
            "blocksize": int(self.blocksize_input.currentText()),
            "click_freq_accent": self.click_freq_accent_input.value(),
            "click_freq_normal": self.click_freq_normal_input.value(),
            "click_type": self.click_type_input.currentText()
        }

class SettingsDialog(QDialog):
    def __init__(self, current_ip, bpm_targets, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OSC / Mixer Settings")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        
        # IP Settings
        ip_layout = QFormLayout()
        self.ip_input = QLineEdit(current_ip)
        ip_layout.addRow("Mixer IP Address:", self.ip_input)
        layout.addLayout(ip_layout)
        
        layout.addSpacing(10)
        layout.addWidget(QLabel("<b>BPM Sync Targets (FX / Parameters)</b>"))
        
        # Table for BPM targets
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["FX ID (1-4)", "Param ID"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        
        # Initialize table with current targets
        for target in bpm_targets:
            self.add_table_row(target.get("fx", 2), target.get("param", 2))
            
        # Buttons to add/remove rows
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("+ Add Target")
        add_btn.clicked.connect(lambda: self.add_table_row(2, 2))
        btn_layout.addWidget(add_btn)
        
        remove_btn = QPushButton("- Remove Selected")
        remove_btn.clicked.connect(self.remove_selected_row)
        btn_layout.addWidget(remove_btn)
        layout.addLayout(btn_layout)
        
        layout.addSpacing(10)
        save_btn = QPushButton("Save Settings")
        save_btn.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 8px;")
        save_btn.clicked.connect(self.accept)
        layout.addWidget(save_btn)

    def add_table_row(self, fx, param):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(str(fx)))
        self.table.setItem(row, 1, QTableWidgetItem(str(param)))

    def remove_selected_row(self):
        current_row = self.table.currentRow()
        if current_row >= 0:
            self.table.removeRow(current_row)

    def get_settings(self):
        targets = []
        for row in range(self.table.rowCount()):
            try:
                fx = int(self.table.item(row, 0).text())
                par = int(self.table.item(row, 1).text())
                targets.append({"fx": fx, "param": par})
            except (ValueError, AttributeError):
                continue
        return self.ip_input.text(), targets

class SongEditorDialog(QDialog):
    def __init__(self, song_title="", song_data=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Song" if song_title else "New Song")
        
        self.data_manager = parent.data_manager if hasattr(parent, 'data_manager') else None
        self.core_fields = ["BPM", "Key", "Energy", "Time Sig", "Snapshot", "Notes"]
        
        raw_data = song_data or {
            "BPM": 120, "Key": "C", "Energy": 5.0, "Time Sig": "4/4", 
            "Snapshot": "", "Notes": "-"
        }
        
        # Normalize: merge lowercase keys that match core fields (e.g. "bpm" -> "BPM")
        # to prevent duplicates from the analyzer writing lowercase keys
        core_lower_map = {f.lower(): f for f in self.core_fields}
        self.song_data = {}
        for k, v in raw_data.items():
            canonical = core_lower_map.get(k.lower())
            if canonical and canonical != k:
                # Lowercase variant of a core field — merge under canonical name
                # Only set if canonical isn't already present (priority: explicit core > lowercase)
                if canonical not in self.song_data:
                    self.song_data[canonical] = v
            else:
                if k not in self.song_data:
                    self.song_data[k] = v
        
        self.main_layout = QFormLayout(self)
        
        self.title_input = QLineEdit(song_title)
        if song_title:
            self.title_input.setReadOnly(True)
        self.main_layout.addRow("Song Title:", self.title_input)

        # --- Patch Selector (multi-select with Ctrl/Shift) ---
        self.patch_list = QListWidget()
        self.patch_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        if self.data_manager:
            patch_names = sorted(self.data_manager.patches.keys())
            self.patch_list.addItems(patch_names)

            selected_patches = self.song_data.get("Patches")
            if not isinstance(selected_patches, list):
                legacy_patch = self.song_data.get("Patch")
                selected_patches = [legacy_patch] if legacy_patch else []

            for i in range(self.patch_list.count()):
                item = self.patch_list.item(i)
                if item.text() in selected_patches:
                    item.setSelected(True)

        self.patch_list.setMinimumHeight(120)
        self.main_layout.addRow("Assigned Patches (Ctrl+Click):", self.patch_list)
        
        self.inputs = {}
        # 1. Add CORE fields in specific order
        # We'll use a set of keys we've already added to avoid duplicates
        added_keys = set()
        
        for key in self.core_fields:
            # Check for existing value (case-insensitive)
            actual_key = next((k for k in self.song_data.keys() if k.lower() == key.lower()), None)
            if actual_key:
                value = self.song_data[actual_key]
                added_keys.add(actual_key)
            else:
                value = "" if key == "Snapshot" else "-"
            
            self.add_field_ui(key, value)
            added_keys.add(key)
            
        # 2. Add any EXTRA fields from song_data that are NOT in core_fields
        for key, value in self.song_data.items():
            # Hide MidiActions, database 'id', Patch/Patches (handled by list), and legacy PC_chX from the UI
            if key not in added_keys and key not in ["MidiActions", "id", "Patch", "Patches"] and not key.startswith("PC_ch"):
                self.add_field_ui(key, value)
                added_keys.add(key)
            
        save_btn = QPushButton("Save to Database")
        save_btn.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 5px;")
        save_btn.clicked.connect(self.accept)
        self.main_layout.addWidget(save_btn)

        # --- Button to edit MIDI Actions ---
        self.midi_actions_btn = QPushButton("🎬 Edit MIDI Time Actions")
        self.midi_actions_btn.setStyleSheet("background-color: #fe8019; color: black; font-weight: bold; padding: 5px;")
        self.midi_actions_btn.clicked.connect(self.open_midi_actions_editor)
        self.main_layout.addWidget(self.midi_actions_btn)

    def open_midi_actions_editor(self):
        """Opens the sub-dialog to manage timed MIDI events."""
        actions = self.song_data.get("MidiActions", [])
        dlg = MidiActionsDialog(actions, self)
        if dlg.exec():
            self.song_data["MidiActions"] = dlg.get_actions()

    def add_field_ui(self, key, value, insert_index=None):
        """Helper method: creates the field. If it's an extra field, adds a trash bin."""
        if key == "Key":
            widget = QComboBox()
            keys = [
                "C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B",
                "Cm", "C#m", "Dbm", "Dm", "D#m", "Ebm", "Em", "Fm", "F#m", "Gbm", "Gm", "G#m", "Abm", "Am", "A#m", "Bbm", "Bm"
            ]
            widget.addItems(keys)
            widget.setCurrentText(str(value))
        elif key == "Energy":
            widget = QDoubleSpinBox()
            widget.setRange(1.0, 10.0)
            widget.setSingleStep(0.5)
            # Handle list/tuple or string safely
            try:
                if isinstance(value, (list, np.ndarray)) and len(value) > 0: v = float(value[0])
                else: v = float(value)
            except: v = 5.0
            widget.setValue(v)
        elif key == "BPM":
            widget = QSpinBox()
            widget.setRange(20, 300)
            try:
                if isinstance(value, (list, np.ndarray)) and len(value) > 0: v = int(value[0])
                else: v = int(value)
            except: v = 120
            widget.setValue(v)
        else:
            widget = QLineEdit(str(value))
            
        self.inputs[key] = widget
        
        if key in self.core_fields:
            # Standard fields: normal insertion without trash bin
            if insert_index is not None:
                self.main_layout.insertRow(insert_index, key, widget)
            else:
                self.main_layout.addRow(key, widget)
        else:
            # Extra fields: Create a horizontal container (Input + Trash bin)
            container = QWidget()
            h_layout = QHBoxLayout(container)
            h_layout.setContentsMargins(0, 0, 0, 0)
            
            h_layout.addWidget(widget)
            
            btn_delete = QPushButton("🗑️")
            btn_delete.setFixedSize(28, 28)
            btn_delete.setToolTip("Remove this parameter")
            btn_delete.clicked.connect(lambda checked=False, k=key, c=container: self.remove_field(k, c))
            
            h_layout.addWidget(btn_delete)
            
            if insert_index is not None:
                self.main_layout.insertRow(insert_index, key, container)
            else:
                self.main_layout.addRow(key, container)

    def remove_field(self, key, container_widget):
        """Removes the field from the UI and from the inputs dictionary."""
        # 1. Remove the reference from the dictionary
        if key in self.inputs:
            del self.inputs[key]
            
        # 2. Remove the entire row from the QFormLayout (removes both Label and Widget)
        self.main_layout.removeRow(container_widget)



    def get_updated_data(self):
        """Retrieves and formats data from the line edits before saving."""
        title = self.title_input.text().strip()
        
        # Create a new dictionary based ONLY on the fields currently remaining in the GUI.
        # This way, fields deleted with the trash bin disappear permanently.
        updated_data = {}
        for key, widget in self.inputs.items():
            if isinstance(widget, QLineEdit):
                val = widget.text()
            elif isinstance(widget, QComboBox):
                val = widget.currentText()
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                val = widget.value()
                
            if isinstance(val, str) and val.isdigit(): 
                val = int(val)
            updated_data[key] = val

        # Handle assigned patches (multi-select)
        selected_patches = [item.text() for item in self.patch_list.selectedItems()]
        updated_data["Patches"] = selected_patches
        # Backward compatibility for older code paths/scripts that still read "Patch"
        updated_data["Patch"] = selected_patches[0] if selected_patches else None
            
        # Preserve non-UI fields like MidiActions
        if "MidiActions" in self.song_data:
            updated_data["MidiActions"] = self.song_data["MidiActions"]

        # Preserve the database ID!
        if "id" in self.song_data:
            updated_data["id"] = self.song_data["id"]
            
        self.song_data = updated_data
        return title, self.song_data

class EditSetlistDialog(QDialog):
    def __init__(self, setlist_name, current_songs, all_songs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Setlist" if setlist_name else "New Setlist")
        self.resize(650, 500)
        
        main_layout = QVBoxLayout(self)
        
        # --- Setlist Name Section ---
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Setlist Name:"))
        self.name_input = QLineEdit(setlist_name)
        self.name_input.setPlaceholderText("Ex: Concert_Rome.csv")
        name_layout.addWidget(self.name_input)
        main_layout.addLayout(name_layout)
        
        # --- Dual Panel Lists ---
        lists_layout = QHBoxLayout()
        
        # Left: Database
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Songs in Database:"))
        self.db_list = QListWidget()
        self.db_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.db_list.addItems(sorted(all_songs))
        left_layout.addWidget(self.db_list)
        lists_layout.addLayout(left_layout)
        
        # Center: Add/Remove Buttons
        center_layout = QVBoxLayout()
        center_layout.addStretch()
        self.btn_add = QPushButton(" >> ")
        self.btn_remove = QPushButton(" << ")
        center_layout.addWidget(self.btn_add)
        center_layout.addWidget(self.btn_remove)
        center_layout.addStretch()
        lists_layout.addLayout(center_layout)
        
        # Right: Current Setlist
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("Songs in Setlist:"))
        self.setlist_list = QListWidget()
        self.setlist_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setlist_list.addItems(current_songs)
        right_layout.addWidget(self.setlist_list)
        
        # Up/Down Buttons
        up_down_layout = QHBoxLayout()
        self.btn_up = QPushButton("Up")
        self.btn_down = QPushButton("Down")
        up_down_layout.addWidget(self.btn_up)
        up_down_layout.addWidget(self.btn_down)
        right_layout.addLayout(up_down_layout)
        
        lists_layout.addLayout(right_layout)
        main_layout.addLayout(lists_layout)
        
        # --- Save Button ---
        btn_save = QPushButton("Save Setlist")
        btn_save.setStyleSheet("background-color: #198754; color: white; font-weight: bold; height: 40px;")
        btn_save.clicked.connect(self.check_and_accept)
        main_layout.addWidget(btn_save)
        
        # Connections
        self.btn_add.clicked.connect(self.add_songs)
        self.btn_remove.clicked.connect(self.remove_songs)
        self.btn_up.clicked.connect(self.move_up)
        self.btn_down.clicked.connect(self.move_down)

    def add_songs(self):
        for item in self.db_list.selectedItems():
            self.setlist_list.addItem(item.text())

    def remove_songs(self):
        for item in self.setlist_list.selectedItems():
            self.setlist_list.takeItem(self.setlist_list.row(item))

    def move_up(self):
        row = self.setlist_list.currentRow()
        if row > 0:
            item = self.setlist_list.takeItem(row)
            self.setlist_list.insertItem(row - 1, item)
            self.setlist_list.setCurrentRow(row - 1)

    def move_down(self):
        row = self.setlist_list.currentRow()
        if row < self.setlist_list.count() - 1:
            item = self.setlist_list.takeItem(row)
            self.setlist_list.insertItem(row + 1, item)
            self.setlist_list.setCurrentRow(row + 1)

    def check_and_accept(self):
        if not self.name_input.text().strip():
            QMessageBox.warning(self, "Warning", "You must enter a name for the setlist!")
            return
        self.accept()

    def get_data(self):
        name = self.name_input.text().strip()
        if not name.endswith(".csv"):
            name += ".csv"
        songs = [self.setlist_list.item(i).text() for i in range(self.setlist_list.count())]
        return name, songs
        
class MidiSettingsDialog(QDialog):
    def __init__(self, current_port, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MIDI Settings")
        self.resize(350, 150)
        
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select output MIDI port for Clock/PC/CC:"))
        
        self.combo_ports = QComboBox()
        available_ports = mido.get_output_names()
        
        self.combo_ports.addItem("None / Disabled")
        self.combo_ports.addItems(available_ports)
        
        if current_port in available_ports:
            self.combo_ports.setCurrentText(current_port)
        else:
            self.combo_ports.setCurrentIndex(0)
            
        layout.addWidget(self.combo_ports)
        
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_save.setStyleSheet("background-color: #198754; color: white; font-weight: bold;")
        btn_save.clicked.connect(self.accept)
        
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
    def get_selected_port(self):
        if self.combo_ports.currentIndex() == 0:
            return None
        return self.combo_ports.currentText()

class ThemeSettingsDialog(QDialog):
    # Define a signal that sends the selected theme name
    theme_preview = pyqtSignal(str)

    def __init__(self, current_theme, themes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Appearance Settings")
        self.resize(300, 150)
        
        # Store the initial theme to be able to cancel the preview
        self.initial_theme = current_theme
        
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select Theme:"))
        
        self.combo_themes = QComboBox()
        self.combo_themes.addItems(themes)
        
        if current_theme in themes:
            self.combo_themes.setCurrentText(current_theme)
            
        layout.addWidget(self.combo_themes)
        
        # --- CONNECTION FOR REAL-TIME PREVIEW ---
        # Every time the user changes selection, emit the signal
        self.combo_themes.currentTextChanged.connect(self.theme_preview.emit)
        
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_save.setStyleSheet("background-color: #198754; color: white; font-weight: bold;")
        btn_save.clicked.connect(self.accept)
        
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.handle_cancel) # Use a custom method
        
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
    def handle_cancel(self):
        """Restores the original theme and exits."""
        self.theme_preview.emit(self.initial_theme)
        self.reject()

    def get_selected_theme(self):
        return self.combo_themes.currentText()
        
class DmxSettingsDialog(QDialog):
    def __init__(self, current_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DMX Lights Configuration")
        self.setMinimumWidth(400)
        layout = QFormLayout(self)
        
        # --- NEW: DMX Universe Selection ---
        self.universe_spin = QSpinBox()
        self.universe_spin.setRange(0, 10)  # From 0 to 10 (most users use 0 or 1)
        # Get the universe from the dictionary (if it doesn't exist, default = 1)
        self.universe_spin.setValue(current_settings.get('universe', 1))
        
        layout.addRow("<b>--- GENERAL SETTINGS ---</b>", QLabel(""))
        layout.addRow("OLA Universe (DMX):", self.universe_spin)
        layout.addRow("", QLabel("")) # Empty space to separate
        # -----------------------------------

        # Store current colors (RGB)
        self.accent_color = QColor(
            current_settings.get('accent_r', 255),
            current_settings.get('accent_g', 0),
            current_settings.get('accent_b', 0)
        )
        self.std_color = QColor(
            current_settings.get('std_r', 0),
            current_settings.get('std_g', 255),
            current_settings.get('std_b', 0)
        )

        # Buttons for color selection
        self.btn_accent_color = QPushButton()
        self.btn_accent_color.setFixedSize(100, 30)
        self.update_button_color(self.btn_accent_color, self.accent_color)
        self.btn_accent_color.clicked.connect(lambda: self.pick_color('accent'))

        self.btn_std_color = QPushButton()
        self.btn_std_color.setFixedSize(100, 30)
        self.update_button_color(self.btn_std_color, self.std_color)
        self.btn_std_color.clicked.connect(lambda: self.pick_color('std'))

        # Spinbox for intensity (Master Dimmer)
        self.accent_dim = QSpinBox()
        self.accent_dim.setRange(0, 255)
        self.accent_dim.setValue(current_settings.get('accent_dim', 255))

        self.std_dim = QSpinBox()
        self.std_dim.setRange(0, 255)
        self.std_dim.setValue(current_settings.get('std_dim', 255))

        # Build Layout
        layout.addRow("<b>--- ACCENTED BEAT ---</b>", QLabel(""))
        layout.addRow("Color:", self.btn_accent_color)
        layout.addRow("Brightness (CH1):", self.accent_dim)

        layout.addRow("<b>--- STANDARD BEAT ---</b>", QLabel(""))
        layout.addRow("Color:", self.btn_std_color)
        layout.addRow("Brightness (CH1):", self.std_dim)

        btn_save = QPushButton("Save")
        btn_save.clicked.connect(self.accept)
        btn_save.setStyleSheet("background-color: #3584e4; font-weight: bold; margin-top: 10px;")
        layout.addRow(btn_save)

    def update_button_color(self, button, color):
        """Updates the button background to show the chosen color."""
        button.setStyleSheet(f"background-color: {color.name()}; border: 1px solid white;")

    def pick_color(self, target):
        """Opens the system color picker."""
        initial = self.accent_color if target == 'accent' else self.std_color
        color = QColorDialog.getColor(initial, self, "Choose DMX Color")
        
        if color.isValid():
            if target == 'accent':
                self.accent_color = color
                self.update_button_color(self.btn_accent_color, color)
            else:
                self.std_color = color
                self.update_button_color(self.btn_std_color, color)

    def get_settings(self):
        return {
            'universe': self.universe_spin.value(),
            'accent_r': self.accent_color.red(),
            'accent_g': self.accent_color.green(),
            'accent_b': self.accent_color.blue(),
            'accent_dim': self.accent_dim.value(),
            'std_r': self.std_color.red(),
            'std_g': self.std_color.green(),
            'std_b': self.std_color.blue(),
            'std_dim': self.std_dim.value()
        }
        
class StepSequencerDialog(QDialog):
    def __init__(self, sequence_data=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MIDI Step Sequencer")
        self.setMinimumSize(800, 450)
        
        # Default data if the song doesn't have a sequence yet
        self.sequence_data = sequence_data or {
            "num_steps": 16,
            "midi_channel": 1,
            "midi_cc": 11,  # E.g., Control Change 11 (Expression)
            "steps": [0] * 16 # Values from 0 to 127
        }
        
        self.sliders = []
        
        # Main layout
        main_layout = QVBoxLayout(self)
        
        # --- TOP BAR: Controls ---
        top_layout = QHBoxLayout()
        
        top_layout.addWidget(QLabel("Number of Steps:"))
        self.spin_steps = QSpinBox()
        self.spin_steps.setRange(4, 128)
        self.spin_steps.setValue(self.sequence_data["num_steps"])
        self.spin_steps.valueChanged.connect(self.rebuild_sliders)
        top_layout.addWidget(self.spin_steps)
        
        top_layout.addSpacing(20)
        
        top_layout.addWidget(QLabel("MIDI Channel:"))
        self.spin_channel = QSpinBox()
        self.spin_channel.setRange(1, 16)
        self.spin_channel.setValue(self.sequence_data["midi_channel"])
        top_layout.addWidget(self.spin_channel)

        top_layout.addSpacing(20)

        top_layout.addWidget(QLabel("CC Number:"))
        self.spin_cc = QSpinBox()
        self.spin_cc.setRange(0, 127)
        self.spin_cc.setValue(self.sequence_data.get("midi_cc", 11))
        top_layout.addWidget(self.spin_cc)
        
        top_layout.addStretch()
        main_layout.addLayout(top_layout)
        
        # --- MIDDLE AREA: The Sliders ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.sliders_container = QWidget()
        self.sliders_layout = QHBoxLayout(self.sliders_container)
        self.scroll_area.setWidget(self.sliders_container)
        
        main_layout.addWidget(self.scroll_area)
        
        # --- BOTTOM BAR: Save/Cancel Buttons ---
        bottom_layout = QHBoxLayout()
        btn_reset = QPushButton("Reset All")
        btn_reset.clicked.connect(self.reset_sliders)
        bottom_layout.addWidget(btn_reset)
        
        bottom_layout.addStretch()
        
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("Save Sequence")
        btn_save.setStyleSheet("background-color: #3584e4; font-weight: bold;")
        btn_save.clicked.connect(self.accept)
        
        bottom_layout.addWidget(btn_cancel)
        bottom_layout.addWidget(btn_save)
        main_layout.addLayout(bottom_layout)
        
        # Draw the faders for the first time
        self.rebuild_sliders()

    def rebuild_sliders(self):
        """Recreates vertical faders when the number of steps changes."""
        # Clears old sliders
        for i in reversed(range(self.sliders_layout.count())): 
            widget = self.sliders_layout.itemAt(i).widget()
            if widget is not None:
                widget.deleteLater()
        
        self.sliders.clear()
        num_steps = self.spin_steps.value()
        
        # Adapts the values array (cuts if smaller, adds 0 if larger)
        current_steps = self.sequence_data["steps"]
        if len(current_steps) < num_steps:
            current_steps.extend([0] * (num_steps - len(current_steps)))
        elif len(current_steps) > num_steps:
            current_steps = current_steps[:num_steps]
            
        self.sequence_data["steps"] = current_steps

        # Creates new sliders
        for i in range(num_steps):
            step_widget = QWidget()
            v_layout = QVBoxLayout(step_widget)
            v_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            
            # Value label (top)
            val_label = QLabel(str(current_steps[i]))
            val_label.setStyleSheet("color: #3584e4; font-weight: bold;")
            v_layout.addWidget(val_label, alignment=Qt.AlignmentFlag.AlignHCenter)
            
            # Slider
            slider = QSlider(Qt.Orientation.Vertical)
            slider.setRange(0, 127)
            slider.setValue(current_steps[i])
            slider.setMinimumHeight(150)
            
            # Updates the label when the slider moves
            slider.valueChanged.connect(lambda val, lbl=val_label: lbl.setText(str(val)))
            
            v_layout.addWidget(slider, alignment=Qt.AlignmentFlag.AlignHCenter)
            
            # Step label (bottom)
            step_label = QLabel(str(i + 1))
            v_layout.addWidget(step_label, alignment=Qt.AlignmentFlag.AlignHCenter)
            
            self.sliders_layout.addWidget(step_widget)
            self.sliders.append(slider)
            
        self.sliders_layout.addStretch()

    def reset_sliders(self):
        for slider in self.sliders:
            slider.setValue(0)

    def get_sequence_data(self):
        """Returns updated data for saving."""
        return {
            "num_steps": self.spin_steps.value(),
            "midi_channel": self.spin_channel.value(),
            "midi_cc": self.spin_cc.value(),
            "steps": [slider.value() for slider in self.sliders]
        }

class MappingDialog(QDialog):
    def __init__(self, mapping_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Input Mappings & MIDI Learn")
        self.resize(1000, 600)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.manager = mapping_manager
        
        layout = QVBoxLayout(self)
        
        # --- Top Section: MIDI Port Selection ---
        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("MIDI Input Port:"))
        self.combo_ports = QComboBox()
        self.combo_ports.addItem("None / Disabled")
        self.combo_ports.addItems(mido.get_input_names())
        
        current_port = self.manager.midi_port_name
        if current_port in [self.combo_ports.itemText(i) for i in range(self.combo_ports.count())]:
            self.combo_ports.setCurrentText(current_port)
            
        port_layout.addWidget(self.combo_ports)
        btn_apply_port = QPushButton("Apply Port")
        btn_apply_port.clicked.connect(self.apply_midi_port)
        port_layout.addWidget(btn_apply_port)
        layout.addLayout(port_layout)
        
        # --- Middle Section: Mappings Table ---
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Action", "MIDI Triggers", "Key Triggers", "Controls"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
        
        # --- Learn Feedback Label ---
        self.learn_label = QLabel("")
        self.learn_label.setStyleSheet("color: #ff9900; font-weight: bold;")
        self.learn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.learn_label)
        
        # --- Bottom Section ---
        btn_layout = QHBoxLayout()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)
        
        self.populate_table()
        
        # Listen to manager for learned inputs
        self.manager.midi_learned.connect(self.on_midi_learned)
        self.manager.key_learned.connect(self.on_key_learned)
        
        # Make the table not take away focus for keyboard events
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
    def apply_midi_port(self):
        port = self.combo_ports.currentText()
        if port == "None / Disabled": port = None
        self.manager.set_midi_port(port)
        QMessageBox.information(self, "Success", f"MIDI Input Port set to {port}")

    def populate_table(self):
        self.table.setRowCount(0)
        for action, data in self.manager.mappings.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            # Action Name
            self.table.setItem(row, 0, QTableWidgetItem(action))
            
            # MIDI
            midi_str = ", ".join([str(m) for m in data.get("midi", [])])
            self.table.setItem(row, 1, QTableWidgetItem(midi_str))
            
            # Keys
            keys_str = ", ".join(data.get("keys", []))
            self.table.setItem(row, 2, QTableWidgetItem(keys_str))
            
            # Controls
            ctrl_widget = QWidget()
            ctrl_layout = QHBoxLayout(ctrl_widget)
            ctrl_layout.setContentsMargins(0, 0, 0, 0)
            
            btn_learn_midi = QPushButton("Learn MIDI")
            btn_learn_midi.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn_learn_midi.clicked.connect(lambda _, a=action: self.start_learn(a, "midi"))
            
            btn_learn_key = QPushButton("Learn Key")
            btn_learn_key.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn_learn_key.clicked.connect(lambda _, a=action: self.start_learn(a, "keys"))
            
            btn_clear = QPushButton("Clear")
            btn_clear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn_clear.clicked.connect(lambda _, a=action: self.clear_mapping(a))
            
            ctrl_layout.addWidget(btn_learn_midi)
            ctrl_layout.addWidget(btn_learn_key)
            ctrl_layout.addWidget(btn_clear)

            self.table.setCellWidget(row, 3, ctrl_widget)

    def keyPressEvent(self, event):
        if self.manager.learning_type == "keys":
            # Consume all key events internally while learning
            if self.manager.handle_qt_key_press(event):
                event.accept()
                return
        elif self.manager.handle_qt_key_press(event):
            event.accept()
            return

        super().keyPressEvent(event)

    def start_learn(self, action, hw_type):
        self.manager.enter_learn_mode(action, hw_type)
        self.learn_label.setText(f"Learning {hw_type.upper()} for action: {action}. Press input now...")
        
        # Force capture of all keyboard strokes globally within the application (crucial for arrows/space)
        if hw_type == "keys":
            self.grabKeyboard()

    def on_midi_learned(self, midi_bytes):
        self.learn_label.setText(f"MIDI Learned: {midi_bytes}")
        action = self.manager.learning_action
        if action:
            self.manager.update_mapping(action, "midi", midi_bytes)
            self.populate_table()
            self.manager.cancel_learn_mode()

    def on_key_learned(self, key_name):
        self.releaseKeyboard()
        self.learn_label.setText(f"Key Learned: {key_name}")
        action = self.manager.learning_action
        if action:
            self.manager.update_mapping(action, "keys", key_name)
            self.populate_table()
            self.manager.cancel_learn_mode()

    def clear_mapping(self, action):
        self.manager.mappings[action] = {"midi": [], "keys": []}
        self.manager.data_manager.save_mappings()
        self.populate_table()
        
    def closeEvent(self, event):
        self.releaseKeyboard()
        self.manager.cancel_learn_mode()
        super().closeEvent(event)

class LanguageSettingsDialog(QDialog):
    def __init__(self, current_lang, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Language Settings")
        self.resize(300, 150)
        
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select Interface Language:"))
        
        self.combo_lang = QComboBox()
        self.languages = list(TRANSLATIONS.keys())
        self.combo_lang.addItems(self.languages)
        
        if current_lang in self.languages:
            self.combo_lang.setCurrentText(current_lang)
            
        layout.addWidget(self.combo_lang)
        
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("Apply")
        btn_save.setStyleSheet("background-color: #198754; color: white; font-weight: bold;")
        btn_save.clicked.connect(self.accept)
        
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
    def get_selected_language(self):
        return self.combo_lang.currentText()

class MidiActionsDialog(QDialog):
    def __init__(self, actions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MIDI Time Actions")
        self.resize(700, 450)
        self.actions = list(actions) if actions else []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Timed MIDI Events (CC/PC)</b>"))
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Time (ms)", "Type", "Ch", "CC/Prog #", "Value"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        btns_layout = QHBoxLayout()
        btn_add = QPushButton("+ Add Action")
        btn_add.clicked.connect(self.add_action_row)
        btn_remove = QPushButton("- Remove Selected")
        btn_remove.clicked.connect(self.remove_selected_row)
        btns_layout.addWidget(btn_add); btns_layout.addWidget(btn_remove); btns_layout.addStretch()
        layout.addLayout(btns_layout)
        save_layout = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("Save Actions")
        btn_save.setStyleSheet("background-color: #28a745; color: white; font-weight: bold;")
        btn_save.clicked.connect(self.accept)
        save_layout.addStretch(); save_layout.addWidget(btn_cancel); save_layout.addWidget(btn_save)
        layout.addLayout(save_layout)
        self.populate_table()

    def populate_table(self):
        self.table.setRowCount(0)
        for action in self.actions: self.add_table_row(action)

    def add_table_row(self, action=None):
        row = self.table.rowCount()
        self.table.insertRow(row)
        if action is None: action = {"time_ms": 0, "type": "cc", "channel": 1, "control": 0, "value": 0}
        self.table.setItem(row, 0, QTableWidgetItem(str(action.get("time_ms", 0))))
        type_combo = QComboBox()
        type_combo.addItems(["CC", "PC"])
        type_combo.setCurrentText(action.get("type", "cc").upper())
        self.table.setCellWidget(row, 1, type_combo)
        self.table.setItem(row, 2, QTableWidgetItem(str(action.get("channel", 1))))
        num_val = action.get("control") if type_combo.currentText() == "CC" else action.get("program", 0)
        self.table.setItem(row, 3, QTableWidgetItem(str(num_val if num_val is not None else 0)))
        self.table.setItem(row, 4, QTableWidgetItem(str(action.get("value", 0))))

    def add_action_row(self): self.add_table_row()
    def remove_selected_row(self):
        curr = self.table.currentRow()
        if curr >= 0: self.table.removeRow(curr)

    def get_actions(self):
        new_actions = []
        for r in range(self.table.rowCount()):
            try:
                time_ms = int(self.table.item(r, 0).text())
                msg_type = self.table.cellWidget(r, 1).currentText().lower()
                channel = int(self.table.item(r, 2).text())
                num = int(self.table.item(r, 3).text())
                val_item = self.table.item(r, 4)
                val = int(val_item.text()) if val_item else 0
                action = {"time_ms": time_ms, "type": msg_type, "channel": channel}
                if msg_type == "cc":
                    action["control"] = num
                    action["value"] = val
                else:
                    action["program"] = num
                new_actions.append(action)
            except Exception: continue
        new_actions.sort(key=lambda x: x["time_ms"])
        return new_actions
class SortSettingsDialog(QDialog):
    """
    Dialog to choose the sorting strategy and priorities for the Setlist Optimizer.
    """
    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Setlist Optimization Settings")
        self.setMinimumWidth(400)
        self.initial_settings = settings or {}
        
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Energy Build", "Harmonic Flow", "BPM Build", "Custom Mix"])
        
        mode = self.initial_settings.get("mode", "Energy Build")
        self.combo_mode.setCurrentText(mode)
        self.combo_mode.currentIndexChanged.connect(self.on_mode_changed)
        form.addRow("Optimization Mode:", self.combo_mode)
        
        # Harmonic Priority Slider
        self.slider_priority = QSlider(Qt.Orientation.Horizontal)
        self.slider_priority.setRange(0, 100)
        
        weight_val = int(self.initial_settings.get("harmonic_weight", 0.2) * 100)
        self.slider_priority.setValue(weight_val)
        self.lbl_priority = QLabel(f"Harmonic Weight: {weight_val}%")
        self.slider_priority.valueChanged.connect(self.update_priority_label)
        
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(self.slider_priority)
        slider_layout.addWidget(self.lbl_priority)
        form.addRow("Mode Interlock:", slider_layout)
        
        # Options
        self.chk_lock_first = QCheckBox("Lock Current First Song")
        self.chk_lock_first.setChecked(self.initial_settings.get("lock_first", False))
        form.addRow("", self.chk_lock_first)
        
        self.chk_global = QCheckBox("Global Flow Optimizer (Simulated Annealing)")
        self.chk_global.setChecked(self.initial_settings.get("use_global_opt", True))
        self.chk_global.setToolTip("Uses advanced heuristics to find the globally optimal sequence instead of a simple greedy path.")
        form.addRow("", self.chk_global)
        
        layout.addLayout(form)
        layout.addSpacing(20)
        
        btns = QHBoxLayout()
        self.btn_ok = QPushButton("🪄 Run Magic Sort")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_ok.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 10px;")
        
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        
        btns.addStretch()
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)
        layout.addLayout(btns)

    def update_priority_label(self, val):
        self.lbl_priority.setText(f"Harmonic Weight: {val}%")

    def on_mode_changed(self, idx):
        # Preset behaviors
        if idx == 0: # Energy Build
            self.slider_priority.setValue(20)
        elif idx == 1: # Harmonic Flow
            self.slider_priority.setValue(80)
        elif idx == 2: # BPM Build
            self.slider_priority.setValue(20)
        # Custom doesn't change anything

    def get_settings(self):
        return {
            "mode": self.combo_mode.currentText(),
            "harmonic_weight": self.slider_priority.value() / 100.0,
            "lock_first": self.chk_lock_first.isChecked(),
            "use_global_opt": self.chk_global.isChecked()
        }

class PatchLibraryDialog(QDialog):
    """Dialog to manage the Global Patch Library."""
    def __init__(self, data_manager, sync_engine=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Global Patch Library")
        self.resize(800, 500)
        self.data_manager = data_manager
        self.sync_engine = sync_engine
        self.patches = copy.deepcopy(data_manager.patches)
        
        main_layout = QHBoxLayout(self)
        
        # Left: Patch List
        left_panel = QVBoxLayout()
        self.patch_list = QListWidget()
        self.patch_list.addItems(sorted(self.patches.keys()))
        self.patch_list.currentItemChanged.connect(self.on_patch_selection_changed)
        left_panel.addWidget(QLabel("<b>Patches</b>"))
        left_panel.addWidget(self.patch_list)
        
        btn_row = QHBoxLayout()
        btn_add = QPushButton("+"); btn_add.clicked.connect(self.add_new_patch)
        btn_duplicate = QPushButton("Duplicate"); btn_duplicate.clicked.connect(self.duplicate_patch)
        btn_rename = QPushButton("Rename"); btn_rename.clicked.connect(self.rename_patch)
        btn_rem = QPushButton("-"); btn_rem.clicked.connect(self.remove_patch)
        btn_row.addWidget(btn_add); btn_row.addWidget(btn_duplicate); btn_row.addWidget(btn_rename); btn_row.addWidget(btn_rem)
        left_panel.addLayout(btn_row)
        main_layout.addLayout(left_panel, 1)
        
        # Right: Action Editor
        right_panel = QVBoxLayout()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Type", "Ch", "Num/CC", "Value"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        right_panel.addWidget(QLabel("<b>Actions in Patch</b>"))
        right_panel.addWidget(self.table)
        
        act_btns = QHBoxLayout()
        btn_add_act = QPushButton("Add Action"); btn_add_act.clicked.connect(self.add_action_row)
        btn_rem_act = QPushButton("Remove Action"); btn_rem_act.clicked.connect(self.remove_action_row)
        
        self.btn_test_patch = QPushButton("⚡ Test Patch")
        self.btn_test_patch.setStyleSheet("background-color: #3584e4; color: white; font-weight: bold;")
        self.btn_test_patch.clicked.connect(self.test_current_patch)
        
        act_btns.addWidget(btn_add_act); act_btns.addWidget(btn_rem_act); act_btns.addWidget(self.btn_test_patch)
        right_panel.addLayout(act_btns)
        
        btn_save_all = QPushButton("Save Library")
        btn_save_all.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; height: 35px;")
        btn_save_all.clicked.connect(self.accept)
        right_panel.addWidget(btn_save_all)
        main_layout.addLayout(right_panel, 2)

    def add_new_patch(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New Patch", "Patch Name:")
        if ok and name:
            self.patches[name] = []
            self.patch_list.addItem(name)
            self.patch_list.setCurrentRow(self.patch_list.count()-1)

    def duplicate_patch(self):
        item = self.patch_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Duplicate Patch", "Please select a patch to duplicate.")
            return
        
        original_name = item.text()
        from PyQt6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(self, "Duplicate Patch", "New Patch Name:", text=f"{original_name} (copy)")
        if ok and new_name:
            if new_name in self.patches:
                QMessageBox.warning(self, "Error", "A patch with this name already exists.")
                return
            self.patches[new_name] = copy.deepcopy(self.patches[original_name])
            self.patch_list.addItem(new_name)
            new_items = self.patch_list.findItems(new_name, Qt.MatchFlag.MatchExactly)
            if new_items:
                self.patch_list.setCurrentItem(new_items[0])  # Select the duplicated item

    def rename_patch(self):
        item = self.patch_list.currentItem()
        if not item: return
        old_name = item.text()
        from PyQt6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(self, "Rename Patch", "New Name:", text=old_name)
        if ok and new_name and new_name != old_name:
            if new_name in self.patches:
                QMessageBox.warning(self, "Error", "A patch with this name already exists.")
                return
            
            # 1. Save current table state to the old name key first
            self.save_table_to_dict(old_name)
            
            # 2. Update references in the local dictionary
            self.patches[new_name] = self.patches.pop(old_name)
            
            # 3. Update the UI list
            item.setText(new_name)
            
            # 4. Update references in the song database
            for song_title, song_data in self.data_manager.database.items():
                if song_data.get("Patch") == old_name:
                    song_data["Patch"] = new_name
                patch_list = song_data.get("Patches")
                if isinstance(patch_list, list):
                    song_data["Patches"] = [new_name if p == old_name else p for p in patch_list]
            self.data_manager.save_database()

    def remove_patch(self):
        item = self.patch_list.currentItem()
        if not item: return
        
        name = item.text()
        # Remove from dictionary first
        if name in self.patches:
            del self.patches[name]

        # Remove any references to this patch from songs
        for song_data in self.data_manager.database.values():
            if song_data.get("Patch") == name:
                song_data["Patch"] = None
            patch_list = song_data.get("Patches")
            if isinstance(patch_list, list):
                cleaned = [p for p in patch_list if p != name]
                song_data["Patches"] = cleaned
                song_data["Patch"] = cleaned[0] if cleaned else None
        self.data_manager.save_database()
            
        # Remove from UI while blocking signals to prevent selection-change auto-save
        self.patch_list.blockSignals(True)
        self.patch_list.takeItem(self.patch_list.row(item))
        self.patch_list.blockSignals(False)
        
        # Clear table and load the new current selection (if any)
        self.table.setRowCount(0)
        new_item = self.patch_list.currentItem()
        if new_item:
            for act in self.patches.get(new_item.text(), []):
                self.add_action_row(act)

    def test_current_patch(self):
        """Saves current table edits and executes the patch immediately via SyncEngine."""
        self.save_current_table_to_dict()
        item = self.patch_list.currentItem()
        if not item: return
        
        actions = self.patches.get(item.text(), [])
        if self.sync_engine:
            self.sync_engine.execute_patch(actions)

    def on_patch_selection_changed(self, current, previous):
        # 1. Save data from the patch we are leaving
        if previous:
            self.save_table_to_dict(previous.text())
            
        # 2. Clear and load data for the new selection
        self.table.setRowCount(0)
        if current:
            for act in self.patches.get(current.text(), []):
                self.add_action_row(act)

    def add_action_row(self, act=None):
        r = self.table.rowCount(); self.table.insertRow(r)
        combo = QComboBox(); combo.addItems(["PC", "CC"]); self.table.setCellWidget(r, 0, combo)
        if act:
            combo.setCurrentText(act.get("type", "PC").upper())
            self.table.setItem(r, 1, QTableWidgetItem(str(act.get("channel", 1))))
            num = act.get("program") if act.get("type")=="pc" else act.get("control")
            self.table.setItem(r, 2, QTableWidgetItem(str(num or 0)))
            self.table.setItem(r, 3, QTableWidgetItem(str(act.get("value", 0))))
        else:
            self.table.setItem(r, 1, QTableWidgetItem("1"))
            self.table.setItem(r, 2, QTableWidgetItem("0"))
            self.table.setItem(r, 3, QTableWidgetItem("0"))

    def remove_action_row(self): self.table.removeRow(self.table.currentRow())

    def save_current_table_to_dict(self):
        """Helper for external calls (like from MainWindow) to save active work."""
        item = self.patch_list.currentItem()
        if item:
            self.save_table_to_dict(item.text())

    def save_table_to_dict(self, patch_name):
        """Reads the table UI and updates the local patches dictionary for a specific name."""
        if not patch_name: return
        actions = []
        for r in range(self.table.rowCount()):
            try:
                # Get Type from ComboBox widget
                t = self.table.cellWidget(r, 0).currentText().lower()
                
                # Get values from TableItems with safety checks
                ch_item = self.table.item(r, 1)
                num_item = self.table.item(r, 2)
                val_item = self.table.item(r, 3)
                
                ch = int(ch_item.text()) if ch_item else 1
                num = int(num_item.text()) if num_item else 0
                val = int(val_item.text()) if val_item else 0
                
                act = {"type": t, "channel": ch}
                if t == "pc": act["program"] = num
                else: act["control"] = num; act["value"] = val
                actions.append(act)
            except (ValueError, AttributeError):
                continue
        self.patches[patch_name] = actions
