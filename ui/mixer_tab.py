from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QScrollArea, QFrame, QPushButton, QButtonGroup, 
                             QSizePolicy)
from PyQt6.QtCore import Qt, pyqtSignal

from ui_components import ClickableSlider, SmoothDial

class XAirChannelStrip(QWidget):
    """A single vertical channel strip with two-way OSC data binding."""
    # Signals now carry ch_id, ch_type ("CH" or "RTN"), and value
    fader_moved = pyqtSignal(int, str, float) 
    mute_toggled = pyqtSignal(int, str, bool)
    pan_moved = pyqtSignal(int, str, float)

    def __init__(self, ch_id, name, ch_type="CH", color="#3498db", parent=None):
        super().__init__(parent)
        self.ch_id = ch_id
        self.ch_type = ch_type
        self.channel_name = name
        self.color = color
        
        # State Dictionary expanded to include the 4 FX Send Buses
        self.mix_levels = {
            "MAIN": 0.0, 
            "BUS1": 0.0, "BUS2": 0.0, "BUS3": 0.0, "BUS4": 0.0, "BUS5": 0.0, "BUS6": 0.0,
            "FX1": 0.0, "FX2": 0.0, "FX3": 0.0, "FX4": 0.0
        }
        self.mutes = {k: False for k in self.mix_levels.keys()}
        
        self.current_layer = "MAIN"
        self.init_ui()

    def init_ui(self):
        self.setFixedWidth(76)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 5, 2, 5)
        layout.setSpacing(5)

        # 1. Scribble Strip (Name & Color)
        self.lbl_scribble = QLabel(self.channel_name)
        self.lbl_scribble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_scribble.setStyleSheet(f"""
            background-color: {self.color}; color: white;
            font-weight: bold; border-radius: 4px; padding: 4px;
        """)
        layout.addWidget(self.lbl_scribble)

        # Channel Number
        id_text = "MASTER" if self.ch_type == "LR" else f"{self.ch_type} {self.ch_id:02d}"
        self.lbl_id = QLabel(id_text)
        self.lbl_id.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_id.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self.lbl_id)

        # 2. Pan Dial
        self.dial_pan = SmoothDial()
        self.dial_pan.setRange(-100, 100)
        self.dial_pan.setValue(0)
        self.dial_pan.setFixedSize(50, 50)
        self.dial_pan.valueChanged.connect(self._on_pan_changed)
        pan_layout = QHBoxLayout()
        pan_layout.addWidget(self.dial_pan, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(pan_layout)

        # 3. Mute & Solo Buttons
        self.btn_mute = QPushButton("MUTE")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mute.setStyleSheet(self._mute_style(False))
        self.btn_mute.toggled.connect(self._on_mute_toggled)
        layout.addWidget(self.btn_mute)

        self.btn_solo = QPushButton("SOLO")
        self.btn_solo.setCheckable(True)
        self.btn_solo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_solo.setStyleSheet("QPushButton:checked { background-color: #f1c40f; color: black; }")
        layout.addWidget(self.btn_solo)

        # 4. Fader Track
        self.slider = ClickableSlider(Qt.Orientation.Vertical)
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.slider.setMinimumHeight(250)
        self.slider.valueChanged.connect(self._on_fader_changed)
        
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(self.slider, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(slider_layout)

        # 5. dB Value Label
        self.lbl_db = QLabel("-oo dB")
        self.lbl_db.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_db.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(self.lbl_db)

    def _mute_style(self, is_muted):
        if is_muted: return "background-color: #e74c3c; color: white; font-weight: bold; border-radius: 3px;"
        return "background-color: #333; color: #888; border-radius: 3px;"

    def _update_db_text(self, norm_val):
        if norm_val <= 0.01:
            self.lbl_db.setText("-oo dB")
        else:
            db = (norm_val * 40) - 30
            self.lbl_db.setText(f"{db:+.1f} dB")

    # --- UI EVENT HANDLERS (Emit OUT to SyncEngine) ---
    def _on_mute_toggled(self, checked):
        if self.ch_type == "LR": self.mutes[self.current_layer] = checked
        else: self.mutes["MAIN"] = checked
        self.btn_mute.setStyleSheet(self._mute_style(checked))
        self.mute_toggled.emit(self.ch_id, self.ch_type, checked)

    def _on_fader_changed(self, val):
        norm_val = val / 1000.0
        self.mix_levels[self.current_layer] = norm_val
        self._update_db_text(norm_val)
        self.fader_moved.emit(self.ch_id, self.ch_type, norm_val)

    def _on_pan_changed(self, val):
        self.pan_moved.emit(self.ch_id, self.ch_type, val / 100.0)

    # --- EXTERNAL UPDATES (Incoming FROM OSC/SyncEngine) ---
    def update_name(self, name):
        self.channel_name = name
        if self.ch_type != "LR" or self.current_layer == "MAIN":
            self.lbl_scribble.setText(name)

    def update_fader(self, level, layer="MAIN"):
        self.mix_levels[layer] = level
        if self.current_layer == layer:
            self.slider.blockSignals(True) # PREVENT INFINITE OSC LOOP
            self.slider.setValue(int(level * 1000))
            self.slider.blockSignals(False)
            self._update_db_text(level)

    def update_mute(self, is_muted, layer="MAIN"):
        self.mutes[layer] = is_muted
        should_update_ui = (self.ch_type == "LR" and self.current_layer == layer) or (self.ch_type != "LR")
            
        if should_update_ui:
            self.btn_mute.blockSignals(True)
            self.btn_mute.setChecked(is_muted)
            self.btn_mute.setStyleSheet(self._mute_style(is_muted))
            self.btn_mute.blockSignals(False)

    def update_pan(self, pan):
        self.dial_pan.blockSignals(True)
        self.dial_pan.setValue(int(pan * 100))
        self.dial_pan.blockSignals(False)

    def set_layer(self, layer_name):
        self.current_layer = layer_name
        self.update_fader(self.mix_levels[layer_name], layer_name)
        
        if self.ch_type == "LR":
            self.update_mute(self.mutes[layer_name], layer_name)
            layer_title = self.channel_name if layer_name == "MAIN" else layer_name.replace("BUS", "BUS ").replace("FX", "FX ")
            self.lbl_scribble.setText(layer_title)


class MixerTab(QWidget):
    def __init__(self, sync_engine, parent=None):
        super().__init__(parent)
        self.sync_engine = sync_engine
        
        # New Routing Dictionary: keys are (ch_type, ch_id)
        self.strips = {} 
        self.active_layer = "MAIN"
        
        self.init_ui()
        self.connect_osc_signals()

    def connect_osc_signals(self):
        if hasattr(self.sync_engine, 'name_signal'):
            self.sync_engine.name_signal.connect(self.on_osc_name)
        if hasattr(self.sync_engine, 'fader_signal'):
            self.sync_engine.fader_signal.connect(self.on_osc_fader)
        if hasattr(self.sync_engine, 'mute_signal'):
            self.sync_engine.mute_signal.connect(self.on_osc_mute)
        if hasattr(self.sync_engine, 'pan_signal'):
            self.sync_engine.pan_signal.connect(self.on_osc_pan)

    # --- Incoming OSC Event Routing ---
    def on_osc_name(self, ch_id, ch_type, name):
        if (ch_type, ch_id) in self.strips:
            self.strips[(ch_type, ch_id)].update_name(name)
        elif ch_type == "LR":
            self.master_strip.channel_name = name
            self.master_strip.update_name(name)

    def on_osc_fader(self, ch_id, ch_type, level):
        # Normal fader movement updates the background "MAIN" state
        if (ch_type, ch_id) in self.strips:
            self.strips[(ch_type, ch_id)].update_fader(level, layer="MAIN")
        elif ch_type == "LR":
            self.master_strip.update_fader(level, layer="MAIN")
        elif ch_type == "BUS" and 1 <= ch_id <= 6:
            self.master_strip.update_fader(level, layer=f"BUS{ch_id}")
        elif ch_type == "FX" and 1 <= ch_id <= 4:
            self.master_strip.update_fader(level, layer=f"FX{ch_id}")

    def on_osc_mute(self, ch_id, ch_type, is_muted):
        if (ch_type, ch_id) in self.strips:
            self.strips[(ch_type, ch_id)].update_mute(is_muted, layer="MAIN")
        elif ch_type == "LR":
            self.master_strip.update_mute(is_muted, layer="MAIN")
        elif ch_type == "BUS" and 1 <= ch_id <= 6:
            self.master_strip.update_mute(is_muted, layer=f"BUS{ch_id}")
        elif ch_type == "FX" and 1 <= ch_id <= 4:
            self.master_strip.update_mute(is_muted, layer=f"FX{ch_id}")

    def on_osc_pan(self, ch_id, ch_type, pan):
        if (ch_type, ch_id) in self.strips:
            self.strips[(ch_type, ch_id)].update_pan(pan)

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(5)

        # --- LEFT: SCROLLABLE CHANNELS ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet("background-color: #1a1a1a;")
        
        self.channels_container = QWidget()
        self.channels_layout = QHBoxLayout(self.channels_container)
        self.channels_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        # 1. Generate Mics 1-16
        for i in range(1, 17):
            self.add_strip_to_ui(i, f"Mic {i}", ch_type="CH", color="#2ecc71")
            
        # 2. Generate Aux (Usually CH 17)
        self.add_strip_to_ui(17, "Aux L/R", ch_type="CH", color="#e67e22")

        # 3. Generate FX Returns 1-4
        for i in range(1, 5):
            # Purple color for FX Returns
            self.add_strip_to_ui(i, f"FX {i} RTN", ch_type="RTN", color="#9b59b6")

        self.scroll_area.setWidget(self.channels_container)
        main_layout.addWidget(self.scroll_area, stretch=4)

        # --- RIGHT: MASTER FADER & LAYER SELECTION ---
        right_panel = QWidget()
        right_panel.setFixedWidth(150)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Master Fader
        self.master_strip = XAirChannelStrip(ch_id=99, name="MAIN LR", ch_type="LR", color="#e74c3c")
        self.master_strip.btn_solo.hide()
        self.master_strip.dial_pan.hide()
        self.master_strip.fader_moved.connect(self.handle_master_fader)
        self.master_strip.mute_toggled.connect(self.handle_master_mute)
        right_layout.addWidget(self.master_strip)

        # Sends on Faders (Layer Buttons)
        lbl_layers = QLabel("MIX LAYERS")
        lbl_layers.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_layers.setStyleSheet("font-weight: bold; color: #aaa; margin-top: 15px;")
        right_layout.addWidget(lbl_layers)

        self.layer_group = QButtonGroup(self)
        self.layer_group.setExclusive(True)

        layers = [
            ("MAIN LR", "MAIN"),
            ("BUS 1", "BUS1"), ("BUS 2", "BUS2"), ("BUS 3", "BUS3"),
            ("BUS 4", "BUS4"), ("BUS 5", "BUS5"), ("BUS 6", "BUS6"),
            # New FX Sends
            ("FX 1", "FX1"), ("FX 2", "FX2"), ("FX 3", "FX3"), ("FX 4", "FX4")
        ]

        for text, layer_id in layers:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton { background-color: #333; color: white; padding: 6px; font-size: 11px; }
                QPushButton:checked { background-color: #3498db; font-weight: bold; }
            """)
            if layer_id == "MAIN": btn.setChecked(True)
            
            btn.clicked.connect(lambda checked, lid=layer_id: self.switch_layer(lid))
            self.layer_group.addButton(btn)
            right_layout.addWidget(btn)

        right_layout.addStretch()
        main_layout.addWidget(right_panel, stretch=1)

    def add_strip_to_ui(self, ch_id, name, ch_type, color):
        """Helper to create and route a channel strip."""
        strip = XAirChannelStrip(ch_id=ch_id, name=name, ch_type=ch_type, color=color)
        strip.fader_moved.connect(self.handle_fader_moved)
        strip.mute_toggled.connect(self.handle_mute_toggled)
        strip.pan_moved.connect(self.handle_pan_moved)
        
        self.strips[(ch_type, ch_id)] = strip
        self.channels_layout.addWidget(strip)

    # --- Outgoing Event Handlers (UI -> SyncEngine -> OSC) ---
    def switch_layer(self, layer_id):
        self.active_layer = layer_id
        for strip in self.strips.values():
            strip.set_layer(layer_id)
        self.master_strip.set_layer(layer_id)

    def handle_fader_moved(self, ch_id, ch_type, value):
        if self.active_layer == "MAIN":
            if ch_type == "CH" and hasattr(self.sync_engine, 'set_mixer_channel_fader'):
                self.sync_engine.set_mixer_channel_fader(ch_id, value)
            elif ch_type == "RTN" and hasattr(self.sync_engine, 'set_mixer_rtn_fader'):
                self.sync_engine.set_mixer_rtn_fader(ch_id, value)
                
        elif self.active_layer.startswith("BUS"):
            bus_num = int(self.active_layer.replace("BUS", ""))
            if ch_type == "CH" and hasattr(self.sync_engine, 'set_mixer_channel_send_level'):
                self.sync_engine.set_mixer_channel_send_level(ch_id, bus_num, value)
            elif ch_type == "RTN" and hasattr(self.sync_engine, 'set_mixer_rtn_send_level'):
                # This injects the FX return into a musician's monitor bus
                self.sync_engine.set_mixer_rtn_send_level(ch_id, bus_num, value)
                
        elif self.active_layer.startswith("FX"):
            fx_num = int(self.active_layer.replace("FX", ""))
            bus_num = fx_num + 6 # Based on your original code where FX1 = bus 7
            if ch_type == "CH" and hasattr(self.sync_engine, 'set_mixer_channel_send_level'):
                self.sync_engine.set_mixer_channel_send_level(ch_id, bus_num, value)
            # We ignore RTN faders here. You cannot send an FX Return to an FX Send (it causes an infinite feedback loop).

    def handle_mute_toggled(self, ch_id, ch_type, is_muted):
        if ch_type == "CH" and hasattr(self.sync_engine, 'set_mixer_channel_mute'):
            self.sync_engine.set_mixer_channel_mute(ch_id, is_muted)
        elif ch_type == "RTN" and hasattr(self.sync_engine, 'set_mixer_rtn_mute'):
            self.sync_engine.set_mixer_rtn_mute(ch_id, is_muted)

    def handle_pan_moved(self, ch_id, ch_type, value):
        if ch_type == "CH" and hasattr(self.sync_engine, 'set_mixer_channel_pan'):
            self.sync_engine.set_mixer_channel_pan(ch_id, value)
        elif ch_type == "RTN" and hasattr(self.sync_engine, 'set_mixer_rtn_pan'):
            self.sync_engine.set_mixer_rtn_pan(ch_id, value)

    def handle_master_fader(self, ch_id, ch_type, value):
        if self.active_layer == "MAIN":
            if hasattr(self.sync_engine, 'set_mixer_main_fader'):
                self.sync_engine.set_mixer_main_fader(value)
        elif self.active_layer.startswith("BUS"):
            bus_num = int(self.active_layer.replace("BUS", ""))
            if hasattr(self.sync_engine, 'set_mixer_bus_master'):
                self.sync_engine.set_mixer_bus_master(bus_num, value)
        elif self.active_layer.startswith("FX"):
            fx_num = int(self.active_layer.replace("FX", ""))
            bus_num = fx_num + 6 # FX Sends are treated as internal buses 7-10
            if hasattr(self.sync_engine, 'set_mixer_bus_master'):
                self.sync_engine.set_mixer_bus_master(bus_num, value)

    def handle_master_mute(self, ch_id, ch_type, is_muted):
        if self.active_layer == "MAIN":
            if hasattr(self.sync_engine, 'set_mixer_main_mute'):
                self.sync_engine.set_mixer_main_mute(is_muted)
        elif self.active_layer.startswith("BUS"):
            bus_num = int(self.active_layer.replace("BUS", ""))
            if hasattr(self.sync_engine, 'set_mixer_bus_mute'):
                self.sync_engine.set_mixer_bus_mute(bus_num, is_muted)
        elif self.active_layer.startswith("FX"):
            fx_num = int(self.active_layer.replace("FX", ""))
            bus_num = fx_num + 6
            if hasattr(self.sync_engine, 'set_mixer_bus_mute'):
                self.sync_engine.set_mixer_bus_mute(bus_num, is_muted)