import json
import logging
import threading
import time
import mido
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, Qt, QEvent
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox, QDialog

logger = logging.getLogger("MappingManager")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%H:%M:%S')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

class MappingManager(QObject):
    """
    Manages global keyboard hooks and MIDI input polling for controlling
    the application remotely.
    """
    action_triggered = pyqtSignal(str)
    
    # Emitted when in "Learn" mode
    midi_learned = pyqtSignal(list) # [status, data1, data2]
    key_learned = pyqtSignal(str)   # e.g. "space"

    def __init__(self, data_manager):
        super().__init__()
        self.data_manager = data_manager
        self.mappings = self.data_manager.mappings
        
        # Determine MIDI Port
        self.midi_port_name = self._get_saved_midi_port()
        self.midi_in = None
        self.midi_thread = None
        self.is_running = False
        
        # Learn Mode State
        self.learning_action = None
        self.learning_type = None # "midi" or "keys"
        
        # State for timing-based "Pedal Mode" (Long-Press Song Change)
        self.pedal_timers = {} # key_name -> QTimer
        self.pedal_consumed = set() # key_name
        self.pedal_keys = ["up", "down", "left", "right"]
        
        # State to prevent repeated triggers on held keys
        self.active_keys = set()
        
        # We'll use a QTimer to poll keyboard events if we don't want to use global hooks
        # However, `keyboard` library works well globally on Windows/Linux if run as root,
        # but for PyQt apps, installing an EventFilter on the QApplication is often safer.
        # We will expose a method `handle_key_event` to be called by the MainWindow.
        
    def _get_saved_midi_port(self):
        try:
            with open("config.json", "r") as f:
                conf = json.load(f)
                return conf.get("midi_in_port", None)
        except Exception:
            return None
            
    def set_midi_port(self, port_name):
        self.midi_port_name = port_name
        # Save to config
        try:
            with open("config.json", "r") as f:
                conf = json.load(f)
        except Exception:
            conf = {}
        conf["midi_in_port"] = port_name
        with open("config.json", "w") as f:
            json.dump(conf, f, indent=4)
            
        # Restart MIDI
        self.stop()
        self.start()

    def start(self):
        self.is_running = True
        
        # Setup MIDI
        if self.midi_port_name and self.midi_port_name in mido.get_input_names():
            try:
                self.midi_in = mido.open_input(self.midi_port_name)
                logger.info(f"MappingManager: Connected to MIDI In {self.midi_port_name}")
                
                self.midi_thread = threading.Thread(target=self._midi_loop, daemon=True)
                self.midi_thread.start()
            except Exception as e:
                logger.error(f"MappingManager: Failed to open MIDI port: {e}")
        # Install global Qt event filter as reliable fallback
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)
            logger.info("MappingManager: Qt Event Filter fallback installed on QApplication")

    def stop(self):
        self.is_running = False
        if self.midi_in:
            try:
                self.midi_in.close()
            except Exception:
                pass
            self.midi_in = None
            
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)

    def _midi_loop(self):
        """Background thread listening for MIDI messages."""
        while self.is_running and self.midi_in:
            try:
                # iter_pending is non-blocking but using receive() in a thread is better
                for msg in self.midi_in.iter_pending():
                    if msg.type in ['note_on', 'control_change', 'program_change']:
                        # Convert to raw bytes to match our JSON saving format
                        raw = msg.bytes()
                        
                        # Only trigger on Note On with velocity > 0, or CC with value > 0
                        if msg.type == 'note_on' and msg.velocity == 0:
                            continue
                            
                        self._handle_midi_input(raw)
            except Exception as e:
                logger.debug(f"MIDI loop error: {e}")
            time.sleep(0.01) # Small sleep to prevent 100% CPU

    def _handle_midi_input(self, raw_bytes):
        if self.learning_type == "midi" and self.learning_action:
            self.learning_type = None
            # Only save the first two bytes to ignore velocity/value differences
            # e.g. [144, 60] means Note On, Middle C, ignore velocity
            saved_bytes = raw_bytes[:2]
            self.midi_learned.emit(saved_bytes)
            return

        # Check against mappings
        # Check first 2 bytes (Status and Data1)
        check_bytes = raw_bytes[:2]
        
        for action, triggers in self.mappings.items():
            for midi_trigger in triggers.get("midi", []):
                if len(midi_trigger) >= 2 and midi_trigger[:2] == check_bytes:
                    self.action_triggered.emit(action)
                    return

    def eventFilter(self, obj, event):
        """Global Qt event filter to capture key press before focused widgets consume them."""
        if event.type() == QEvent.Type.KeyPress:
            # Check if the focused widget is an input widget or inside a dialog
            focus_widget = QApplication.focusWidget()
            if focus_widget:
                # 1. Skip if it's a known input widget
                from PyQt6.QtWidgets import QDoubleSpinBox
                if isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox)):
                    return super().eventFilter(obj, event)
                
                # 2. Skip if it's part of a QDialog (like our MIDI Actions editor)
                # We want mappings to work primarily on the MainWindow
                parent = focus_widget
                while parent:
                    if isinstance(parent, QDialog):
                        return super().eventFilter(obj, event)
                    parent = parent.parent()

            # handle_qt_key_press only returns True if the key was learned OR triggered an action.
            if self.handle_qt_key_press(event):
                return True
        elif event.type() == QEvent.Type.KeyRelease:
            if self.handle_qt_key_release(event):
                return True
        return super().eventFilter(obj, event)

    def _on_pedal_timeout(self, key_name, long_action):
        """Triggered when a navigation key is held long enough."""
        self.pedal_consumed.add(key_name)
        logger.info(f"MappingManager: Long Press Triggered -> {long_action}")
        self.action_triggered.emit(long_action)

    # Fallback for Qt Event Filter if `keyboard` library fails
    def handle_qt_key_press(self, key_event: QKeyEvent):
        """
        Extracts key name and checks learning mode / active triggers.
        Returns True if the event was consumed.
        """
        # Convert Qt Key to string
        key_map = {
            Qt.Key.Key_Space: "space",
            Qt.Key.Key_PageDown: "page down",
            Qt.Key.Key_PageUp: "page up",
            Qt.Key.Key_Up: "up",
            Qt.Key.Key_Down: "down",
            Qt.Key.Key_Left: "left",
            Qt.Key.Key_Right: "right",
            Qt.Key.Key_Enter: "enter",
            Qt.Key.Key_Return: "enter",
            Qt.Key.Key_Escape: "esc",
        }
        
        key_name = key_map.get(key_event.key(), key_event.text().lower())
        if not key_name:
            return False

        if key_event.isAutoRepeat():
            # Consume repeats for navigation keys to prevent native widget movement
            if key_name in self.pedal_keys:
                return True
            return False
            
        if self.learning_type == "keys" and self.learning_action:
            self.learning_type = None
            self.key_learned.emit(key_name)
            return True

        # Special Pedal Logic for Navigation Keys
        if key_name in self.pedal_keys:
            # Short-circuit: if we already have a timer, just ignore repeats
            if key_name in self.pedal_timers:
                return True
                
            long_action = "NEXT_SONG" if key_name in ["down", "right"] else "PREV_SONG"
            
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._on_pedal_timeout(key_name, long_action))
            timer.start(600) # 600ms threshold
            self.pedal_timers[key_name] = timer
            return True

        for action, triggers in self.mappings.items():
            keys = [k.lower() for k in triggers.get("keys", [])]
            if key_name in keys:
                self.action_triggered.emit(action)
                return True
                
        return False

    def handle_qt_key_release(self, key_event: QKeyEvent):
        """Handles pedal release to trigger short-press (scroll) actions."""
        if key_event.isAutoRepeat():
            return True
            
        key_map = {
            Qt.Key.Key_Up: "up",
            Qt.Key.Key_Down: "down",
            Qt.Key.Key_Left: "left",
            Qt.Key.Key_Right: "right",
        }
        key_name = key_map.get(key_event.key())
        if not key_name or key_name not in self.pedal_keys:
            return False
            
        # Stop and remove the long-press timer
        timer = self.pedal_timers.pop(key_name, None)
        if timer:
            timer.stop()
            timer.deleteLater()
            
        # If it wasn't a long press, trigger the short press (scroll)
        if key_name not in self.pedal_consumed:
            short_action = "SCROLL_DOWN" if key_name in ["down", "right"] else "SCROLL_UP"
            logger.info(f"MappingManager: Multi-Action Trigger -> Short Press ({key_name}) -> {short_action}")
            self.action_triggered.emit(short_action)
        else:
            logger.info(f"MappingManager: Multi-Action Complete -> Handled Long Press for {key_name}")
            self.pedal_consumed.remove(key_name)
            
        return True

    def enter_learn_mode(self, action, hw_type):
        """hw_type is 'midi' or 'keys'"""
        self.learning_action = action
        self.learning_type = hw_type
        
    def cancel_learn_mode(self):
        self.learning_action = None
        self.learning_type = None

    def update_mapping(self, action, hw_type, trigger_data):
        if action not in self.mappings:
            self.mappings[action] = {"midi": [], "keys": []}
            
        if trigger_data not in self.mappings[action][hw_type]:
            self.mappings[action][hw_type].append(trigger_data)
            self.data_manager.save_mappings()

    def remove_mapping(self, action, hw_type, trigger_data):
        if action in self.mappings:
            if trigger_data in self.mappings[action][hw_type]:
                self.mappings[action][hw_type].remove(trigger_data)
                self.data_manager.save_mappings()
