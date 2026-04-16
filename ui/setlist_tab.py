import os
import json
import logging
import threading
import socket
import numpy as np
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem, 
                             QTextBrowser, QLabel, QPushButton, QSplitter, QSlider, QCheckBox, 
                             QScrollArea, QFrame, QComboBox, QGridLayout, QMenu, QMessageBox, 
                             QProgressDialog)
from PyQt6.QtGui import QIcon, QCursor, QPixmap
from PyQt6.QtGui import QImage
import qrcode
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QSize, QEvent
import soundfile as sf

from ui.themes import THEMES
from ui.workers import SongLoaderWorker, BPMWorker
from utils import find_file_case_insensitive
from ui_components import SmoothDial, ClickableSlider, WaveformWidget
from dialogs import SongEditorDialog, EditSetlistDialog
from ui.undo_commands import MoveSongCommand, UpdateSetlistCommand, TransposeCommand
from ui.chord_renderer import create_chord_pixmap
from ui.chord_lib import GUITAR_CHORDS

# Web Server components
try:
    from web_server import web_bridge, app_state, push_web_state
except ImportError:
    web_bridge = None
    app_state = {}
    push_web_state = lambda: None

from confidence_server import update_monitor_lyrics, update_monitor_scroll

logger = logging.getLogger("MSM2_SETLIST")

