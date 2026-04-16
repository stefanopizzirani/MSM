"""
Module: hardware.py
Meaning: Hooks into hardware inputs (e.g. Bluetooth Pedals) cross-platform.
Working Procedure: Uses the `keyboard` library to catch global hotkeys 
(Page Up/Down, Up/Down) even if the application is not focused.
"""
import time
from PyQt6.QtCore import QThread, pyqtSignal
import keyboard

class HardwarePedal(QThread):
    pedal_signal = pyqtSignal(str)

    def __init__(self, device_name="cubeturner"):
        super().__init__()
        self.running = True
        # Keep the device_name argument for compatibility with gui.py,
        # even though on Windows the capture will be global for all keyboards/pedals.
        self.target_name = device_name 

    def run(self):
        # Define the callbacks that emit your original PyQt signals
        def on_scroll_down(event):
            self.pedal_signal.emit("SCROLL_DOWN")
            
        def on_scroll_up(event):
            self.pedal_signal.emit("SCROLL_UP")

        # Register global hooks.
        # on_press_key reacts only to the press, avoiding double inputs upon release.
        # Bluetooth pedals usually send Page Up/Down or Arrow Up/Down.
        try:
            hook_pgdn = keyboard.on_press_key('page down', on_scroll_down)
            hook_down = keyboard.on_press_key('down', on_scroll_down)
            hook_right = keyboard.on_press_key('right', on_scroll_down)
            
            hook_pgup = keyboard.on_press_key('page up', on_scroll_up)
            hook_up = keyboard.on_press_key('up', on_scroll_up)
            hook_left = keyboard.on_press_key('left', on_scroll_up)
            
            # Loop to keep the thread alive until stopped by the GUI
            while self.running:
                time.sleep(0.1)

        except Exception as e:
            print(f"Hardware Error (Pedal): {e}")

        finally:
            # Cleanup: when the app closes, release the hooks so they don't interfere with the OS
            try:
                keyboard.unhook(hook_pgdn)
                keyboard.unhook(hook_pgup)
                keyboard.unhook(hook_down)
                keyboard.unhook(hook_up)
                keyboard.unhook(hook_right)
                keyboard.unhook(hook_left)
            except:
                pass

    def stop(self):
        """Safely stops the background thread."""
        self.running = False
        self.wait()