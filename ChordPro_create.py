import sys
import os
import json
import tempfile
import configparser
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QFileDialog, QTextEdit, QListWidget,
    QGroupBox, QLineEdit, QMessageBox
)
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QIcon

# Import Playwright for web scraping
from playwright.sync_api import sync_playwright

# Import the NEW Google SDK for Gemini
from google import genai
from google.genai import types

os.environ["QT_LOGGING_RULES"] = "qt.qpa.wayland.textinput=false"

# --- THEME DICTIONARY ---
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

class ChordProWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    
    def __init__(self, urls, output_dir, api_key):
        super().__init__()
        self.urls = urls
        self.output_dir = output_dir
        self.api_key = api_key
        self.is_cancelled = False
        self.client = None

    def stop(self):
        self.is_cancelled = True
        self.log_signal.emit("\n🛑 STOP REQUEST RECEIVED. Stopping...")

    def run(self):
        output_folder_path = Path(self.output_dir)
        output_folder_path.mkdir(parents=True, exist_ok=True)

        try:
            self.client = genai.Client(api_key=self.api_key)
        except Exception as e:
            self.log_signal.emit(f"❗️ Error in Gemini API configuration: {e}")
            self.finished_signal.emit()
            return

        for index, url in enumerate(self.urls):
            if self.is_cancelled: break 
            
            self.log_signal.emit(f"\n▶️ Processing {index + 1}/{len(self.urls)}: {url}")
            
            # PHASE 1: Web Scraping with Playwright
            self.log_signal.emit("🕵️ Starting Playwright for tab retrieval...")
            raw_text = ""
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=False)
                    context = browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    )
                    page = context.new_page()
                    
                    self.log_signal.emit("⏳ Waiting for page to load (resolve captcha if it appears)...")
                    page.goto(url, timeout=45000)
                    
                    try:
                        page.wait_for_selector("h1", timeout=15000)
                    except:
                        self.log_signal.emit("⚠️ Slow loading or Cloudflare. Waiting another 10 seconds...")
                        page.wait_for_timeout(10000)
                    
                    try:
                        page.locator("button:has-text('AGREE'), button:has-text('Accetta'), button:has-text('Accept')").first.click(timeout=2000)
                    except:
                        pass
                    
                    self.log_signal.emit("🔍 Searching for chords on the page...")
                    
                    title_header = ""
                    try:
                        if page.locator("h1").count() > 0:
                            title_header = "TITLE AND ARTIST: " + page.locator("h1").first.inner_text() + "\n\n"
                    except: pass

                    if page.locator("pre").count() > 0:
                        raw_text = title_header + page.locator("pre").first.inner_text(timeout=3000)
                    elif page.locator("main").count() > 0:
                        raw_text = page.locator("main").inner_text(timeout=3000)
                    else:
                        raw_text = page.locator("body").inner_text()
                        
                    browser.close()
            except Exception as e:
                self.log_signal.emit(f"⚠️ Error during scraping: {e}")
                continue
                
            if len(raw_text) < 50:
                self.log_signal.emit("❗️ Extracted text too short.")
                continue

            if self.is_cancelled: break

            # PHASE 2: Conversion with Gemini AI
            self.log_signal.emit("✨ Sending data to Gemini for ChordPro formatting...")
            
            # ORIGINAL AND WORKING PROMPT
            prompt = f"""
            You are a music expert. I extracted the following text from Ultimate Guitar. 
            WARNING: The text contains a lot of 'junk' (menus, ads, user comments, reviews at the bottom). 
            
            DO NOT GIVE UP. You must scan the entire text, find the central body with the chords and lyrics of the song, and ignore everything else!
            
            Your task:
            1. Identify the title and artist.
            2. Extract ONLY the musical part (lyrics + chords), discarding UI and user comments.
            3. Accurately format the text in ChordPro format. 
            4. The chords must be integrated into the text between square brackets, eg: [C]Hello [G]world
            5. The sections (Verse, Chorus, Intro) must be indicated between curly braces, eg: {{Chorus}}
            6. At the beginning of the file, put the title and artist between curly braces, eg: {{Song Name}} and on the next line {{Artist}}
            
            Here is the raw text to analyze:
            {raw_text}
            
            YOU MUST respond ONLY with a valid JSON object structured like this:
            {{
                "filename": "Song Name.pro",
                "content": "all the content in chordpro formatted exactly as requested..."
            }}
            """
            
            try:
                # Restored gemini-2.5-flash
                response = self.client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                    )
                )
                
                result = json.loads(response.text)
                
                filename = result.get("filename", f"unknown_song_{index}.pro")
                content = result.get("content", "")
                
                # --- PYTHON ONLY CLEANING ---
                # Surgically removes initial spaces/tabs line by line
                content = "\n".join(line.lstrip() for line in content.split("\n"))
                
                filename = "".join(c for c in filename if c.isalnum() or c in " ._-").rstrip()
                final_path = output_folder_path / filename
                
                with open(final_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                    
                self.log_signal.emit(f"✅ File saved successfully: {filename}")
                
            except Exception as e:
                self.log_signal.emit(f"❗️ Error during Gemini generation or parsing: {e}")

        if self.is_cancelled:
            self.log_signal.emit("\n🚫 Operation cancelled by the user.")
        self.finished_signal.emit()


class ChordProGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UG to ChordPro Converter")
        self.resize(750, 800)
        self.worker = None
        self.config_file = "config.ini"
        self.config = configparser.ConfigParser()

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        # 1. API KEY SECTION
        api_group = QGroupBox("1. Google Gemini API Key")
        api_layout = QVBoxLayout()
        self.line_api_key = QLineEdit()
        self.line_api_key.setPlaceholderText("Paste your Gemini API Key here...")
        self.line_api_key.setEchoMode(QLineEdit.EchoMode.Password) 
        api_layout.addWidget(self.line_api_key)
        api_group.setLayout(api_layout)
        main_layout.addWidget(api_group)

        # 2. INPUT SECTION
        input_group = QGroupBox("2. Add Song Links (Ultimate Guitar)")
        input_layout = QVBoxLayout()
        
        link_layout = QHBoxLayout()
        self.line_url = QLineEdit()
        self.line_url.setPlaceholderText("https://tabs.ultimate-guitar.com/tab/...")
        self.btn_add_url = QPushButton("🔗 Add Link")
        self.btn_add_url.clicked.connect(self.add_url)
        link_layout.addWidget(self.line_url)
        link_layout.addWidget(self.btn_add_url)
        
        self.list_files = QListWidget()
        self.btn_clear_files = QPushButton("🗑 Clear List")
        self.btn_clear_files.clicked.connect(self.clear_files)
        
        input_layout.addLayout(link_layout)
        input_layout.addWidget(self.list_files)
        input_layout.addWidget(self.btn_clear_files)
        input_group.setLayout(input_layout)
        main_layout.addWidget(input_group)

        # 3. OUTPUT SECTION
        output_group = QGroupBox("3. Destination Folder")
        output_layout = QHBoxLayout()
        self.line_output_dir = QLineEdit()
        self.line_output_dir.setPlaceholderText("Select the folder for .pro files...")
        self.line_output_dir.setReadOnly(True)
        self.btn_browse_output = QPushButton("Browse...")
        self.btn_browse_output.clicked.connect(self.browse_output_dir)
        output_layout.addWidget(self.line_output_dir)
        output_layout.addWidget(self.btn_browse_output)
        output_group.setLayout(output_layout)
        main_layout.addWidget(output_group)

        # 4. START BUTTON
        self.btn_start = QPushButton("▶ START CONVERSION")
        self.btn_start.clicked.connect(self.toggle_processing)
        main_layout.addWidget(self.btn_start)

        # 5. CONSOLE
        log_group = QGroupBox("System Console")
        log_layout = QVBoxLayout()
        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        log_layout.addWidget(self.text_log)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        self.load_settings()
        self.apply_theme("Modern Dark")

    def apply_theme(self, theme_data):
        if isinstance(theme_data, str):
            colors = THEMES.get(theme_data, THEMES["Modern Dark"])
        else:
            colors = theme_data

        qss = f"""
        QMainWindow, QWidget {{ background-color: {colors['primary']}; color: {colors['text']}; }}
        QGroupBox {{ background-color: {colors['secondary']}; border: 1px solid {colors['border']}; margin-top: 10px; font-weight: bold; padding-top: 15px; }}
        QPushButton {{ background-color: {colors['button']}; color: {colors['text']}; border: 1px solid {colors['border']}; padding: 6px; border-radius: 4px; }}
        QPushButton:hover {{ background-color: {colors['accent']}; color: white; }}
        QLineEdit, QComboBox, QTextEdit, QListWidget {{ background-color: {colors['card']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 3px; padding: 4px; }}
        """
        self.setStyleSheet(qss)
        self.btn_start.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 10px; font-size: 14px; border-radius: 4px;")
        self.text_log.setStyleSheet(f"background-color: {colors['card']}; color: {colors.get('accent', '#4af626')}; font-family: Consolas, monospace; border: 1px solid {colors['border']};")

    def load_settings(self):
        if os.path.exists(self.config_file):
            self.config.read(self.config_file)
            if 'Settings' in self.config:
                api_key = self.config['Settings'].get('api_key', '')
                output_dir = self.config['Settings'].get('output_dir', '')
                self.line_api_key.setText(api_key)
                self.line_output_dir.setText(output_dir)

    def save_settings(self):
        if 'Settings' not in self.config:
            self.config['Settings'] = {}
            
        self.config['Settings']['api_key'] = self.line_api_key.text().strip()
        self.config['Settings']['output_dir'] = self.line_output_dir.text().strip()
        
        with open(self.config_file, 'w') as configfile:
            self.config.write(configfile)

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
        dir_path = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        if dir_path:
            self.line_output_dir.setText(dir_path)

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
        api_key = self.line_api_key.text().strip()
        if not api_key:
            QMessageBox.warning(self, "Error", "Please insert your Google Gemini API Key.")
            return
            
        if self.list_files.count() == 0:
            QMessageBox.warning(self, "Error", "Add at least one Ultimate Guitar link.")
            return
            
        output_dir = self.line_output_dir.text()
        if not output_dir:
            QMessageBox.warning(self, "Error", "Select a destination folder.")
            return

        self.save_settings()

        self.set_ui_enabled(False)
        self.text_log.clear()

        self.btn_start.setText("🛑 STOP PROCESSING")
        self.btn_start.setStyleSheet("background-color: #c62828; color: white; font-weight: bold; padding: 10px; font-size: 14px; border-radius: 4px;")

        urls = [self.list_files.item(i).text() for i in range(self.list_files.count())]

        self.worker = ChordProWorker(urls, output_dir, api_key)
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished_signal.connect(self.on_processing_finished)
        self.worker.start()

    def on_processing_finished(self):
        self.set_ui_enabled(True)
        self.btn_start.setText("▶ START CONVERSION")
        self.btn_start.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 10px; font-size: 14px; border-radius: 4px;")
        
        if self.worker.is_cancelled:
            QMessageBox.information(self, "Stopped", "Process stopped by user.")
        else:
            self.append_log("\n🎉 ALL CONVERSIONS COMPLETED!")
            QMessageBox.information(self, "Completed", "The songs have been converted to ChordPro format.")

    def set_ui_enabled(self, enabled):
        self.line_api_key.setEnabled(enabled)
        self.btn_add_url.setEnabled(enabled)
        self.line_url.setEnabled(enabled)
        self.btn_clear_files.setEnabled(enabled)
        self.btn_browse_output.setEnabled(enabled)
        self.btn_start.setEnabled(True) 

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = ChordProGUI()
    window.show()
    sys.exit(app.exec())