class SetlistTab(QWidget):
    def __init__(self, data_manager, sync_engine, audio_engine, mapping_manager, undo_stack, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.data_manager = data_manager
        self.sync_engine = sync_engine
        self.audio_engine = audio_engine
        self.mapping_manager = mapping_manager
        self.undo_stack = undo_stack

        # Local state
        self.is_live_mode = False
        self.current_song = None
        self.transpose_steps = 0
        self.current_csv = "Default.csv"
        self.chord_font_size = 14 
        self.is_user_seeking = False
        self.is_autoscrolling = False
        self.is_remote_scrolling = False
        self.autoscroll_interval = 50

        self.init_ui()
        self.setup_timers()
        self.setup_animations()
        
        # Connect signals
        self.sync_engine.tick_signal.connect(self.handle_sync_tick)
        self.sync_engine.jitter_signal.connect(self.update_jitter_display)
        self.sync_engine.mtc_started_signal.connect(self.start_tick_disable_timer)
        self.sync_engine.link_sync_signal.connect(self.update_web_link_state)
        
        if web_bridge:
            web_bridge.web_command_signal.connect(self.handle_web_command)

    def setup_timers(self):
        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self.auto_scroll_step)
        
        self.save_speed_timer = QTimer(self)
        self.save_speed_timer.setSingleShot(True)
        self.save_speed_timer.timeout.connect(self.save_autoscroll_speed)
        
        self.tick_10s_timer = QTimer(self)
        self.tick_10s_timer.setSingleShot(True)
        self.tick_10s_timer.timeout.connect(self.auto_mute_click)
        
        # High-frequency timer for predictive visual sync
        self.visual_sync_timer = QTimer(self)
        self.visual_sync_timer.timeout.connect(self.update_visual_sync)

    def setup_animations(self):
        # Smooth scrolling animation
        self.scroll_animation = QPropertyAnimation(self.chordpro_display.verticalScrollBar(), b"value")
        self.scroll_animation.setEasingCurve(QEasingCurve.Type.InOutCubic) 
        self.scroll_animation.setDuration(2000)

    def _get_local_ip(self):
        """Helper to get the local IP address."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('10.255.255.255', 1))
            ip = s.getsockname()[0]
        except Exception:
            ip = '127.0.0.1' # Fallback for no network
        finally:
            s.close()
        return ip

    def _update_qr_code(self):
        # This will be implemented in Step 3
        pass

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # --- LEFT PANEL (Setlist & Sync) ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        self.combo_setlists = QComboBox()
        self.combo_setlists.currentTextChanged.connect(self.load_setlist_from_combo)
        self.lbl_setlist = QLabel("<b>SETLIST:</b>")
        left_layout.addWidget(self.lbl_setlist)
        left_layout.addWidget(self.combo_setlists)
        
        setlist_btns_layout = QHBoxLayout()
        self.btn_new_setlist = QPushButton("New Setlist")
        self.btn_new_setlist.setIcon(QIcon("assets/icons/add.svg"))
        self.btn_new_setlist.setIconSize(QSize(16, 16))
        self.btn_new_setlist.clicked.connect(self.create_new_setlist)
        self.btn_edit_setlist = QPushButton("Edit Setlist")
        self.btn_edit_setlist.setIcon(QIcon("assets/icons/edit.svg"))
        self.btn_edit_setlist.setIconSize(QSize(16, 16))
        self.btn_edit_setlist.clicked.connect(self.edit_current_setlist)
        self.btn_add_song = QPushButton("New Song (DB)")
        self.btn_add_song.setIcon(QIcon("assets/icons/music-note.svg"))
        self.btn_add_song.setIconSize(QSize(16, 16))
        self.btn_add_song.clicked.connect(self.add_new_song)
        self.btn_edit_song = QPushButton("Edit Song (DB)")
        self.btn_edit_song.setIcon(QIcon("assets/icons/edit.svg"))
        self.btn_edit_song.setIconSize(QSize(16, 16))
        self.btn_edit_song.clicked.connect(self.open_song_editor)
        
        setlist_btns_layout.addWidget(self.btn_new_setlist)
        setlist_btns_layout.addWidget(self.btn_edit_setlist)
        setlist_btns_layout.addWidget(self.btn_add_song)
        setlist_btns_layout.addWidget(self.btn_edit_song)
        for btn in [self.btn_new_setlist, self.btn_edit_setlist, self.btn_add_song, self.btn_edit_song]:
            btn.setFixedHeight(28)
        left_layout.addLayout(setlist_btns_layout)
        
        self.setlist_widget = QTreeWidget()
        self.setlist_widget.setHeaderLabels(["Song", "Notes"])
        self.setlist_widget.setColumnWidth(0, 230)
        self.setlist_widget.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.setlist_widget.setIndentation(0)
        self.setlist_widget.setDropIndicatorShown(True)
        self.setlist_widget.itemClicked.connect(self.on_song_selected)
        self.setlist_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setlist_widget.customContextMenuRequested.connect(self.show_song_context_menu)
        self.original_drop_event = self.setlist_widget.dropEvent
        self.setlist_widget.dropEvent = self._custom_drop_event
        left_layout.addWidget(self.setlist_widget)
        
        sync_header = QHBoxLayout()
        self.lbl_sync_header = QLabel("<b>METRONOME</b>")
        sync_header.addWidget(self.lbl_sync_header)
        
        self.chk_10s_tick = QCheckBox("Tick AutoMute")
        sync_header.addWidget(self.chk_10s_tick)
        
        sync_header.addStretch()
        self.lbl_total_time = QLabel("Total: 00:00")
        self.lbl_total_time.setStyleSheet("color: #4ade80; font-weight: bold; font-family: monospace; font-size: 10px;")
        sync_header.addWidget(self.lbl_total_time)
        self.jitter_label = QLabel("Err: 0.00ms")
        self.jitter_label.setStyleSheet("color: #888; font-family: monospace; font-size: 10px;")
        sync_header.addStretch()
        sync_header.addWidget(self.jitter_label)
        left_layout.addLayout(sync_header)

        self.btn_sync = QPushButton("START METRONOME")
        self.btn_sync.setCheckable(True)
        self.btn_sync.clicked.connect(self.toggle_sync)
        left_layout.addWidget(self.btn_sync)

        self.btn_live_mode = QPushButton("🔴  LIVE MODE: OFF")
        self.btn_live_mode.setCheckable(True)
        self.btn_live_mode.setStyleSheet(
            "QPushButton { background-color: #333; color: #aaa; font-weight: bold; height: 28px; border: 2px solid #555; border-radius: 4px; }"
            "QPushButton:checked { background-color: #8b0000; color: white; border: 2px solid #cc0000; }"
        )
        self.btn_live_mode.clicked.connect(self._on_live_mode_button_clicked)
        left_layout.addWidget(self.btn_live_mode)

        splitter.addWidget(left_panel)
        
        # --- CENTER PANEL (Lyrics/Chords Display) ---
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        
        ctrl_layout = QHBoxLayout()
        btn_trans_down = QPushButton("Key -1")
        self.lbl_trans = QLabel("Key: 0")
        self.lbl_trans.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_trans_up = QPushButton("Key +1")
        
        btn_zoom_out = QPushButton("A-")
        btn_zoom_out.setFixedWidth(40)
        btn_zoom_in = QPushButton("A+")
        btn_zoom_in.setFixedWidth(40)
        
        self.btn_scroll = QPushButton("Auto-Scroll: OFF")
        self.btn_scroll.setCheckable(True)
        self.btn_scroll.clicked.connect(self.toggle_autoscroll)
        
        self.lbl_scroll_speed = QLabel("Speed:")
        self.scroll_speed_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.scroll_speed_slider.setRange(10, 300) # Increased range for longer songs
        self.scroll_speed_slider.setValue(self.autoscroll_interval)
        self.scroll_speed_slider.setInvertedAppearance(True)
        self.scroll_speed_slider.setFixedWidth(200)
        self.scroll_speed_slider.valueChanged.connect(self.update_autoscroll_speed)
                
        btn_trans_down.clicked.connect(lambda: self.transpose(-1))
        btn_trans_up.clicked.connect(lambda: self.transpose(1))
        btn_zoom_out.clicked.connect(self.zoom_out)
        btn_zoom_in.clicked.connect(self.zoom_in)
        
        ctrl_layout.addWidget(btn_trans_down)
        ctrl_layout.addWidget(self.lbl_trans)
        ctrl_layout.addWidget(btn_trans_up)
        ctrl_layout.addSpacing(20)
        ctrl_layout.addWidget(btn_zoom_out)
        ctrl_layout.addWidget(btn_zoom_in)
        ctrl_layout.addSpacing(20)
        ctrl_layout.addWidget(self.btn_scroll)
        ctrl_layout.addWidget(self.lbl_scroll_speed)
        ctrl_layout.addWidget(self.scroll_speed_slider)
        ctrl_layout.addStretch()
        
        # Store for live mode toggling
        self._transpose_widgets = [btn_trans_down, self.lbl_trans, btn_trans_up,
                                    btn_zoom_out, btn_zoom_in]
        
        self.chordpro_display = QTextBrowser()
        self.chordpro_display.setOpenLinks(False)
        self.chordpro_display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chordpro_display.verticalScrollBar().valueChanged.connect(self.sync_web_scroll)
        self.chordpro_display.setStyleSheet("background-color: #1e1e1e; color: #ffffff;")
        self.chordpro_display.viewport().setMouseTracking(True)
        self.chordpro_display.viewport().installEventFilter(self)
        
        self.chord_tooltip = QLabel()
        self.chord_tooltip.setWindowFlag(Qt.WindowType.ToolTip)
        self.chord_tooltip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.chord_tooltip.hide()
        
        center_layout.addLayout(ctrl_layout)
        center_layout.addWidget(self.chordpro_display)
        splitter.addWidget(center_panel)

        # --- RIGHT PANEL (Audio & Mixer) ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        self.lbl_general_outputs = QLabel("<b>GENERAL OUTPUTS</b>")
        right_layout.addWidget(self.lbl_general_outputs)
        hw_layout = QHBoxLayout()
        self.check_midi = QCheckBox("MIDI Out"); self.check_midi.setChecked(True)
        self.check_dmx = QCheckBox("DMX Out"); self.check_dmx.setChecked(True)
        self.check_osc = QCheckBox("OSC Out"); self.check_osc.setChecked(True)
        self.check_mtc = QCheckBox("MTC Out"); self.check_mtc.setChecked(True)
        
        self.check_midi.stateChanged.connect(lambda s: self.sync_engine.set_midi_enabled(s == 2))
        self.check_dmx.stateChanged.connect(lambda s: self.sync_engine.set_dmx_enabled(s == 2))
        self.check_osc.stateChanged.connect(lambda s: self.sync_engine.set_osc_enabled(s == 2))
        self.check_mtc.stateChanged.connect(lambda s: self.sync_engine.set_mtc_enabled(s == 2))
        
        hw_layout.addWidget(self.check_midi); hw_layout.addWidget(self.check_dmx); hw_layout.addWidget(self.check_osc); hw_layout.addWidget(self.check_mtc)
        right_layout.addLayout(hw_layout)
        
        def get_ip():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(('10.255.255.255', 1))
                return s.getsockname()[0]
            except:
                return "127.0.0.1"
            finally:
                s.close()
        local_ip = get_ip()
        
        self.lbl_web_remote = QLabel(f"📱 <b>Web Remote:</b> http://msm.local:5000 &nbsp; <i>(or {local_ip}:5000)</i>")
        self.lbl_web_remote.setStyleSheet("color: #4ade80; font-size: 13px; margin-top: 5px; margin-bottom: 5px;")
        self.lbl_web_remote.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_web_remote.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right_layout.addWidget(self.lbl_web_remote)


        for label, val, func, mute_func in [
            ("MASTER", 100, self.audio_engine.set_master_volume, self.audio_engine.set_master_mute),
            ("CLICK", 50, self.sync_engine.set_click_volume, self.sync_engine.set_click_mute)
        ]:
            row_container = QWidget()
            row_container.setFixedHeight(28) # Match stems row height
            row_layout = QHBoxLayout(row_container)
            row_layout.setContentsMargins(5, 0, 5, 0) # Match stems margins
            row_layout.setSpacing(10) # Match stems spacing
            lbl = QLabel(label)
            lbl.setFixedWidth(70); lbl.setStyleSheet("font-size: 11px;") # Match stems label font size
            sld = ClickableSlider(Qt.Orientation.Horizontal)
            sld.setRange(0, 100); sld.setValue(val)
            sld.setFixedHeight(20) # Match stems slider height
            sld.valueChanged.connect(lambda v, f=func: f(v / 100.0))
            btn_mute = QPushButton("M")
            btn_mute.setCheckable(True)
            btn_mute.setFixedSize(22, 22)
            btn_mute.setStyleSheet("font-size: 10px; font-weight: bold;")
            
            if label == "CLICK":
                self.chk_click_mute = btn_mute
            
            def create_mute_handler(f_mute, chk_label, btn):
                def handler(checked):
                    f_mute(checked)
                    if checked:
                        btn.setStyleSheet("background-color: #cc0000; color: white; font-size: 10px; font-weight: bold; border-radius: 3px;")
                    else:
                        btn.setStyleSheet("font-size: 10px; font-weight: bold;")
                    
                    if chk_label == "CLICK":
                        try:
                            app_state["click_mute"] = checked
                            push_web_state()
                        except NameError: pass
                return handler

            btn_mute.toggled.connect(create_mute_handler(mute_func, label, btn_mute))
            row_layout.addWidget(lbl)
            row_layout.addWidget(sld)
            row_layout.addWidget(btn_mute)
            pan_knob = SmoothDial(theme_colors=THEMES[self.main_window.current_theme])
            pan_knob.setRange(-100, 100); pan_knob.setValue(0); pan_knob.setNotchesVisible(True); pan_knob.setFixedSize(30, 30)
            
            original_mousePressEvent = pan_knob.mousePressEvent
            def custom_mousePressEvent(event, dial=pan_knob, original=original_mousePressEvent):
                if event.button() == Qt.MouseButton.RightButton: dial.setValue(0)
                else: original(event)
            pan_knob.mousePressEvent = custom_mousePressEvent
            
            pan_label = QLabel("C"); pan_label.setFixedWidth(35); pan_label.setAlignment(Qt.AlignmentFlag.AlignCenter); pan_label.setStyleSheet("font-size: 10px; color: #888;")
            
            def create_pan_callback(channel_label, label_widget):
                def on_pan_changed(v):
                    if v == 0: label_widget.setText("C")
                    elif v < 0: label_widget.setText(f"L{abs(v)}")
                    else: label_widget.setText(f"R{v}")
                    pan_float = v / 100.0
                    if channel_label == "CLICK": self.sync_engine.set_click_pan(pan_float)
                    elif channel_label == "MASTER": self.audio_engine.set_master_pan(pan_float)
                return on_pan_changed
                
            pan_knob.valueChanged.connect(create_pan_callback(label, pan_label))
            row_layout.addWidget(pan_knob)
            row_layout.addWidget(pan_label)
            right_layout.addWidget(row_container) # Add the container widget to right_layout

        self.lbl_audio_player = QLabel("<b>AUDIO PLAYER</b>"); right_layout.addWidget(self.lbl_audio_player)
        audio_ctrl_layout = QHBoxLayout()
        btn_rw = QPushButton(); btn_rw.clicked.connect(lambda: self.audio_engine.seek(-10))
        btn_rw.setIcon(QIcon("assets/icons/rewind.svg")); btn_rw.setIconSize(QSize(20, 20))
        

        self.btn_play_pause = QPushButton(); self.btn_play_pause.clicked.connect(self.toggle_audio_playback)
        self.btn_play_pause.setIcon(QIcon("assets/icons/play.svg")); self.btn_play_pause.setIconSize(QSize(20, 20))
        
        btn_stop = QPushButton(); btn_stop.clicked.connect(self.stop_audio_playback)
        btn_stop.setIcon(QIcon("assets/icons/stop.svg")); btn_stop.setIconSize(QSize(20, 20))
        
        btn_fw = QPushButton(); btn_fw.clicked.connect(lambda: self.audio_engine.seek(10))
        btn_fw.setIcon(QIcon("assets/icons/fast-forward.svg")); btn_fw.setIconSize(QSize(20, 20))
        
        self.btn_export = QPushButton("Export")
        self.btn_export.setToolTip("Export actual mixdown with current pitch and volumes")
        self.btn_export.clicked.connect(self.export_mixdown)
        
        for btn in [btn_rw, self.btn_play_pause, btn_stop, btn_fw, self.btn_export]:
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus); btn.setFixedHeight(25); audio_ctrl_layout.addWidget(btn)
        right_layout.addLayout(audio_ctrl_layout)
        
        seek_layout = QHBoxLayout()
        self.time_label = QLabel("00:00 / 00:00")
        self.seek_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek_slider.sliderPressed.connect(self.on_seek_pressed)
        self.seek_slider.sliderMoved.connect(self.on_seek_moved)
        self.seek_slider.sliderReleased.connect(self.on_seek_released)
        seek_layout.addWidget(self.time_label); seek_layout.addWidget(self.seek_slider)
        right_layout.addLayout(seek_layout)
        
        self.waveform_viewer = WaveformWidget()
        self.waveform_viewer.seek_requested.connect(self.handle_waveform_seek)
        self.waveform_viewer.loop_requested.connect(self.audio_engine.set_loop)
        self.waveform_viewer.loop_cleared.connect(self.audio_engine.clear_loop)
        self.waveform_viewer.marker_added.connect(self.add_song_marker)
        self.waveform_viewer.marker_moved.connect(self.handle_marker_moved)
        self.waveform_viewer.marker_deleted.connect(self.handle_marker_deleted)
        right_layout.addWidget(self.waveform_viewer)
        
        seq_layout = QHBoxLayout()
        self.btn_seq_toggle = QPushButton("SEQ OFF"); self.btn_seq_toggle.setCheckable(True); self.btn_seq_toggle.setStyleSheet("background-color: #444; color: white;")
        self.btn_seq_toggle.toggled.connect(self.toggle_sequencer)
        self.btn_seq_edit = QPushButton("⚙️ Edit"); self.btn_seq_edit.clicked.connect(self.open_sequencer_dialog)
        self.btn_seq_toggle.setFixedHeight(25); self.btn_seq_edit.setFixedHeight(25)
        seq_layout.addWidget(QLabel("MIDI Sequencer")); seq_layout.addWidget(self.btn_seq_toggle); seq_layout.addWidget(self.btn_seq_edit)
        right_layout.addLayout(seq_layout)
    
        stems_header_layout = QHBoxLayout()
        self.lbl_stems_mixer = QLabel("<b>STEMS MIXER</b>")
        stems_header_layout.addWidget(self.lbl_stems_mixer)
        stems_header_layout.addStretch()
        
        self.btn_pitch_down = QPushButton("-")
        self.btn_pitch_down.setFixedSize(25, 25)
        self.btn_pitch_down.clicked.connect(lambda: self.change_stems_pitch(self.audio_engine.pitch_semitones - 1))
                
        self.lbl_pitch_val = QLabel("0")
        self.lbl_pitch_val.setStyleSheet("font-weight: bold; color: #ff9900; min-width: 30px;")
        self.lbl_pitch_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.btn_pitch_up = QPushButton("+")
        self.btn_pitch_up.setFixedSize(25, 25)
        self.btn_pitch_up.clicked.connect(lambda: self.change_stems_pitch(self.audio_engine.pitch_semitones + 1))
        
        stems_header_layout.addWidget(QLabel("Pitch:"))
        stems_header_layout.addWidget(self.btn_pitch_down)
        stems_header_layout.addWidget(self.lbl_pitch_val)
        stems_header_layout.addWidget(self.btn_pitch_up)
        
        stems_header_layout.addSpacing(15)
        
        self.btn_speed_down = QPushButton("-")
        self.btn_speed_down.setFixedSize(25, 25)
        self.btn_speed_down.clicked.connect(lambda: self.change_stems_speed_bpm(self.get_current_speed_bpm() - 1))
                
        self.lbl_speed_val = QLabel("120 BPM")
        self.lbl_speed_val.setStyleSheet("font-weight: bold; color: #ff9900; min-width: 60px;")
        self.lbl_speed_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.btn_speed_up = QPushButton("+")
        self.btn_speed_up.setFixedSize(25, 25)
        self.btn_speed_up.clicked.connect(lambda: self.change_stems_speed_bpm(self.get_current_speed_bpm() + 1))
        
        self.btn_speed_reset = QPushButton("↺")
        self.btn_speed_reset.setFixedSize(25, 25)
        self.btn_speed_reset.clicked.connect(self.reset_stems_speed_bpm)
        self.btn_speed_reset.setToolTip("Reset to Original BPM")
        
        stems_header_layout.addWidget(QLabel("Speed:"))
        stems_header_layout.addWidget(self.btn_speed_down)
        stems_header_layout.addWidget(self.lbl_speed_val)
        stems_header_layout.addWidget(self.btn_speed_up)
        stems_header_layout.addWidget(self.btn_speed_reset)
        
        right_layout.addLayout(stems_header_layout)
        self.stems_widget = QWidget(); self.stems_layout = QVBoxLayout(self.stems_widget); self.stems_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.stems_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.stems_widget.customContextMenuRequested.connect(self.show_stems_context_menu)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(self.stems_widget)
        right_layout.addWidget(scroll)

        # --- MOTE GROUPS TOGGLE ---
        self.btn_toggle_mute_groups = QPushButton("HIDE MUTE GROUPS")
        self.btn_toggle_mute_groups.setCheckable(True)
        self.btn_toggle_mute_groups.setChecked(True) # Visible by default
        self.btn_toggle_mute_groups.setFixedHeight(25)
        self.btn_toggle_mute_groups.setStyleSheet("font-weight: bold; color: #888;")
        self.btn_toggle_mute_groups.clicked.connect(self.toggle_mute_groups_visibility)
        right_layout.addWidget(self.btn_toggle_mute_groups)

        self.mute_groups_container = QWidget()
        mg_cont_layout = QVBoxLayout(self.mute_groups_container)
        mg_cont_layout.setContentsMargins(0, 0, 0, 0)
        mg_cont_layout.setSpacing(2)

        self.lbl_mute_groups_mixer = QLabel("<b>MUTE GROUPS MIXER</b>")
        mg_cont_layout.addWidget(self.lbl_mute_groups_mixer)
        
        mute_groups_layout = QGridLayout()
        mute_groups_layout.setSpacing(2)
        self.mg_buttons = []
        for i in range(1, 5):
            btn = QPushButton(f"Mute Group {i}"); btn.setCheckable(True); btn.setFixedHeight(22)
            btn.toggled.connect(lambda checked, gid=i, b=btn: self.on_mute_group_toggled(gid, checked, b))
            self.mg_buttons.append(btn); mute_groups_layout.addWidget(btn, (i-1)//2, (i-1)%2)
        
        self.fx_buttons = []
        for i in range(1, 5):
            btn = QPushButton(f"Mute FX {i}"); btn.setCheckable(True); btn.setFixedHeight(22)
            btn.toggled.connect(lambda checked, gid=i, b=btn: self.on_fx_mute_toggled(gid, checked, b))
            self.fx_buttons.append(btn); mute_groups_layout.addWidget(btn, ((i-1)//2)+2, (i-1)%2)
        
        mg_cont_layout.addLayout(mute_groups_layout)
        right_layout.addWidget(self.mute_groups_container)
        self.mute_groups_container.setVisible(True) # Visible by default

        splitter.addWidget(right_panel)
        splitter.setSizes([380, 670, 350])
        main_layout.addWidget(splitter)
        
        # Complex Bottom Layout: Stacked BPM/MTC on left, QR on right
        bottom_row_layout = QHBoxLayout()
        bottom_row_layout.setContentsMargins(5, 0, 5, 0)
        bottom_row_layout.setSpacing(5)
        
        left_stack = QVBoxLayout()
        left_stack.setSpacing(0) # Flush stacking
        
        # BPM Bar
        self.bpm_bar = QLabel("BPM: ---")
        self.bpm_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bpm_bar.setFixedHeight(60)
        self.bpm_bar.setStyleSheet("background-color: #333; color: white; font-size: 24px; font-weight: bold; border-top-left-radius: 4px; border-bottom-left-radius: 0px;")
        
        # MTC Bar
        self.mtc_bar = QLabel("MTC: 00:00:00:00")
        self.mtc_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mtc_bar.setFixedHeight(30)
        self.mtc_bar.setStyleSheet("background-color: #222; color: #ff9900; font-family: monospace; font-size: 18px; font-weight: bold; border-top-left-radius: 0px; border-bottom-left-radius: 4px;")
        
        left_stack.addWidget(self.bpm_bar)
        left_stack.addWidget(self.mtc_bar)
        
        bottom_row_layout.addLayout(left_stack, 1) # Stretch to take full width

        # QR Code (Spans both heights)
        self.qr_code_label = QLabel()
        self.qr_code_label.setFixedSize(90, 90) # Sum of 60 + 30
        self.qr_code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_code_label.setStyleSheet("background-color: #333; border-top-right-radius: 4px; border-bottom-right-radius: 4px;")
        bottom_row_layout.addWidget(self.qr_code_label)
        
        main_layout.addLayout(bottom_row_layout)

        # Call QR code update after UI is built
        self._update_qr_code()

    def _update_qr_code(self):
        web_url = f"http://{self._get_local_ip()}:5000"
        
        # Get theme colors
        theme_name = getattr(self.main_window, 'current_theme', 'Modern Dark')
        colors = THEMES.get(theme_name, THEMES.get("Modern Dark"))
        
        bg_color = colors.get('card', '#333')
        fg_color = colors.get('accent', '#ff9900')
        
        qr = qrcode.QRCode(border=2)
        qr.add_data(web_url)
        qr.make(fit=True)
        
        # Generate with theme colors
        qr_img = qr.make_image(fill_color=fg_color, back_color=bg_color)
        pil_img = qr_img.convert("RGB")
        
        width, height = pil_img.size
        bytes_per_line = 3 * width
        q_img = QImage(pil_img.tobytes("raw", "RGB"), width, height, bytes_per_line, QImage.Format.Format_RGB888)
        self.qr_code_label.setPixmap(QPixmap.fromImage(q_img).scaled(self.qr_code_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    # --- LOGIC METHODS ---

    def load_initial_data(self):
        csv_files = self.data_manager.get_all_csv_files()
        self.combo_setlists.addItems(csv_files)
        if self.current_csv in csv_files:
            self.combo_setlists.setCurrentText(self.current_csv)
        self.load_setlist_from_combo(self.combo_setlists.currentText())

    def load_setlist_from_combo(self, text):
        if not text: return
        self.current_csv = text
        self.setlist_widget.clear()
        songs = self.data_manager.load_setlist(text)
        for song in songs: self.add_song_to_list(song)
        self.update_total_setlist_time()
        self._sync_optimizer_current_setlist()

    def add_song_to_list(self, song_title):
        song_data = self.data_manager.database.get(song_title, {})
        item = QTreeWidgetItem([song_title, str(song_data.get("Notes", ""))])
        item.setData(0, Qt.ItemDataRole.UserRole, song_title)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
        self.setlist_widget.addTopLevelItem(item)

    def update_total_setlist_time(self):
        """Calculates and displays the sum of all durations in the current setlist."""
        total_sec = 0
        for i in range(self.setlist_widget.topLevelItemCount()):
            song_id = self.setlist_widget.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            song_data = self.data_manager.database.get(song_id, {})
            total_sec += float(song_data.get("Duration", 0))
        
        m, s = divmod(int(total_sec), 60)
        h, m = divmod(m, 60)
        time_str = f"Total: {h:02d}:{m:02d}:{s:02d}" if h > 0 else f"Total: {m:02d}:{s:02d}"
        self.lbl_total_time.setText(time_str)

    def on_song_selected(self, item):
        if not item: return
        # Restore: Use UserRole to get the actual database key/ID
        self.current_song = item.data(0, Qt.ItemDataRole.UserRole)
        song_title = item.text(0)
        logger.info(f"Setlist: Selecting song '{song_title}' (ID: {self.current_song})")
        song_data = self.data_manager.database.get(self.current_song, {})
        
        # Lyrics Transpose
        self.transpose_steps = song_data.get("Transpose", 0)
        self.lbl_trans.setText(f"Key: {self.transpose_steps}")
        
        self.update_chordpro_view()
        
        app_state["current_song"] = self.current_song
        
        # Determine next song for remote interfaces
        next_item = self.setlist_widget.itemBelow(item)
        if next_item:
            app_state["next_song"] = next_item.text(0)
        else:
            app_state["next_song"] = "END OF SETLIST"
            
        push_web_state()
        
        # Mixer Snapshot
        snapshot = song_data.get("Snapshot", "")
        if str(snapshot).isdigit(): 
            self.sync_engine.load_mixer_snapshot(int(snapshot))

        # Base BPM and Time Sig
        base_bpm = float(song_data.get("BPM", 120))
        time_sig = song_data.get("Time Sig", "4/4") 
        self.sync_engine.set_time_signature(time_sig)
        
        # Auto-Scroll Speed
        saved_speed = song_data.get("ScrollSpeed", 50)
        self.scroll_speed_slider.blockSignals(True)
        self.scroll_speed_slider.setValue(saved_speed)
        self.autoscroll_interval = saved_speed
        self.scroll_speed_slider.blockSignals(False)
        
        # --- STEMS PITCH & SPEED LOADING ---
        stems_pitch = song_data.get("StemsPitch", 0)
        self.audio_engine.set_pitch_shift(float(stems_pitch))
        
        target_bpm = float(song_data.get("StemsTargetBPM", base_bpm))
        time_ratio = base_bpm / target_bpm if target_bpm > 0 else 1.0
        self.audio_engine.set_speed_ratio(time_ratio)
        
        if hasattr(self, 'lbl_pitch_val'):
            self.lbl_pitch_val.setText(f"{int(float(stems_pitch)):+}")
            
        if hasattr(self, 'lbl_speed_val'):
            self.lbl_speed_val.setText(f"{int(round(target_bpm))} BPM")
            
        # Apply target BPM to Sync Engine (Metronome follows speed curve)
        self.bpm_bar.setText(f"BPM: {int(round(target_bpm))}")
        self.sync_engine.set_bpm(target_bpm)
        # ---------------------------

        if self.check_midi.isChecked():
            actions = self.data_manager.get_resolved_patch(self.current_song)
            self.sync_engine.execute_patch(actions)
        
        self.sync_engine.midi_actions = song_data.get("MidiActions", [])
        
        was_playing = self.sync_engine.is_playing
        if was_playing:
            self.sync_engine.stop()
            # Explicitly reset button state so the restart timer can fire
            self.btn_sync.setChecked(False)
            self.retranslate_ui(TRANSLATIONS.get(self.main_window.current_language, TRANSLATIONS["English"]))

        self.sync_engine.reset_mtc() 
        self.mtc_bar.setText("MTC: 00:00:00:00")

        if was_playing:
            sound_settings = self.data_manager.config.get("sound_settings", {"transition_delay": 2.0})
            delay_ms = int(sound_settings.get("transition_delay", 2.0) * 1000)
            QTimer.singleShot(delay_ms, self.main_window.restart_metronome_after_delay)
            
        self.stop_audio_playback()
        # Immediate mixer creation using fast folder scan
        self.audio_engine.scan_song_stems(self.current_song)
        self.build_stems_mixer()
        
        if self.tick_10s_timer.isActive(): self.tick_10s_timer.stop()
        if self.chk_10s_tick.isChecked():
            if self.chk_click_mute.isChecked(): self.chk_click_mute.setChecked(False)
            is_sync_running = getattr(self.sync_engine, 'is_playing', False)
            is_audio_running = getattr(self.audio_engine, 'is_playing', False)
            if is_sync_running or is_audio_running: 
                self.start_tick_disable_timer()
        
        # Ensure visual timer is active if sync starts
        if not self.visual_sync_timer.isActive():
            self.visual_sync_timer.start(16) # ~60 FPS
        
        self.update_waveform()

        # Load Markers (After update_waveform to avoid reset)
        self.waveform_viewer.markers = song_data.get("Markers", [])
        self.waveform_viewer.update()
        
        self.refresh_autoscroll_timer()

    def update_chordpro_view(self):
        if not self.current_song: return
        html = self.data_manager.parse_chordpro(self.current_song, self.transpose_steps, self.chord_font_size)
        self.chordpro_display.setHtml(html)
        
        web_html = self.data_manager.parse_chordpro_web(self.current_song, self.transpose_steps)
        
        try:
            from confidence_server import update_monitor_lyrics
            update_monitor_lyrics(self.current_song, web_html)
        except Exception as e:
            pass
            
        if web_bridge:
            try:
                push_web_state()
            except Exception: pass

    # --- Mouse Tracking & Hover System ---
    def eventFilter(self, source, event):
        try:
            # Evaluating viewport() can raise RuntimeError if the widget is deleted
            if not self.chordpro_display or source is not self.chordpro_display.viewport():
                return super().eventFilter(source, event)
        except RuntimeError:
            return super().eventFilter(source, event)

        if event.type() == QEvent.Type.MouseMove:
            anchor = self.chordpro_display.anchorAt(event.pos())
            if anchor.startswith("chord:"):
                chord_name = anchor.split(":")[1]
                fingering = GUITAR_CHORDS.get(chord_name)
                
                if fingering:
                    pixmap = create_chord_pixmap(chord_name, fingering)
                    self.chord_tooltip.setPixmap(pixmap)
                    
                    global_pos = self.chordpro_display.viewport().mapToGlobal(event.pos())
                    self.chord_tooltip.move(global_pos.x() + 15, global_pos.y() + 15)
                    self.chord_tooltip.show()
                else:
                    self.chord_tooltip.hide()
            else:
                self.chord_tooltip.hide()
            
        elif source == self.chordpro_display.viewport() and event.type() == QEvent.Type.Leave:
            self.chord_tooltip.hide()
            
        return super().eventFilter(source, event)

    def select_song(self, item, col):
        title = item.text(0)
        self.load_song(title)

    def transpose(self, step):
        if not self.current_song: return
        old_val = int(self.transpose_steps)
        new_val = old_val + step
        cmd = TransposeCommand(self, self.current_song, old_val, new_val)
        self.undo_stack.push(cmd)

    def zoom_in(self): self.chord_font_size += 2; self.update_chordpro_view()
    def zoom_out(self): self.chord_font_size = max(8, self.chord_font_size - 2); self.update_chordpro_view()

    def toggle_sync(self):
        if self.btn_sync.isChecked():
            self.btn_sync.setText("STOP METRONOME")
            #self.btn_sync.setStyleSheet("background-color: #cc0000; color: white; height: 28px; font-weight: bold;")
            self.sync_engine.start(with_delay=True)
            self.visual_sync_timer.start(16)
            self.refresh_autoscroll_timer()
            if app_state: app_state["is_playing"] = True; push_web_state()
        else:
            self.btn_sync.setText("START METRONOME")
            #self.btn_sync.setStyleSheet("background-color: #ff9900; color: black; height: 28px; font-weight: bold;")
            self.sync_engine.stop()
            self.refresh_autoscroll_timer()
            if app_state: app_state["is_playing"] = False; push_web_state()
        if self.chk_10s_tick.isChecked():
            if self.chk_click_mute.isChecked(): self.chk_click_mute.setChecked(False)

    def handle_sync_tick(self, is_downbeat):
        """Handles non-visual or non-latency-critical beat tasks."""
        try:
            from web_server import push_beat
            push_beat(is_downbeat)
        except Exception:
            pass

    def update_visual_sync(self):
        """Predictive visual metronome update (matches remote logic and avoids jitter)."""
        if not self.sync_engine.is_playing or not self.sync_engine.link:
            if getattr(self, '_last_blink_state', None) != "idle":
                self._last_blink_state = "idle"
                self._update_bpm_bar_style("#333")
            return

        # Get current beat phase from Ableton Link
        curr_beat = self.sync_engine.link.beat
        phase = curr_beat % 1.0
        
        # Threshold for the visual flash (30% of a beat)
        flash_threshold = 0.3
        is_downbeat = (int(curr_beat) % self.sync_engine.beats_per_bar == 0)
        
        state = "off"
        if phase < flash_threshold:
            state = "downbeat" if is_downbeat else "beat"
            
        if getattr(self, '_last_blink_state', None) != state:
            self._last_blink_state = state
            s = self.sync_engine.dmx_settings
            
            if state == "downbeat":
                color = f"rgb({s.get('accent_r', 255)}, {s.get('accent_g', 0)}, {s.get('accent_b', 0)})"
            elif state == "beat":
                color = f"rgb({s.get('std_r', 0)}, {s.get('std_g', 255)}, {s.get('std_b', 0)})"
            else:
                color = "#2d2d2d"
                
            self._update_bpm_bar_style(color)

    def _update_bpm_bar_style(self, bg_color):
        # Use consistent style properties to avoid layout jumps/recalculations
        style = (f"background-color: {bg_color}; color: white; "
                 "font-size: 24px; font-weight: bold; border-top-left-radius: 4px;")
        self.bpm_bar.setStyleSheet(style)

    def refresh_current_song_state(self):
        """Reloads the current song data from the database into the engines and UI."""
        item = self.setlist_widget.currentItem()
        if item:
            self.on_song_selected(item)

    def auto_mute_click(self):
        if hasattr(self, 'chk_click_mute'): self.chk_click_mute.setChecked(True)
            
    def start_tick_disable_timer(self):
        if self.chk_10s_tick.isChecked():
            sound_settings = self.data_manager.config.get("sound_settings", {})
            duration = sound_settings.get("tick_disable_duration", 10.0)
            self.tick_10s_timer.start(int(duration * 1000))

    def toggle_audio_playback(self):
        if not self.current_song: return
        
        # Lazy loading logic: if audio isn't decoded yet, load it now
        if not self.audio_engine.is_loaded:
            self.btn_play_pause.setEnabled(False)
            self.loader_worker = SongLoaderWorker(self.audio_engine, self.current_song)
            self.loader_worker.finished.connect(self.on_song_loaded_and_play)
            self.loader_worker.start()
            return
            
        self.toggle_playback()

    def on_song_loaded_and_play(self, success):
        self.btn_play_pause.setEnabled(True)
        if success:
            logger.info(f"Setlist: Loaded '{self.current_song}' ({self.audio_engine.get_duration():.1f}s)")
            # Refresh mixer connections with real decoded objects
            self.build_stems_mixer()
            self.start_audio_playback()
        else:
            logger.error(f"Setlist: Failed to load '{self.current_song}'")
            QMessageBox.warning(self, "Load Error", "Failed to load audio for this song.")

    def on_song_loaded(self, success):
        self.btn_play_pause.setEnabled(True)
        if not success: return
        self.build_stems_mixer()

    def stop_audio_playback(self):
        logger.info("Setlist: STOPPING playback")
        self.audio_engine.stop()
        self.btn_play_pause.setIcon(QIcon("assets/icons/play.svg"))
        self.seek_slider.setValue(0)
        self.update_time_label(0, self.audio_engine.get_duration())
        if hasattr(self, 'waveform_viewer'):
            self.waveform_viewer.set_progress(0.0)
        self.refresh_autoscroll_timer()

    def start_audio_playback(self):
        self.audio_engine.play()
        self.btn_play_pause.setIcon(QIcon("assets/icons/pause.svg"))
        self.refresh_autoscroll_timer()

    def pause_audio_playback(self):
        self.audio_engine.pause()
        self.btn_play_pause.setIcon(QIcon("assets/icons/play.svg"))
        self.refresh_autoscroll_timer()

    def toggle_playback(self):
        if self.audio_engine.is_playing:
            logger.info("Setlist: PAUSING playback")
            self.pause_audio_playback()
        else:
            logger.info("Setlist: STARTING playback")
            self.start_audio_playback()

    def toggle_autoscroll(self):
        self.is_autoscrolling = not self.is_autoscrolling
        logger.info(f"Setlist: Autoscroll {'ARMED' if self.is_autoscrolling else 'DISARMED'}")
        self.btn_scroll.setText("Auto-Scroll: ON" if self.is_autoscrolling else "Auto-Scroll: OFF")
        self.refresh_autoscroll_timer()

    def refresh_autoscroll_timer(self):
        """Starts/Stops the actual timer based on ARMED state and PLAYBACK state."""
        armed = self.is_autoscrolling
        # Music is truly "moving" if audio engine is playing or MTC has started (post-countdown)
        music_moving = self.audio_engine.is_playing or self.sync_engine.mtc_active
        
        if armed and music_moving:
            if not self.scroll_timer.isActive():
                self.scroll_timer.start(self.autoscroll_interval)
        else:
            self.scroll_timer.stop()

    def update_autoscroll_speed(self, value):
        self.autoscroll_interval = value
        self.refresh_autoscroll_timer()
        self.save_speed_timer.start(500)
            
    def save_autoscroll_speed(self):
        if self.current_song:
            song_data = self.data_manager.database.get(self.current_song, {})
            song_data["ScrollSpeed"] = self.scroll_speed_slider.value()
            self.data_manager.database[self.current_song] = song_data
            self.data_manager.save_database()

    def auto_scroll_step(self):
        sb = self.chordpro_display.verticalScrollBar()
        if sb.value() < sb.maximum(): sb.setValue(sb.value() + 1)
        else: self.toggle_autoscroll(); self.btn_scroll.setChecked(False)
        
    def do_smooth_scroll(self, direction):
        scrollbar = self.chordpro_display.verticalScrollBar()
        current_val = scrollbar.value()
        step = int(scrollbar.pageStep() * 0.6) 
        if direction == "DOWN": target_val = min(current_val + step, scrollbar.maximum())
        elif direction == "UP": target_val = max(current_val - step, scrollbar.minimum())
        else: return
        self.scroll_animation.stop()
        self.scroll_animation.setStartValue(current_val)
        self.scroll_animation.setEndValue(target_val)
        self.scroll_animation.start()

    def handle_pedal_input(self, command):
        if command == "NEXT_SONG":
            curr = self.setlist_widget.currentItem()
            idx = self.setlist_widget.indexOfTopLevelItem(curr) if curr else -1
            if idx < self.setlist_widget.topLevelItemCount() - 1:
                next_item = self.setlist_widget.topLevelItem(idx + 1)
                self.setlist_widget.setCurrentItem(next_item)
                self.on_song_selected(next_item)
        elif command == "PREV_SONG":
            curr = self.setlist_widget.currentItem()
            idx = self.setlist_widget.indexOfTopLevelItem(curr) if curr else -1
            if idx > 0:
                prev_item = self.setlist_widget.topLevelItem(idx - 1)
                self.setlist_widget.setCurrentItem(prev_item)
                self.on_song_selected(prev_item)
        elif command == "SCROLL_DOWN": self.do_smooth_scroll("DOWN")
        elif command == "SCROLL_UP": self.do_smooth_scroll("UP")

    def build_stems_mixer(self):
        while self.stems_layout.count():
            child = self.stems_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()
            
        self.stems_layout.setSpacing(0)
        self.stems_layout.setContentsMargins(0, 0, 0, 0)

        for name in self.audio_engine.stems_data.keys():
            row = QWidget()
            row.setFixedHeight(28) # Compact vertical dimension
            lay = QHBoxLayout(row)
            lay.setContentsMargins(5, 0, 5, 0)
            lay.setSpacing(10)
            
            lbl = QLabel(name[:12])
            lbl.setFixedWidth(70)
            lbl.setStyleSheet("font-size: 11px;")
            lay.addWidget(lbl)
            
            sld = ClickableSlider(Qt.Orientation.Horizontal)
            sld.setRange(0, 100)
            sld.setValue(100)
            sld.setFixedHeight(20)
            sld.valueChanged.connect(lambda v, n=name: self.audio_engine.set_volume(n, v / 100.0))
            
            # Pro Feature: Individual Stem Pan
            pan_knob = SmoothDial(theme_colors=THEMES[self.main_window.current_theme])
            pan_knob.setRange(-100, 100)
            pan_knob.setValue(0)
            pan_knob.setFixedSize(24, 24)
            pan_knob.setToolTip(f"Pan: {name}")
            
            # Reset pan on Right Click
            pan_knob.mousePressEvent = lambda e, p=pan_knob: p.setValue(0) if e.button() == Qt.MouseButton.RightButton else SmoothDial.mousePressEvent(p, e)
            
            pan_knob.valueChanged.connect(lambda v, n=name: self.audio_engine.set_pan(n, v / 100.0))
            
            btn_mute = QPushButton("M")
            btn_mute.setCheckable(True)
            btn_mute.setFixedSize(22, 22)
            btn_mute.setStyleSheet("font-size: 10px; font-weight: bold;")

            def handle_mute(checked, n=name, b=btn_mute):
                self.audio_engine.set_mute(n, checked)
                if checked:
                    b.setStyleSheet("background-color: #cc0000; color: white; font-size: 10px; font-weight: bold; border-radius: 3px;")
                else:
                    b.setStyleSheet("font-size: 10px; font-weight: bold;")

            btn_mute.toggled.connect(handle_mute)

            btn_solo = QPushButton("S")
            btn_solo.setCheckable(True)
            btn_solo.setFixedSize(22, 22)
            btn_solo.setStyleSheet("font-size: 10px; font-weight: bold;")
            
            def handle_solo(checked, n=name, b=btn_solo):
                self.audio_engine.set_solo(n, checked)
                if checked:
                    b.setStyleSheet("background-color: #f1c40f; color: black; font-size: 10px; font-weight: bold; border-radius: 3px;")
                else:
                    b.setStyleSheet("font-size: 10px; font-weight: bold;")
            
            btn_solo.toggled.connect(handle_solo)

            lay.addWidget(sld)
            lay.addWidget(pan_knob)
            lay.addWidget(btn_mute)
            lay.addWidget(btn_solo)
            self.stems_layout.addWidget(row)
            
    def toggle_mute_groups_visibility(self):
        is_visible = self.btn_toggle_mute_groups.isChecked()
        self.mute_groups_container.setVisible(is_visible)
        self.btn_toggle_mute_groups.setText("HIDE MUTE GROUPS" if is_visible else "SHOW MUTE GROUPS")

    def on_mute_group_toggled(self, gid, muted, btn):
        btn.setStyleSheet(f"background-color: {'#cc0000' if muted else '#444'}; color: white;")
        self.sync_engine.toggle_mute_group(gid, muted)
        app_state.setdefault("mutes", {})[f"mg{gid}"] = muted
        push_web_state() 

    def on_fx_mute_toggled(self, gid, muted, btn):
        btn.setStyleSheet(f"background-color: {'#cc0000' if muted else '#444'}; color: white;")
        self.sync_engine.toggle_fx_mute(gid, muted)
        app_state.setdefault("mutes", {})[f"fx{gid}"] = muted
        push_web_state()  

    def handle_web_command(self, cmd, args):
        if cmd == "play": self.toggle_audio_playback()
        elif cmd == "stop": self.stop_audio_playback()
        elif cmd == "next": self.handle_pedal_input("NEXT_SONG")
        elif cmd == "toggle_mute":
            btn_type = str(args) if args else ""
            try:
                if btn_type.startswith("mg"):
                    idx = int(btn_type.replace("mg", "")) - 1
                    if 0 <= idx < len(self.mg_buttons): self.mg_buttons[idx].click() 
                elif btn_type.startswith("fx"):
                    idx = int(btn_type.replace("fx", "")) - 1
                    if 0 <= idx < len(self.fx_buttons): self.fx_buttons[idx].click()
            except Exception as e: logger.error(f"Web command error: {e}")
        elif cmd == "toggle_click":
            self.chk_click_mute.click()
            app_state["click_mute"] = not app_state.get("click_mute", False)
            push_web_state()
        elif cmd == 'toggle_metronome':
            self.toggle_metronome_from_web()
            
    def toggle_metronome_from_web(self):
        if self.sync_engine.is_playing: 
            self.sync_engine.stop()
            if app_state: app_state["is_playing"] = False
        else: 
            self.sync_engine.start(with_delay=True)
            self.refresh_autoscroll_timer()
            if app_state: app_state["is_playing"] = True
        push_web_state()

    def sync_web_scroll(self, val):
        if getattr(self, 'is_remote_scrolling', False): return
        max_v = self.chordpro_display.verticalScrollBar().maximum()
        if max_v > 0: 
            app_state["scroll_pct"] = val / max_v
            update_monitor_scroll(app_state["scroll_pct"])
            push_web_state()

    def update_web_link_state(self, bpm, ref_time, ref_beat):
        if not app_state: return
        app_state["link_bpm"] = bpm
        app_state["link_ref_time"] = ref_time
        app_state["link_ref_beat"] = ref_beat
        if hasattr(self, 'bpm_bar'):
            self.bpm_bar.setText(f"BPM: {int(round(bpm))}")
        push_web_state()

    def update_jitter_display(self, error_ms):
        self.jitter_label.setText(f"Err: {error_ms:+.3f}ms")
        self.jitter_label.setStyleSheet(f"color: {'#ff4444' if abs(error_ms) > 4 else '#44ff44'}; font-family: monospace;  font-size: 10px;")
        
    def update_playback_ui(self):
        self.mtc_bar.setText(f"MTC: {self.sync_engine.get_mtc_string()}")
        if not self.audio_engine.stems_data or self.is_user_seeking: return
        dur, curr = self.audio_engine.get_duration(), self.audio_engine.get_current_time()
        if dur > 0:
            self.seek_slider.setRange(0, int(dur)); self.seek_slider.setValue(int(curr))
            self.update_time_label(curr, dur)
            self.waveform_viewer.set_duration(dur)
            self.waveform_viewer.set_progress(curr / dur)
        self.btn_play_pause.setIcon(QIcon(f"assets/icons/{'pause.svg' if self.audio_engine.is_playing else 'play.svg'}"))
        
        # Sync playing state to web
        if app_state:
            active = self.sync_engine.is_playing or self.audio_engine.is_playing
            if app_state.get("is_playing") != active:
                app_state["is_playing"] = active
                push_web_state()

    def update_time_label(self, current, total):
        cm, cs = divmod(int(current), 60); tm, ts = divmod(int(total), 60)
        self.time_label.setText(f"{cm:02d}:{cs:02d} / {tm:02d}:{ts:02d}")

    def on_seek_pressed(self): self.is_user_seeking = True
    def on_seek_moved(self, pos): self.update_time_label(pos, self.audio_engine.get_duration())
    def on_seek_released(self): self.audio_engine.set_position(self.seek_slider.value()); self.is_user_seeking = False

    def toggle_sequencer(self, checked):
        if checked:
            self.btn_seq_toggle.setText("SEQ ON"); self.btn_seq_toggle.setStyleSheet("background-color: #44ff44; color: black; font-weight: bold;")
            self.sync_engine.sequencer_enabled = True 
        else:
            self.btn_seq_toggle.setText("SEQ OFF"); self.btn_seq_toggle.setStyleSheet("background-color: #444; color: white;")
            self.sync_engine.sequencer_enabled = False

    def open_sequencer_dialog(self):
        from dialogs import StepSequencerDialog
        if not self.current_song: return
        song_data = self.data_manager.database.get(self.current_song, {})
        dialog = StepSequencerDialog(sequence_data=song_data.get("midi_sequence", None), parent=self.main_window)
        if dialog.exec():
            new_seq = dialog.get_sequence_data()
            song_data["midi_sequence"] = new_seq
            self.data_manager.database[self.current_song] = song_data
            self.data_manager.save_database()
            self.sync_engine.current_sequence = new_seq
            
    def show_song_context_menu(self, position):
        item = self.setlist_widget.itemAt(position)
        if not item: return
        song_title = item.data(0, Qt.ItemDataRole.UserRole)
        
        menu = QMenu()
        calc_dur_action = menu.addAction(f"Calculate Duration for '{song_title}'")
        calc_all_dur_action = menu.addAction("Calculate ALL Durations in Setlist")
        menu.addSeparator()
        calc_bpm_action = menu.addAction(f"Calculate BPM for '{song_title}'")
        calc_scroll_action = menu.addAction(f"Calculate Scroll Speed for '{song_title}'")
        calc_all_scroll_action = menu.addAction("Calculate ALL Scroll Speeds in Setlist")
        
        selected_action = menu.exec(self.setlist_widget.viewport().mapToGlobal(position))
        
        if selected_action == calc_dur_action:
            self.calculate_song_duration(song_title)
        elif selected_action == calc_all_dur_action:
            self.calculate_all_durations()
        elif selected_action == calc_bpm_action:
            self.calculate_bpm(song_title)
        elif selected_action == calc_scroll_action:
            self.calculate_autoscroll_speed(song_title)
        elif selected_action == calc_all_scroll_action:
            self.calculate_all_autoscroll_speeds()

    def show_stems_context_menu(self, position):
        # Removed "Generate BPM Click Stem" action as it is poorly working
        pass
            
    def calculate_song_duration(self, song_title, silent=False):
        """Measures the actual length of audio files on disk and saves to DB."""
        song_data = self.data_manager.database.get(song_title, {})
        duration = 0
        
        # If it's the current song and loaded, use actual duration from engine
        if self.current_song == song_title and self.audio_engine.is_loaded:
            duration = self.audio_engine.get_duration()
        else:
            # FALLBACK: Scan disk for files and measure them
            songs_base_dir = os.path.join("music", "Songs")
            song_dir = find_file_case_insensitive(songs_base_dir, song_title)
            if song_dir and os.path.isdir(song_dir):
                all_audio = [os.path.join(song_dir, f) for f in os.listdir(song_dir) 
                             if f.lower().endswith(('.wav', '.mp3', '.ogg', '.flac'))]
                if all_audio:
                    max_d = 0
                    for filepath in all_audio:
                        try:
                            with sf.SoundFile(filepath) as f:
                                d = f.frames / f.samplerate
                                if d > max_d: max_d = d
                        except: continue
                    duration = round(max_d)
        
        if duration > 0:
            song_data["Duration"] = duration
            self.data_manager.database[song_title] = song_data
            self.data_manager.save_database()
            self.update_total_setlist_time()
            if not silent:
                m, s = divmod(int(duration), 60)
                QMessageBox.information(self, "Success", f"Duration for '{song_title}' saved: {m:02d}:{s:02d}")
            return True
        else:
            if not silent:
                QMessageBox.warning(self, "Error", f"No audio found to measure duration for '{song_title}'")
            return False

    def calculate_all_durations(self):
        """Iterates through the current setlist and updates all song durations."""
        count = self.setlist_widget.topLevelItemCount()
        if count == 0: return
        
        progress = QProgressDialog("Calculating song durations...", "Cancel", 0, count, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        
        success_count = 0
        for i in range(count):
            if progress.wasCanceled(): break
            item = self.setlist_widget.topLevelItem(i)
            song_title = item.data(0, Qt.ItemDataRole.UserRole)
            progress.setLabelText(f"Calculating: {song_title}")
            if self.calculate_song_duration(song_title, silent=True):
                success_count += 1
            progress.setValue(i + 1)
            
        progress.setValue(count)
        self.update_total_setlist_time()
        QMessageBox.information(self, "Complete", f"Calculated durations for {success_count} songs.")

    def calculate_autoscroll_speed(self, song_title, silent=False):
        """
        Calculates the speed required to scroll through the song perfectly.
        Speed is the tick interval (ms) for a 1-pixel step.
        """
        song_data = self.data_manager.database.get(song_title, {})
        
        # 1. Get Duration
        duration = 0
        # If it's the current song and loaded, use actual duration
        if self.current_song == song_title and self.audio_engine.is_loaded:
            duration = self.audio_engine.get_duration()
        else:
            # Check DB
            duration = float(song_data.get("Duration", 0))
            if duration <= 0:
                # NEW FALLBACK: Scan disk for files and measure them
                songs_base_dir = os.path.join("music", "Songs")
                song_dir = find_file_case_insensitive(songs_base_dir, song_title)
                if song_dir and os.path.isdir(song_dir):
                    all_audio = [os.path.join(song_dir, f) for f in os.listdir(song_dir) 
                                 if f.lower().endswith(('.wav', '.mp3', '.ogg', '.flac'))]
                    if all_audio:
                        max_d = 0
                        for filepath in all_audio:
                            try:
                                with sf.SoundFile(filepath) as f:
                                    d = f.frames / f.samplerate
                                    if d > max_d: max_d = d
                            except: continue
                        duration = max_d
        
        if duration <= 0:
            if not silent: QMessageBox.warning(self, "Calculation Error", f"No duration found for {song_title}")
            return False

        # 2. Get Scroll Height (Max)
        # We need a rendered view to get the height. We use a hidden shadow browser.
        if not hasattr(self, '_shadow_browser'):
            self._shadow_browser = QTextBrowser(self)
            self._shadow_browser.setVisible(False)
        
        # Sync width and font size for accuracy
        self._shadow_browser.setFixedWidth(self.chordpro_display.width())
        # Apply current font size to the shadow browser's document
        font = self.chordpro_display.font()
        self._shadow_browser.setFont(font)
        
        html = self.data_manager.parse_chordpro(song_title, song_data.get("Transpose", 0), self.chord_font_size)
        self._shadow_browser.setHtml(html)
        # Force layout update and calculate scroll maximum
        doc = self._shadow_browser.document()
        doc.setTextWidth(self._shadow_browser.width())
        total_height = doc.size().height()
        # Scroll maximum is total height minus the viewport height
        max_scroll = max(0, int(total_height - self.chordpro_display.height()))
        
        if max_scroll <= 0:
            if not silent: QMessageBox.warning(self, "Calculation Error", f"Could not determine text height for {song_title}")
            return False

        # 3. Calculate Interval (ms per pixel)
        # (Duration_sec * 1000) / Max_Scroll
        interval = int((duration * 1000) / max_scroll)
        interval = max(10, min(500, interval)) # Clamp to sane range
        
        song_data["ScrollSpeed"] = interval
        self.data_manager.database[song_title] = song_data
        self.data_manager.save_database()
        
        if self.current_song == song_title:
            self.scroll_speed_slider.setValue(interval)
            self.autoscroll_interval = interval
            
        if not silent:
            QMessageBox.information(self, "Success", f"Autoscroll speed for '{song_title}' calculated: {interval}ms per pixel.")
        return True

    def calculate_all_autoscroll_speeds(self):
        count = self.setlist_widget.topLevelItemCount()
        if count == 0: return
        
        progress = QProgressDialog("Calculating all scroll speeds...", "Cancel", 0, count, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        
        success_count = 0
        for i in range(count):
            if progress.wasCanceled(): break
            item = self.setlist_widget.topLevelItem(i)
            song_title = item.data(0, Qt.ItemDataRole.UserRole)
            progress.setLabelText(f"Calculating: {song_title}")
            if self.calculate_autoscroll_speed(song_title, silent=True):
                success_count += 1
            progress.setValue(i + 1)
            
        progress.setValue(count)
        QMessageBox.information(self, "Complete", f"Calculated speeds for {success_count} songs.")
            
    def calculate_bpm(self, song_title):
        songs_base_dir = os.path.join("music", "Songs")
        song_dir = find_file_case_insensitive(songs_base_dir, song_title)
        target_file = None
        source_type = ""
        
        if song_dir and os.path.isdir(song_dir):
            all_files = [os.path.join(song_dir, f) for f in os.listdir(song_dir) if f.endswith(('.wav', '.mp3', '.ogg', '.flac'))]
            full_mix = [f for f in all_files if os.path.splitext(os.path.basename(f))[0].lower() == song_title.lower()]
            stem_files = [f for f in all_files if f not in full_mix]
            
            # Prioritize drum stem
            for f in stem_files:
                if 'drum' in os.path.basename(f).lower():
                    target_file, source_type = f, "Stem (Drums)"
                    break
                    
            # Fallback to full mix
            if not target_file and full_mix:
                target_file, source_type = full_mix[0], "Full Mix"
                
            # Final fallback to any stem
            if not target_file and stem_files:
                target_file, source_type = stem_files[0], "Stem (Other)"

        if not target_file:
            QMessageBox.warning(self, "BPM Error", f"No audio file found for {song_title}"); return

        self.progress_dialog = QProgressDialog(f"Audio analysis in progress for '{song_title}'...\nSource: {source_type}", "Cancel", 0, 0, self)
        self.progress_dialog.setWindowTitle("BPM Calculation"); self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal); self.progress_dialog.show()
        self.bpm_thread = BPMWorker(target_file, source_type, song_title)
        self.bpm_thread.finished.connect(self.on_bpm_success)
        self.bpm_thread.error.connect(self.on_bpm_error)
        self.progress_dialog.canceled.connect(self.bpm_thread.terminate)
        self.bpm_thread.start()

    def on_bpm_success(self, bpm_value, song_title, source_type):
        if hasattr(self, 'progress_dialog'): self.progress_dialog.close()
        bpm_rounded = int(round(bpm_value))
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("BPM Result"); msg_box.setIcon(QMessageBox.Icon.Information)
        msg_box.setText(f"Song: <b>{song_title}</b><br>Source: {source_type}<br>Estimated BPM: <b>{bpm_rounded}</b> <i>(exact: {bpm_value:.2f})</i>")
        save_btn = msg_box.addButton("Save to DB", QMessageBox.ButtonRole.ActionRole)
        msg_box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        msg_box.exec()
        if msg_box.clickedButton() == save_btn: self.save_bpm_to_db(song_title, bpm_rounded)

    def on_bpm_error(self, error_msg):
        if hasattr(self, 'progress_dialog'): self.progress_dialog.close()
        QMessageBox.critical(self, "Analysis Error", f"An error occurred during analysis:\n{error_msg}")
        
    def save_bpm_to_db(self, song_title, bpm):
        try:
            song_data = self.data_manager.database.get(song_title, {})
            song_data["BPM"] = bpm
            self.data_manager.database[song_title] = song_data
            self.data_manager.save_database()
            if self.current_song == song_title: self.refresh_current_song_state()
            QMessageBox.information(self, "Saved", f"The BPM ({bpm}) of '{song_title}' has been successfully updated in the database.")
        except Exception as e: QMessageBox.critical(self, "Save Error", f"Unable to save BPM to database:\n{str(e)}")
            
    def save_transposition_to_db(self):
        if self.current_song:
            if self.current_song not in self.data_manager.database: self.data_manager.database[self.current_song] = {}
            self.data_manager.database[self.current_song]["Transpose"] = self.transpose_steps
            self.data_manager.save_database()

    def handle_waveform_seek(self, pct):
        if self.audio_engine.max_frames > 0:
            self.audio_engine.set_position(pct * self.audio_engine.get_duration())
            self.update_playback_ui()

    def change_stems_pitch(self, value):
        """Updates the engine and UI based on an absolute pitch value."""
        # Constrain pitch shift range to +/- 1 octave
        value = max(-12, min(12, value))

        # 1. Update the Audio Engine
        self.audio_engine.set_pitch_shift(value)
        
        # 2. Update UI Label
        if hasattr(self, 'lbl_pitch_val'):
            self.lbl_pitch_val.setText(f"{value:+}")
            
        # 3. Persist to database
        self.save_stems_pitch_to_db(value)
    
    def save_stems_pitch_to_db(self, value):
        """Safely saves the pitch value using self.current_song as the ID key."""
        if not self.current_song:
            return
            
        # current_song is the ID (string), not a dict.
        song_id = self.current_song
        
        if song_id in self.data_manager.database:
            # Update the key in the database dictionary
            self.data_manager.database[song_id]["StemsPitch"] = value
            # Save to disk
            self.data_manager.save_database()
            logger.info(f"Saved StemsPitch {value} for song: {song_id}")

    def get_current_speed_bpm(self):
        """Helper to compute current target BPM from audio engine time_ratio"""
        if not self.current_song: return 120
        song_data = self.data_manager.database.get(self.current_song, {})
        base_bpm = float(song_data.get("BPM", 120.0))
        time_ratio = getattr(self.audio_engine, 'speed_ratio', 1.0)
        if time_ratio <= 0: return int(round(base_bpm))
        return int(round(base_bpm / time_ratio))

    def change_stems_speed_bpm(self, target_bpm):
        """Updates the engine and UI based on an absolute target BPM."""
        if not self.current_song: return
        song_data = self.data_manager.database.get(self.current_song, {})
        base_bpm = float(song_data.get("BPM", 120.0))
        
        # Limit extremes to avoid audio anomalies, e.g., max 2x, min 0.5x
        target_bpm = max(int(base_bpm * 0.5), min(int(base_bpm * 2.0), int(target_bpm)))
        
        time_ratio = base_bpm / float(target_bpm) if target_bpm > 0 else 1.0
        self.audio_engine.set_speed_ratio(time_ratio)
        
        # Crucial: Send target BPM downstream to the metronome
        self.sync_engine.set_bpm(float(target_bpm))
        self.bpm_bar.setText(f"BPM: {target_bpm}")
        
        if hasattr(self, 'lbl_speed_val'):
            self.lbl_speed_val.setText(f"{target_bpm} BPM")
            
        self.save_stems_speed_bpm_to_db(target_bpm)

    def reset_stems_speed_bpm(self):
        """Resets the stems speed to the base BPM of the current song."""
        if not self.current_song: return
        song_data = self.data_manager.database.get(self.current_song, {})
        base_bpm = float(song_data.get("BPM", 120.0))
        self.change_stems_speed_bpm(base_bpm)
        
    def save_stems_speed_bpm_to_db(self, value):
        """Safely saves the target BPM using self.current_song as the ID key."""
        if not self.current_song:
            return
            
        song_id = self.current_song
        
        if song_id in self.data_manager.database:
            self.data_manager.database[song_id]["StemsTargetBPM"] = value
            self.data_manager.save_database()
            logger.info(f"Saved StemsTargetBPM {value} for song: {song_id}")

    def update_waveform(self):
        if not self.current_song: self.waveform_viewer.set_audio_file(None); return
        songs_base_dir = os.path.join("music", "Songs")
        song_dir = find_file_case_insensitive(songs_base_dir, self.current_song)
        if song_dir and os.path.isdir(song_dir):
            all_files = [os.path.join(song_dir, f) for f in os.listdir(song_dir) if f.endswith(('.wav', '.mp3', '.ogg', '.flac'))]
            full_mix = [f for f in all_files if os.path.splitext(os.path.basename(f))[0].lower() == self.current_song.lower()]
            stem_files = [f for f in all_files if f not in full_mix]
            if full_mix:
                self.waveform_viewer.set_audio_file(full_mix[0])
            elif stem_files:
                self.waveform_viewer.set_audio_file(stem_files[0])
            elif all_files:
                self.waveform_viewer.set_audio_file(all_files[0])
            else:
                self.waveform_viewer.set_audio_file(None)
        else:
            self.waveform_viewer.set_audio_file(None)

    def add_song_marker(self, pct, label):
        if not self.current_song: return
        song_data = self.data_manager.database.get(self.current_song, {})
        markers = song_data.get("Markers", [])
        markers.append({'pct': pct, 'label': label})
        song_data["Markers"] = markers
        self.data_manager.database[self.current_song] = song_data
        self.data_manager.save_database()
        self.waveform_viewer.markers = markers
        self.waveform_viewer.update()

    def handle_marker_moved(self, idx, pct):
        if not self.current_song: return
        song_data = self.data_manager.database.get(self.current_song, {})
        markers = song_data.get("Markers", [])
        if 0 <= idx < len(markers):
            markers[idx]['pct'] = pct
            self.data_manager.save_database()

    def handle_marker_deleted(self, idx):
        if not self.current_song: return
        song_data = self.data_manager.database.get(self.current_song, {})
        markers = song_data.get("Markers", [])
        if 0 <= idx < len(markers):
            markers.pop(idx)
            self.data_manager.save_database()
            self.waveform_viewer.markers = markers
            self.waveform_viewer.update()

    def create_new_setlist(self):
        all_songs = list(self.data_manager.database.keys())
        dialog = EditSetlistDialog("", [], all_songs, self.main_window)
        if dialog.exec():
            filename, songs = dialog.get_data()
            if filename:
                cmd = UpdateSetlistCommand(self, filename, [], songs)
                self.undo_stack.push(cmd)
                if self.combo_setlists.findText(filename) == -1: self.combo_setlists.addItem(filename)
                self.combo_setlists.setCurrentText(filename)

    def edit_current_setlist(self):
        curr_songs = [self.setlist_widget.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.setlist_widget.topLevelItemCount())]
        dialog = EditSetlistDialog(self.current_csv, curr_songs, list(self.data_manager.database.keys()), self.main_window)
        if dialog.exec():
            filename, songs = dialog.get_data()
            cmd = UpdateSetlistCommand(self, filename, curr_songs, songs)
            self.undo_stack.push(cmd)
            if self.current_csv != filename:
                self.load_setlist_from_combo(filename)

    def add_new_song(self):
        dialog = SongEditorDialog("", None, self.main_window)
        if dialog.exec():
            title, data = dialog.get_updated_data()
            if title: self.data_manager.database[title] = data; self.data_manager.save_database(); self.add_song_to_list(title)

    def open_song_editor(self):
        curr = self.setlist_widget.currentItem()
        if not curr: return
        title = curr.data(0, Qt.ItemDataRole.UserRole)
        dialog = SongEditorDialog(title, self.data_manager.database.get(title, {}), self.main_window)
        if dialog.exec():
            t, data = dialog.get_updated_data()
            self.data_manager.database[title] = data; self.data_manager.save_database(); curr.setText(1, data.get("Notes", ""))
            self.refresh_current_song_state()

    def _custom_drop_event(self, event):
        item = self.setlist_widget.currentItem()
        if not item:
            self.original_drop_event(event)
            return
            
        old_idx = self.setlist_widget.indexOfTopLevelItem(item)
        
        # Perfom native drag and drop handling
        self.original_drop_event(event)
        
        new_idx = self.setlist_widget.indexOfTopLevelItem(item)
        if old_idx != new_idx and new_idx != -1:
            cmd = MoveSongCommand(self.setlist_widget, old_idx, new_idx, self)
            self.undo_stack.push(cmd)
            self.save_setlist_order_silent()

    def save_setlist_order_silent(self, *args):
        songs = [self.setlist_widget.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.setlist_widget.topLevelItemCount())]
        self.data_manager.save_setlist(self.current_csv, songs)
        self._sync_optimizer_current_setlist()
        
    def _reload_setlist_silently(self, songs):
        self.setlist_widget.clear()
        for song in songs:
            self.add_song_to_list(song)
        # Keep total duration in sync after edit/undo/redo setlist operations.
        self.update_total_setlist_time()
        self._sync_optimizer_current_setlist()

    def _sync_optimizer_current_setlist(self):
        """Keeps optimizer tab in sync with the currently active setlist."""
        if hasattr(self, "main_window") and hasattr(self.main_window, "optimizer_tab"):
            self.main_window.optimizer_tab.sync_with_setlist(self.current_csv)

    def _on_live_mode_button_clicked(self):
        """Triggered when the in-panel Live Mode button is clicked."""
        if hasattr(self, 'main_window') and self.main_window:
            self.main_window.toggle_live_mode()
        else:
            self.toggle_live_mode()
            
    def export_mixdown(self):
        from PyQt6.QtWidgets import QMessageBox, QFileDialog, QProgressDialog, QApplication
        from PyQt6.QtCore import Qt
        import tempfile
        import os
        import shutil

        if not self.current_song:
            QMessageBox.warning(self, "Export", "No song selected.")
            return

        # FORCE LOAD: If stems aren't loaded yet
        if getattr(self.audio_engine, 'stems_list', None) == [] or not self.audio_engine.stems_list:
            loaded = self.audio_engine.load_song(self.current_song)
            if not loaded or not self.audio_engine.stems_list:
                QMessageBox.warning(self, "Export", "No audio stems found for this song.")
                return

        # Get pitch and build filename
        pitch = self.audio_engine.pitch_semitones
        pitch_str = f"{pitch:+}" if pitch != 0 else "0"
        default_name = f"{self.current_song}_pitch{pitch_str}.mp3"
        
        out_path, _ = QFileDialog.getSaveFileName(self, "Export Mixdown", default_name, "MP3 Files (*.mp3)")
        if not out_path: 
            return
            
        # Setup Progress Dialog
        progress = QProgressDialog("Rendering fast mixdown...", "Cancel", 0, 100, self)
        progress.setWindowTitle("Exporting")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        
        # Callback to update the UI from the audio engine
        def progress_callback(percentage):
            progress.setValue(percentage)
            QApplication.processEvents() # Prevents UI freezing
            return not progress.wasCanceled() # Returns False if user clicked Cancel

        temp_wav = os.path.join(tempfile.gettempdir(), f"export_temp_{pitch_str}.wav")
        
        # 1. Export the WAV offline quickly (Allocating 0-90% of the progress bar for this)
        success, err = self.audio_engine.export_offline_mixdown(temp_wav, progress_callback)
        
        if success:
            progress.setLabelText("Converting to MP3 (This may take a moment)...")
            progress.setValue(90)
            QApplication.processEvents()
            
            try:
                # 2. Convert to MP3
                from pydub import AudioSegment
                audio = AudioSegment.from_wav(temp_wav)
                audio.export(out_path, format="mp3", bitrate="320k")
                
                if os.path.exists(temp_wav):
                    os.remove(temp_wav)
                
                progress.setValue(100)
                QMessageBox.information(self, "Export Complete", "Mixdown successfully saved as MP3!")
                
            except ImportError:
                progress.setValue(100)
                QMessageBox.warning(self, "Missing Module", "Please run: pip install pydub\n\nThe mixdown was saved as a .WAV file instead.")
                shutil.move(temp_wav, out_path.replace(".mp3", ".wav"))
            except Exception as e:
                progress.setValue(100)
                QMessageBox.warning(self, "MP3 Conversion Failed", f"WAV rendered, but MP3 conversion failed (Is FFmpeg installed?).\n\nError: {e}\n\nTemp WAV saved at: {temp_wav}")
        else:
            progress.close()
            if err != "Canceled":
                QMessageBox.critical(self, "Export Failed", f"An error occurred during export:\n{err}")

    def toggle_live_mode(self) -> bool:
        """
        Toggle live mode on/off.
        Returns True if live mode is now ON, False if now OFF.
        """
        self.is_live_mode = not self.is_live_mode

        # Editable widgets to hide in live mode
        edit_widgets = [
            self.btn_new_setlist, self.btn_edit_setlist,
            self.btn_add_song, self.btn_edit_song,
        ]
        # Transpose / zoom controls
        transpose_widgets = getattr(self, '_transpose_widgets', [])

        for w in edit_widgets:
            w.setVisible(not self.is_live_mode)

        for w in transpose_widgets:
            w.setVisible(not self.is_live_mode)

        # Drag-and-drop
        if self.is_live_mode:
            self.setlist_widget.setDragDropMode(
                self.setlist_widget.DragDropMode.NoDragDrop
            )
            self.btn_live_mode.setText("\ud83d\udd34  LIVE MODE: ON")
            self.btn_live_mode.setChecked(True)
        else:
            self.setlist_widget.setDragDropMode(
                self.setlist_widget.DragDropMode.InternalMove
            )
            self.btn_live_mode.setText("\ud83d\udd34  LIVE MODE: OFF")
            self.btn_live_mode.setChecked(False)

        return self.is_live_mode

    def retranslate_ui(self, tr):

        self.btn_sync.setText(tr["STOP_METRONOME"] if self.sync_engine.is_playing else tr["START_METRONOME"])
        self.btn_scroll.setText(tr["AUTO_SCROLL_ON"] if self.is_autoscrolling else tr["AUTO_SCROLL_OFF"])
        self.lbl_scroll_speed.setText(tr["SPEED"] + ":")
        self.setlist_widget.setHeaderLabels([tr["SONG"], tr["NOTES"]])
        self.lbl_setlist.setText(f"<b>{tr['SETLIST']}:</b>")
        self.btn_new_setlist.setText(tr["NEW_SETLIST"])
        self.btn_edit_setlist.setText(tr["EDIT_SETLIST"])
        self.btn_add_song.setText(tr["NEW_SONG"])
        self.btn_edit_song.setText(tr["EDIT_SONG"])
        self.lbl_sync_header.setText(f"<b>{tr['METRONOME_SYNC']}</b>")
        self.lbl_general_outputs.setText(f"<b>{tr['GENERAL_OUTPUTS']}</b>")
        self.check_midi.setText(tr["MIDI_OUT"]); self.check_dmx.setText(tr["DMX_OUT"])
        self.check_osc.setText(tr["OSC_OUT"]); self.check_mtc.setText(tr["MTC_OUT"])
        self.lbl_audio_player.setText(f"<b>{tr['AUDIO_PLAYER']}</b>")
        self.lbl_stems_mixer.setText(f"<b>{tr['STEMS_MIXER']}</b>")
        self.lbl_mute_groups_mixer.setText(f"<b>{tr['MUTE_GROUPS_MIXER']}</b>")
        if hasattr(self, 'btn_seq_toggle'):
            self.btn_seq_toggle.setText(tr["SEQ_ON"] if self.sync_engine.sequencer_enabled else tr["SEQ_OFF"])

    def apply_theme(self, colors):
        self.chordpro_display.setStyleSheet(f"background-color: {colors['secondary']}; color: {colors['text']}")
        for dial in self.findChildren(SmoothDial): dial.set_theme(colors)
        if hasattr(self, 'waveform_viewer'): self.waveform_viewer.set_theme(colors)
        
        self.btn_sync.setStyleSheet(f"""
            QPushButton {{ background-color: {colors['button']}; color: {colors['text']}; font-weight: bold; height: 28px; }}
            QPushButton:checked {{ background-color: {colors['accent']}; color: white; }}
        """)

        # Update BPM and MTC bars to match theme
        if hasattr(self, 'bpm_bar'):
            self.bpm_bar.setStyleSheet(f"background-color: {colors['card']}; color: {colors['text']}; font-size: 24px; font-weight: bold; border-top-left-radius: 4px;")
        if hasattr(self, 'mtc_bar'):
            self.mtc_bar.setStyleSheet(f"background-color: {colors['secondary']}; color: {colors['accent']}; font-family: monospace; font-size: 18px; font-weight: bold; border-bottom-left-radius: 4px;")
        if hasattr(self, 'qr_code_label'):
            self.qr_code_label.setStyleSheet(f"background-color: {colors['card']}; border-top-right-radius: 4px; border-bottom-right-radius: 4px;")
            
        # Refresh QR code with new colors
        self._update_qr_code()
