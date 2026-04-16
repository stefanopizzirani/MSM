import sys
import os
import subprocess
import shutil
import tempfile
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QFileDialog, QTextEdit, QListWidget,
    QGroupBox, QLineEdit, QMessageBox, QSpinBox, QDoubleSpinBox
)
from PyQt6.QtCore import QThread, pyqtSignal

# Import Playwright for headless browser automation
from playwright.sync_api import sync_playwright

os.environ["QT_LOGGING_RULES"] = "qt.qpa.wayland.textinput=false"

# --- THEME DICTIONARY (Synchronized with gui.py) ---
THEMES = {
    "Adwaita Dark":     {"primary": "#1e1e1e", "secondary": "#242424", "accent": "#3584e4", "text": "#ffffff", "card": "#2d2d2d", "border": "#1a1a1a", "button": "#3a3a3a"},
    "Adwaita Light":    {"primary": "#f6f5f4", "secondary": "#ffffff", "accent": "#1c71d8", "text": "#2e3436", "card": "#ffffff", "border": "#cdc7c2", "button": "#eeeeec"},
    "Yaru Dark":        {"primary": "#111111", "secondary": "#1d1d1d", "accent": "#e95420", "text": "#eeeeee", "card": "#262626", "border": "#000000", "button": "#333333"},
    "Solarized Dark":   {"primary": "#002b36", "secondary": "#073642", "accent": "#268bd2", "text": "#839496", "card": "#073642", "border": "#586e75", "button": "#002b36"},
    "Solarized Light":  {"primary": "#fdf6e3", "secondary": "#eee8d5", "accent": "#268bd2", "text": "#657b83", "card": "#ffffff", "border": "#d5d1c1", "button": "#fdf6e3"},
    "Gruvbox Dark":     {"primary": "#282828", "secondary": "#1d2021", "accent": "#fe8019", "text": "#ebdbb2", "card": "#3c3836", "border": "#504945", "button": "#665c54"},
    "Dracula":          {"primary": "#282a36", "secondary": "#44475a", "accent": "#bd93f9", "text": "#f8f8f2", "card": "#282a36", "border": "#6272a4", "button": "#44475a"},
    "Clearlooks":       {"primary": "#edeceb", "secondary": "#e1e0df", "accent": "#729fcf", "text": "#2e3436", "card": "#ffffff", "border": "#b6b3b0", "button": "#f6f5f4"},
    "Materia Dark":     {"primary": "#212121", "secondary": "#2c2c2c", "accent": "#4080ff", "text": "#ffffff", "card": "#2c2c2c", "border": "#1a1a1a", "button": "#373737"},
    "Modern Dark":      {"primary": "#121212", "secondary": "#1e1e1e", "accent": "#ff9900", "text": "#ffffff", "card": "#252525", "border": "#333333", "button": "#3a3a3a"},
    "Deep Blue":        {"primary": "#050a14", "secondary": "#0a192f", "accent": "#00d4ff", "text": "#e6f1ff", "card": "#112240", "border": "#1b3a5b", "button": "#1d3557"},
    "High Contrast":    {"primary": "#000000", "secondary": "#000000", "accent": "#ffff00", "text": "#ffffff", "card": "#1a1a1a", "border": "#ffffff", "button": "#333333"},
    "Nordic":           {"primary": "#2e3440", "secondary": "#3b4252", "accent": "#88c0d0", "text": "#eceff4", "card": "#434c5e", "border": "#4c566a", "button": "#4c566a"},
    "Cyberpunk":        {"primary": "#0d0221", "secondary": "#0f0817", "accent": "#ff007f", "text": "#00f5ff", "card": "#1a1b41", "border": "#38003c", "button": "#240b36"},
    "Forest Night":     {"primary": "#0b0f0b", "secondary": "#141a14", "accent": "#4ade80", "text": "#ecfdf5", "card": "#1e291b", "border": "#2d3a2a", "button": "#22c55e"},
    "Pure Light":       {"primary": "#ffffff", "secondary": "#f8f9fa", "accent": "#007bff", "text": "#212529", "card": "#ffffff", "border": "#dee2e6", "button": "#e9ecef"},
    "Soft Cream":       {"primary": "#fdfcf0", "secondary": "#f4f1de", "accent": "#e07a5f", "text": "#3d405b", "card": "#ffffff", "border": "#dfd8ca", "button": "#f2cc8f"},
    "Rose Quartz":      {"primary": "#fff5f7", "secondary": "#fae8ed", "accent": "#d23669", "text": "#4a0e1e", "card": "#ffffff", "border": "#f1d1d9", "button": "#f8bbd0"},
    "Slate Morning":    {"primary": "#f1f5f9", "secondary": "#e2e8f0", "accent": "#4f46e5", "text": "#0f172a", "card": "#ffffff", "border": "#cbd5e1", "button": "#f8fafc"}
}

class SeparationWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    
    def __init__(self, items, output_dir, model, out_format, quality, two_stems, shifts, overlap, device):
        super().__init__()
        self.items = items
        self.output_dir = output_dir
        self.model = model
        self.out_format = out_format
        self.quality = quality
        
        # New Demucs Options
        self.two_stems = two_stems
        self.shifts = shifts
        self.overlap = overlap
        self.device = device
        
        self.is_cancelled = False
        self.current_process = None

    def stop(self):
        """Method called by GUI to interrupt the running process."""
        self.is_cancelled = True
        self.log_signal.emit("\n🛑 STOP REQUEST RECEIVED. Closing processes...")
        if self.current_process:
            try:
                self.current_process.terminate()
                self.current_process.wait(timeout=2)
            except:
                self.current_process.kill()

    def fetch_youtube_cookies(self, cookie_path):
        """
        Uses Chromium Headless to simulate human visit to YouTube, 
        extract fresh cookies and save them in Netscape format for SpotDL.
        """
        self.log_signal.emit("🤖 Starting Chromium Headless...")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                page.goto("https://www.youtube.com", timeout=30000)
                page.wait_for_timeout(3000) 
                
                try:
                    page.locator("button:has-text('Accetta tutto'), button:has-text('Accept all')").first.click(timeout=3000)
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
                    
                cookies = context.cookies()
                with open(cookie_path, 'w', encoding='utf-8') as f:
                    f.write("# Netscape HTTP Cookie File\n\n")
                    for c in cookies:
                        domain = c.get('domain', '')
                        flag = 'TRUE' if domain.startswith('.') else 'FALSE'
                        path = c.get('path', '/')
                        secure = 'TRUE' if c.get('secure', False) else 'FALSE'
                        expires = str(int(c.get('expires', 0)))
                        name = c.get('name', '')
                        value = c.get('value', '')
                        f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
                        
                browser.close()
                self.log_signal.emit("🍪 Cookies generated and exported successfully!")
                return True
        except Exception as e:
            self.log_signal.emit(f"⚠️ Unable to generate cookies via Headless: {e}")
            return False

    def run(self):
        output_folder_path = Path(self.output_dir)
        output_folder_path.mkdir(parents=True, exist_ok=True)

        for index, item in enumerate(self.items):
            if self.is_cancelled: break 
            
            self.log_signal.emit(f"\n▶️ Processing {index + 1}/{len(self.items)}: {item}")
            
            if item.startswith("http"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    cookie_file = os.path.join(temp_dir, "fresh_cookies.txt")
                    self.fetch_youtube_cookies(cookie_file)
                    
                    if self.is_cancelled: break
                    
                    self.log_signal.emit("⏳ Downloading via SpotDL...")
                    dl_cmd = [
                        "spotdl", item, 
                        "--format", "mp3", 
                        "--audio", "soundcloud", "youtube-music","youtube",
                        "--output", f"{temp_dir}/{{title}} ### {{artist}}.{{ext}}"
                    ]
                    
                    if os.path.exists(cookie_file):
                        dl_cmd.extend(["--cookie-file", cookie_file])
                    
                    try:
                        self.current_process = subprocess.Popen(dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                        for line in iter(self.current_process.stdout.readline, ''):
                            if self.is_cancelled: break
                            if line: self.log_signal.emit(line.strip())
                            
                        self.current_process.wait()
                        if self.is_cancelled: continue

                        downloaded_files = [f for f in os.listdir(temp_dir) if f.endswith('.mp3')]
                        if not downloaded_files:
                            self.log_signal.emit("❗️ Download failed. Please try again later.")
                            continue
                            
                        for dl_file in downloaded_files:
                            if self.is_cancelled: break
                            
                            full_temp_path = Path(temp_dir) / dl_file
                            name_part = dl_file.replace(".mp3", "")
                            if " ### " in name_part:
                                title, artist = name_part.split(" ### ", 1)
                            else:
                                title, artist = name_part, "Unknown Artist"

                            song_out_dir = output_folder_path / title
                            song_out_dir.mkdir(exist_ok=True)

                            final_song_path = song_out_dir / f"{title}.mp3"
                            
                            if final_song_path.exists():
                                final_song_path.unlink()
                            shutil.move(str(full_temp_path), str(final_song_path))
                            
                            self._process_ai(final_song_path, title, song_out_dir)

                    except Exception as e:
                        if not self.is_cancelled:
                            self.log_signal.emit(f"❗️ Critical error: {str(e)}")
            else:
                source_path = Path(item)
                song_name = source_path.stem
                song_out_dir = output_folder_path / song_name
                song_out_dir.mkdir(exist_ok=True)
                
                final_song_path = song_out_dir / source_path.name
                if source_path.absolute() != final_song_path.absolute():
                    if final_song_path.exists():
                        final_song_path.unlink()
                    shutil.copy2(source_path, final_song_path)
                    self.log_signal.emit(f"💾 Original song copy saved.")
                
                self._process_ai(final_song_path, song_name, song_out_dir)

        if self.is_cancelled:
            self.log_signal.emit("\n🚫 Operation cancelled by the user.")
        self.finished_signal.emit()

    def _process_ai(self, file_path, name, out_dir):
        if self.is_cancelled: return
        self.log_signal.emit(f"⏳ Starting stem separation for: {name}")
        
        import sys
        command = [
            sys.executable, "-m", "demucs", "-v", "-n", self.model,
            "--filename", str(out_dir / "{stem}.{ext}")
        ]
        
        # Audio formats
        if self.out_format == "mp3":
            command.extend(["--mp3", f"--mp3-bitrate={self.quality}"])
        elif self.out_format == "ogg":
            command.extend(["--ogg", f"--ogg-bitrate={self.quality}"]) 
        elif self.out_format == "flac":
            command.append("--flac")
            
        # --- NEW ADVANCED OPTIONS ---
        if self.two_stems and self.two_stems.lower() != "none":
            command.extend(["--two-stems", self.two_stems.lower()])
            
        command.extend(["--shifts", str(self.shifts)])
        command.extend(["--overlap", str(self.overlap)])
        
        if self.device and self.device.lower() != "auto":
            command.extend(["-d", self.device.lower()])
        # ----------------------------
        
        command.append(str(file_path))

        try:
            self.current_process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in iter(self.current_process.stdout.readline, ''):
                if self.is_cancelled: break
                if line: self.log_signal.emit(line.strip())
            
            self.current_process.wait()

            if not self.is_cancelled:
                if self.current_process.returncode == 0:
                    self.log_signal.emit(f"✅ Finished: Stems for '{name}' are ready.")
                else:
                    self.log_signal.emit(f"❗️ Error during the processing of '{name}'.")
        except Exception as e:
            if not self.is_cancelled:
                self.log_signal.emit(f"❗️ Critical error in AI engine: {str(e)}")


class DemucsGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stem Creator")
        self.resize(800, 950) # Slightly larger to accommodate new UI
        self.worker = None

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        # 1. Input Group
        input_group = QGroupBox("1. Add Songs (File or Link)")
        input_layout = QVBoxLayout()
        
        btn_layout = QHBoxLayout()
        self.btn_add_files = QPushButton("📁 Add Audio Files")
        self.btn_add_files.clicked.connect(self.add_files)
        self.btn_clear_files = QPushButton("🗑 Clear List")
        self.btn_clear_files.clicked.connect(self.clear_files)
        btn_layout.addWidget(self.btn_add_files)
        btn_layout.addWidget(self.btn_clear_files)
        
        link_layout = QHBoxLayout()
        self.line_url = QLineEdit()
        self.line_url.setPlaceholderText("Paste a Spotify or YouTube link...")
        self.btn_add_url = QPushButton("🔗 Add Link")
        self.btn_add_url.clicked.connect(self.add_url)
        link_layout.addWidget(self.line_url)
        link_layout.addWidget(self.btn_add_url)
        
        self.list_files = QListWidget()
        
        input_layout.addLayout(link_layout)
        input_layout.addLayout(btn_layout)
        input_layout.addWidget(self.list_files)
        input_group.setLayout(input_layout)
        main_layout.addWidget(input_group)

        # 2. Output Group
        output_group = QGroupBox("2. Output Directory")
        output_layout = QHBoxLayout()
        self.line_output_dir = QLineEdit()
        
        default_dir = os.path.abspath(os.path.join(".", "music", "Songs"))
        self.line_output_dir.setText(default_dir)
        self.line_output_dir.setPlaceholderText("Select the folder to save stems...")
        self.line_output_dir.setReadOnly(True)
        self.btn_browse_output = QPushButton("Browse...")
        self.btn_browse_output.clicked.connect(self.browse_output_dir)
        output_layout.addWidget(self.line_output_dir)
        output_layout.addWidget(self.btn_browse_output)
        output_group.setLayout(output_layout)
        main_layout.addWidget(output_group)

        # 3. AI Settings
        settings_group = QGroupBox("3. AI Settings")
        settings_layout = QVBoxLayout()
        
        # Row 1: Model
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        
        row1.addWidget(QLabel("Model:"))
        self.combo_model = QComboBox()
        self.combo_model.setEditable(True)
        self.combo_model.setMinimumWidth(300)
        row1.addWidget(self.combo_model)
        
        row1.addStretch()
        settings_layout.addLayout(row1)

        # Row 2: Format & Quality
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        
        row2.addWidget(QLabel("Stem Format:"))
        self.combo_format = QComboBox()
        self.combo_format.addItems(["mp3", "ogg", "flac", "wav"])
        self.combo_format.currentTextChanged.connect(self.toggle_quality)
        row2.addWidget(self.combo_format)
        
        row2.addSpacing(20)

        self.label_quality = QLabel(" MP3/OGG Quality:")
        row2.addWidget(self.label_quality)
        self.combo_quality = QComboBox()
        self.combo_quality.addItems(["320", "256", "192", "128"])
        row2.addWidget(self.combo_quality)
        row2.addStretch()
        settings_layout.addLayout(row2)

        # Row 3: Advanced Options (Shifts, Overlap, Two-Stems, Device)
        row3 = QHBoxLayout()
        row3.setSpacing(10)
        
        row3.addWidget(QLabel("Two Stems:"))
        self.combo_two_stems = QComboBox()
        self.combo_two_stems.addItems(["None", "vocals", "drums", "bass", "other", "piano", "guitar"])
        self.combo_two_stems.setToolTip("Extract only one stem and leave the rest as a mixed track. Great for Karaoke!")
        row3.addWidget(self.combo_two_stems)
        
        row3.addSpacing(20)
        
        row3.addWidget(QLabel(" Shifts:"))
        self.spin_shifts = QSpinBox()
        self.spin_shifts.setRange(0, 10)
        self.spin_shifts.setValue(2)
        self.spin_shifts.setToolTip("Multiple predictions with random shifts. Higher = better quality but much slower.")
        row3.addWidget(self.spin_shifts)
        
        row3.addSpacing(20)
        
        row3.addWidget(QLabel(" Overlap:"))
        self.spin_overlap = QDoubleSpinBox()
        self.spin_overlap.setRange(0.1, 0.99)
        self.spin_overlap.setSingleStep(0.1)
        self.spin_overlap.setValue(0.5)
        self.spin_overlap.setToolTip("Overlap between prediction windows. Higher = better quality, uses more RAM/VRAM.")
        row3.addWidget(self.spin_overlap)

        row3.addSpacing(20)

        row3.addWidget(QLabel(" Device:"))
        self.combo_device = QComboBox()
        self.combo_device.addItems(["auto", "cuda", "cpu", "mps"])
        self.combo_device.setToolTip("Force processing on GPU (cuda/mps) or CPU.")
        row3.addWidget(self.combo_device)
        
        row3.addStretch()
        
        settings_layout.addLayout(row3)

        settings_group.setLayout(settings_layout)
        main_layout.addWidget(settings_group)
        self.update_model_list()

        self.btn_start = QPushButton("▶ START PROCESSING")
        self.btn_start.clicked.connect(self.toggle_processing)
        main_layout.addWidget(self.btn_start)

        log_group = QGroupBox("System Console")
        log_layout = QVBoxLayout()
        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        log_layout.addWidget(self.text_log)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

    def apply_theme(self, theme_data):
        if isinstance(theme_data, str):
            colors = THEMES.get(theme_data, THEMES["Modern Dark"])
        else:
            colors = theme_data

        if not colors or not isinstance(colors, dict):
            return

        qss = f"""
        QMainWindow, QWidget {{ background-color: {colors['primary']}; color: {colors['text']}; }}
        QGroupBox {{ background-color: {colors['secondary']}; border: 1px solid {colors['border']}; margin-top: 10px; font-weight: bold; }}
        QPushButton {{ background-color: {colors['button']}; color: {colors['text']}; border: 1px solid {colors['border']}; padding: 5px; border-radius: 4px; }}
        QPushButton:hover {{ background-color: {colors['accent']}; color: white; }}
        QLineEdit, QComboBox, QTextEdit, QListWidget, QSpinBox, QDoubleSpinBox {{ 
        background-color: {colors['card']}; 
        color: {colors['text']}; 
        border: 1px solid {colors['border']}; 
        border-radius: 3px;
        min-width: 80px;  /* <--- Set global minimum width */
        padding: 2px 5px; /* <--- Improve internal readability */
        }}
        """
        self.setStyleSheet(qss)
        self.text_log.setStyleSheet(f"background-color: {colors['card']}; color: {colors.get('accent', '#4af626')}; font-family: Consolas, monospace; border: 1px solid {colors['border']};")

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Audio Files", "", 
            "Audio Files (*.mp3 *.wav *.flac *.ogg);;All files (*.*)"
        )
        for f in files:
            if not self.item_exists(f): self.list_files.addItem(f)

    def add_url(self):
        url = self.line_url.text().strip()
        if url.startswith("http"):
            if not self.item_exists(url):
                self.list_files.addItem(url)
                self.line_url.clear()
        else:
            QMessageBox.warning(self, "Invalid Link", "Please enter a valid link starting with http:// or https://")

    def item_exists(self, text):
        items = [self.list_files.item(i).text() for i in range(self.list_files.count())]
        return text in items

    def clear_files(self):
        self.list_files.clear()

    def browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if dir_path:
            self.line_output_dir.setText(dir_path)

    def update_model_list(self):
        self.combo_model.clear()
        self.combo_model.addItems([
            "htdemucs_6s", "htdemucs", "htdemucs_ft", "hdemucs_mmi",
            "mdx_extra", "mdx_extra_q", "mdx", "mdx_q"
        ])

    def toggle_quality(self, text):
        state = text not in ["wav", "flac"]
        self.combo_quality.setEnabled(state)
        self.label_quality.setEnabled(state)

    def append_log(self, text):
        self.text_log.append(text)
        scrollbar = self.text_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def toggle_processing(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.btn_start.setEnabled(False)
            self.btn_start.setText("Stopping...")
        else:
            self.start_processing()

    def start_processing(self):
        if self.list_files.count() == 0:
            QMessageBox.warning(self, "Error", "Add at least one file or link.")
            return
        output_dir = self.line_output_dir.text()
        if not output_dir:
            QMessageBox.warning(self, "Error", "Select a destination folder.")
            return

        self.set_ui_enabled(False)
        self.text_log.clear()

        self.btn_start.setText("🛑 STOP PROCESSING")
        self.btn_start.setStyleSheet("background-color: #c62828; color: white; font-weight: bold; padding: 10px; font-size: 14px;")

        items = [self.list_files.item(i).text() for i in range(self.list_files.count())]
        model = self.combo_model.currentText()
        out_format = self.combo_format.currentText()
        quality = self.combo_quality.currentText()
        
        # New variables from UI
        two_stems = self.combo_two_stems.currentText()
        shifts = self.spin_shifts.value()
        overlap = self.spin_overlap.value()
        device = self.combo_device.currentText()

        self.worker = SeparationWorker(
            items, output_dir, model, out_format, quality,
            two_stems, shifts, overlap, device
        )
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished_signal.connect(self.on_processing_finished)
        self.worker.start()

    def on_processing_finished(self):
        self.set_ui_enabled(True)
        self.btn_start.setText("▶ START PROCESSING")
        self.btn_start.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 10px; font-size: 14px;")
        
        if self.worker.is_cancelled:
            QMessageBox.information(self, "Stopped", "Process successfully stopped by the user.")
        else:
            self.append_log("\n🎉 ALL OPERATIONS COMPLETED!")
            QMessageBox.information(self, "Completed", "Processing completed successfully!")

    def set_ui_enabled(self, enabled):
        self.btn_add_files.setEnabled(enabled)
        self.btn_add_url.setEnabled(enabled)
        self.line_url.setEnabled(enabled)
        self.btn_clear_files.setEnabled(enabled)
        self.btn_browse_output.setEnabled(enabled)
        self.combo_model.setEnabled(enabled)
        self.combo_format.setEnabled(enabled)
        
        # Toggle new UI elements
        self.combo_two_stems.setEnabled(enabled)
        self.spin_shifts.setEnabled(enabled)
        self.spin_overlap.setEnabled(enabled)
        self.combo_device.setEnabled(enabled)
        
        self.btn_start.setEnabled(True) 
        
        if enabled:
            self.toggle_quality(self.combo_format.currentText())

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(2000) 
        event.accept()
        
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = DemucsGUI()
    window.show()
    sys.exit(app.exec())