import math
import os
import librosa
import numpy as np
from PyQt6.QtWidgets import QSlider, QDial, QStyle, QStyleOptionSlider, QWidget
from PyQt6.QtCore import Qt, QRectF, QPointF, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QFont

class ClickableSlider(QSlider):
    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            val = self.pixelPosToRangeValue(event.pos())
            self.setValue(val)
            self.sliderPressed.emit()
            self.sliderMoved.emit(val)
            self.sliderReleased.emit()

    def pixelPosToRangeValue(self, pos):
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        gr = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
        sr = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self)

        if self.orientation() == Qt.Orientation.Horizontal:
            sliderLength = sr.width()
            sliderMin = gr.x()
            sliderMax = gr.right() - sliderLength + 1
            return self.style().sliderValueFromPosition(self.minimum(), self.maximum(), pos.x() - sliderMin, sliderMax - sliderMin, opt.upsideDown)
        else:
            sliderLength = sr.height()
            sliderMin = gr.y()
            sliderMax = gr.bottom() - sliderLength + 1
            return self.style().sliderValueFromPosition(self.minimum(), self.maximum(), pos.y() - sliderMin, sliderMax - sliderMin, opt.upsideDown)

class SmoothDial(QDial):
    def __init__(self, parent=None, theme_colors=None):
        super().__init__(parent)
        self.setMinimumSize(60, 60)
        
        # Base colors (will be overridden by theme)
        self.bg_color = QColor("#2b2b2b")         
        self.track_color = QColor("#1a1a1a")      
        self.accent_color = QColor("#3498db")  
        self.indicator_color = QColor("#ffffff")  

        if theme_colors:
            self.set_theme(theme_colors)

    def set_theme(self, theme_colors):
        """Updates colors based on theme dictionary and redraws."""
        self.bg_color = QColor(theme_colors.get("secondary", "#242424"))
        self.track_color = QColor(theme_colors.get("primary", "#1a1a1a"))
        self.accent_color = QColor(theme_colors.get("accent", "#3498db"))
        self.indicator_color = QColor(theme_colors.get("text", "#ffffff"))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()
        size = min(width, height) - 10 
        
        rect = QRectF((width - size) / 2, (height - size) / 2, size, size)
        radius = size / 2
        center = rect.center()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self.bg_color))
        painter.drawEllipse(rect)

        min_val = self.minimum()
        max_val = self.maximum()
        val = self.value()
        
        if max_val == min_val:
            span = 0
        else:
            span = (val - min_val) / (max_val - min_val)

        start_angle = 225
        total_angle = -270 

        pen_width = 3
        pen_track = QPen(self.track_color, pen_width)
        pen_track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_track)
        painter.drawArc(int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()), 
                        start_angle * 16, total_angle * 16)

        active_angle = total_angle * span
        pen_accent = QPen(self.accent_color, pen_width)
        pen_accent.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_accent)
        painter.drawArc(int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()), 
                        start_angle * 16, int(active_angle * 16))

        current_angle_deg = start_angle + active_angle
        current_angle_rad = math.radians(current_angle_deg)
        ind_radius = radius - 5 
        ind_x = center.x() + ind_radius * math.cos(current_angle_rad)
        ind_y = center.y() - ind_radius * math.sin(current_angle_rad) 

        painter.setBrush(QBrush(self.indicator_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(ind_x, ind_y), 2, 2)
        painter.end()

class WaveformWorker(QThread):
    finished = pyqtSignal(np.ndarray, float) # data, duration
    
    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path
        
    def run(self):
        try:
            # Load signed signal at 4kHz
            sr_target = 4000
            y, sr = librosa.load(self.file_path, sr=sr_target)
            duration = len(y) / sr_target
            self.finished.emit(y, duration)
        except Exception as e:
            print(f"Waveform loading error: {e}")

class WaveformWidget(QWidget):
    seek_requested = pyqtSignal(float)
    loop_requested = pyqtSignal(float, float) # start_pct, end_pct
    loop_cleared = pyqtSignal()
    marker_added = pyqtSignal(float, str)
    marker_moved = pyqtSignal(int, float) # index, new_pct
    marker_deleted = pyqtSignal(int)      # index
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(100) # Increased height for time markers
        self.waveform_data = None
        self.markers = [] # List of {'pct': float, 'label': str}
        self.progress = 0.0
        self.duration = 0.0
        self.bg_color = QColor("#1e1e1e")
        self.wave_color = QColor("#3584e4")
        self.playhead_color = QColor("#ffffff")
        
        # State for Zoom and Scroll
        self.zoom_level = 1.0  # 1.0 = fit to width
        self.view_offset = 0.0 # Pixel offset from the start
        
        # State for Loop Selection
        self.selection_start = None
        self.selection_end = None
        self.is_selecting = False
        self.dragging_marker_idx = -1
        
        self.worker = None
        self.zombie_workers = []
        
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_theme(self, theme_colors):
        self.bg_color = QColor(theme_colors.get("secondary", "#242424"))
        self.wave_color = QColor(theme_colors.get("accent", "#3498db"))
        self.playhead_color = QColor(theme_colors.get("text", "#ffffff"))
        self.update()

    def stop(self):
        if self.worker:
            try: self.worker.finished.disconnect()
            except Exception: pass
            self.zombie_workers.append(self.worker)
            self.worker = None
            self.zombie_workers = [w for w in self.zombie_workers if w.isRunning()]

    def set_audio_file(self, file_path):
        if not file_path or not os.path.exists(file_path):
            self.waveform_data = None
            self.duration = 0.0
            self.stop()
            self.update()
            return

        self.stop()
        self.selection_start = None
        self.selection_end = None
        self.markers = []
        self.zoom_level = 1.0
        self.view_offset = 0.0
            
        self.worker = WaveformWorker(file_path)
        self.worker.finished.connect(self.on_waveform_ready)
        self.worker.start()

    def on_waveform_ready(self, data, duration):
        # Significantly increased resolution for zoomed views (200k points)
        self.duration = duration
        target_points = 200000 
        if len(data) > target_points:
            step = len(data) // target_points
            data = data[::step]
        if len(data) > 0:
            max_val = np.max(np.abs(data))
            if max_val > 0: data = data / max_val
        self.waveform_data = data
        self.update()

    def set_duration(self, duration):
        """Sets total song duration in seconds."""
        self.duration = duration
        self.update()

    def set_progress(self, progress):
        self.progress = max(0.0, min(1.0, progress))
        
        # AUTO-SCROLL: Keep playhead in view when zoomed
        if self.zoom_level > 1.0:
            full_width = self.width() * self.zoom_level
            playhead_x = self.progress * full_width
            
            # If playhead is outside the current view, shift the view_offset
            margin = self.width() * 0.2
            if playhead_x < self.view_offset + margin:
                self.view_offset = max(0, playhead_x - margin)
            elif playhead_x > self.view_offset + self.width() - margin:
                self.view_offset = min(full_width - self.width(), playhead_x - self.width() + margin)
                
        self.update()

    def wheelEvent(self, event):
        """Zoom in/out around the mouse cursor."""
        cursor_x = event.position().x()
        old_zoom = self.zoom_level
        
        # Calculate cursor position in normalized audio time
        full_width_old = self.width() * old_zoom
        progress_at_cursor = (self.view_offset + cursor_x) / full_width_old
        
        zoom_factor = 1.2
        if event.angleDelta().y() > 0:
            self.zoom_level = min(50.0, self.zoom_level * zoom_factor)
        else:
            self.zoom_level = max(1.0, self.zoom_level / zoom_factor)
            
        # Re-calculate view_offset to keep progress_at_cursor under the cursor
        full_width_new = self.width() * self.zoom_level
        self.view_offset = (progress_at_cursor * full_width_new) - cursor_x
        
        # Clamp offset
        self.view_offset = max(0, min(full_width_new - self.width(), self.view_offset))
        self.update()

    def paintEvent(self, event):
        painter = QPainter()
        if not painter.begin(self): return
        painter.fillRect(self.rect(), self.bg_color)
        
        width = self.width()
        height = self.height()
        wave_height = 60 # Keep waveform area at 60px
        mid_y = wave_height / 2
        
        full_drawn_width = width * self.zoom_level
        
        if self.waveform_data is not None and len(self.waveform_data) > 0:
            data_len = len(self.waveform_data)
            
            # CYan -> Purple -> Pink palette
            c1, c2, c3 = QColor("#00f0ff"), QColor("#7000ff"), QColor("#ff00ff")
            
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            
            # Determine range of indices to draw
            painter.setPen(QPen(Qt.GlobalColor.white, 1)) # Default fall-through pen
            
            for x_screen in range(width):
                x_full = self.view_offset + x_screen
                # Calculate the exact data index for this screen pixel
                idx = int(x_full * data_len / full_drawn_width)
                
                if 0 <= idx < data_len:
                    # Single-point lookup for a more 'jagged' and dynamic look
                    raw_val = self.waveform_data[idx]
                    val = abs(raw_val) # Use absolute for color/scaling logic
                    
                    if val < 0.5:
                        t = val * 2
                        r = int(c1.red() + (c2.red() - c1.red()) * t)
                        g = int(c1.green() + (c2.green() - c1.green()) * t)
                        b = int(c1.blue() + (c2.blue() - c1.blue()) * t)
                    else:
                        t = (val - 0.5) * 2
                        r = int(c2.red() + (c3.red() - c2.red()) * t)
                        g = int(c2.green() + (c3.green() - c2.green()) * t)
                        b = int(c2.blue() + (c3.blue() - c2.blue()) * t)
                    
                    # Thinner pen (1px) for more precision and less 'fullness'
                    painter.setPen(QPen(QColor(r, g, b), 1))
                    
                    # Draw a balanced mirrored line based on the sample displacement
                    draw_h = val * mid_y * 0.9
                    painter.drawLine(x_screen, int(mid_y - draw_h), x_screen, int(mid_y + draw_h))
            
            # Loop Selection Shading
            if self.selection_start is not None and self.selection_end is not None:
                s1 = min(self.selection_start, self.selection_end)
                s2 = max(self.selection_start, self.selection_end)
                
                # Convert normalized selection to pixels
                sel_px_start = (s1 * full_drawn_width) - self.view_offset
                sel_px_end = (s2 * full_drawn_width) - self.view_offset
                
                if sel_px_end > 0 and sel_px_start < width:
                    sh_start = max(0, int(sel_px_start))
                    sh_end = min(width, int(sel_px_end))
                    painter.fillRect(sh_start, 0, sh_end - sh_start, wave_height, QColor(255, 255, 255, 40))
                    painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
                    painter.drawLine(int(sel_px_start), 0, int(sel_px_start), wave_height)
                    painter.drawLine(int(sel_px_end), 0, int(sel_px_end), wave_height)

            # --- SONG MARKERS ---
            painter.setFont(QFont("Inter", 9, QFont.Weight.Bold))
            for m in self.markers:
                m_x = (m['pct'] * full_drawn_width) - self.view_offset
                if 0 <= m_x <= width:
                    painter.setPen(QPen(QColor("#ff9900"), 2))
                    painter.drawLine(int(m_x), 0, int(m_x), wave_height)
                    
                    # Label with background for readability
                    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QBrush(QColor(0,0,0,150)))
                    painter.drawRect(int(m_x)+2, 2, 65, 18)
                    painter.setPen(QColor("#ffffff"))
                    painter.drawText(int(m_x)+5, 15, m['label'][:10])

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        
        # Draw central white line (waveform area)
        painter.setPen(QPen(QColor(255, 255, 255, 100), 1))
        painter.drawLine(0, int(mid_y), width, int(mid_y))

        # --- TIME MARKERS & TICKS ---
        if self.duration > 0:
            # Decide on an interval based on pixels per second
            px_per_sec = full_drawn_width / self.duration
            
            # Possible intervals: 1s, 2s, 5s, 10s, 30s, 1m, 2m, 5m
            options = [1, 2, 5, 10, 30, 60, 120, 300]
            interval = options[-1]
            for opt in options:
                if (opt * px_per_sec) > 80: # Target 80px space between labels
                    interval = opt
                    break
            
            painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
            painter.setFont(QFont("Inter", 8))
            
            # Start time to draw
            start_sec = max(0, int(self.view_offset / px_per_sec))
            end_sec = min(self.duration, (self.view_offset + width) / px_per_sec)
            
            # Round start_sec down to interval
            first_tick = (int(start_sec) // interval) * interval
            
            for t in range(int(first_tick), int(end_sec) + interval, interval):
                x_pos = (t * px_per_sec) - self.view_offset
                if -10 <= x_pos <= width + 10:
                    # Tick
                    painter.drawLine(int(x_pos), wave_height, int(x_pos), wave_height + 5)
                    # Text
                    m, s = divmod(t, 60)
                    time_str = f"{m:02d}:{s:02d}"
                    painter.drawText(int(x_pos) - 15, wave_height + 18, 40, 20, Qt.AlignmentFlag.AlignHCenter, time_str)

        # Playhead
        playhead_x = (self.progress * full_drawn_width) - self.view_offset
        if 0 <= playhead_x <= width:
            painter.setPen(QPen(QColor(255, 255, 255, 255), 2))
            painter.drawLine(int(playhead_x), 0, int(playhead_x), wave_height + 10)
        
        painter.end()

    def mousePressEvent(self, event):
        full_width = self.width() * self.zoom_level
        mouse_x = event.position().x()
        mouse_y = event.position().y()
        clicked_pct = (self.view_offset + mouse_x) / full_width

        # Check for marker interaction (hit zone of the label)
        for i, m in enumerate(self.markers):
            m_x = (m['pct'] * full_width) - self.view_offset
            # Hit zone matches paintEvent: (m_x+2, 2, 65, 18)
            if m_x + 2 <= mouse_x <= m_x + 67 and 2 <= mouse_y <= 20:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.dragging_marker_idx = i
                    return
                elif event.button() == Qt.MouseButton.RightButton:
                    self._show_marker_menu(i, event.globalPosition().toPoint())
                    return

        if event.button() == Qt.MouseButton.LeftButton:
            self.seek_requested.emit(clicked_pct)
            self.set_progress(clicked_pct)
        elif event.button() == Qt.MouseButton.RightButton:
            self.selection_start = clicked_pct
            self.selection_end = clicked_pct
            self.is_selecting = True
            self.update()

    def mouseDoubleClickEvent(self, event):
        """Add a marker on double click."""
        if event.button() == Qt.MouseButton.LeftButton:
            full_width = self.width() * self.zoom_level
            pct = (self.view_offset + event.position().x()) / full_width
            from PyQt6.QtWidgets import QInputDialog
            label, ok = QInputDialog.getText(self, "Add Marker", "Marker Label (e.g. Chorus):")
            if ok and label:
                self.marker_added.emit(pct, label)
                self.update()

    def _show_marker_menu(self, idx, pos):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        remove_action = menu.addAction("Remove Marker")
        action = menu.exec(pos)
        if action == remove_action:
            self.marker_deleted.emit(idx)

    def mouseMoveEvent(self, event):
        full_width = self.width() * self.zoom_level
        mouse_x = event.position().x()
        hover_pct = (self.view_offset + mouse_x) / full_width

        if self.dragging_marker_idx != -1:
            new_pct = max(0.0, min(1.0, hover_pct))
            self.markers[self.dragging_marker_idx]['pct'] = new_pct
            self.marker_moved.emit(self.dragging_marker_idx, new_pct)
            self.update()
            return

        if event.buttons() & Qt.MouseButton.LeftButton:
            self.seek_requested.emit(hover_pct)
            self.set_progress(hover_pct)
        elif event.buttons() & Qt.MouseButton.RightButton and self.is_selecting:
            self.selection_end = hover_pct
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.dragging_marker_idx != -1:
            self.dragging_marker_idx = -1
            return
            
        if event.button() == Qt.MouseButton.RightButton and self.is_selecting:
            self.is_selecting = False
            
            # If the user performed a meaningful drag, set the loop. 
            # Otherwise (a single click), clear the loop.
            drag_distance_pct = abs(self.selection_end - self.selection_start)
            if drag_distance_pct < 0.005: # Slight threshold for clicks
                self.selection_start = None
                self.selection_end = None
                self.loop_cleared.emit()
            else:
                s1, s2 = sorted([self.selection_start, self.selection_end])
                self.loop_requested.emit(s1, s2)
            self.update()
class CompatibilityIndicator(QWidget):
    """
    Shows a color-coded indicator for harmonic compatibility.
    """
    def __init__(self, score=None, parent=None):
        super().__init__(parent)
        self.setFixedSize(50, 20)
        self.score = score

    def set_score(self, score):
        self.score = score
        self.update()

    def paintEvent(self, event):
        if self.score is None: return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Determine color
        if self.score >= 100: color = QColor("#28a745") # Perfect (Green)
        elif self.score >= 80: color = QColor("#ffc107") # Good (Yellow)
        else: color = QColor("#dc3545") # Clash (Red)
        
        # Draw rounded rect
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(5, 5, 40, 10)
        painter.drawRoundedRect(rect, 5, 5)
        painter.end()

class EnergyFlowWidget(QWidget):
    """
    Visualizes the BPM and Energy profile of a setlist.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.data_points = [] # List of (bpm, energy)
        self.selected_index = -1
        self.bg_color = QColor("#121212")
        self.bpm_color = QColor("#ff9900")
        self.energy_color = QColor("#00f0ff")
        self.text_color = QColor("#ffffff")
        self.font = QFont("Inter", 9)

    def set_theme(self, theme_colors):
        self.bg_color = QColor(theme_colors.get("secondary", "#1e1e1e"))
        self.bpm_color = QColor(theme_colors.get("accent", "#ff9900"))
        self.text_color = QColor(theme_colors.get("text", "#ffffff"))
        self.update()

    def set_data(self, data):
        """
        Expects list of dictionaries with:
        - 'bpm': float
        - 'energy': float (The "effective" score: manual > cloud > ai)
        - 'energy_ai': float (The original local AI baseline for ghosting)
        """
        self.data_points = data
        if self.selected_index >= len(self.data_points):
            self.selected_index = -1
        self.update()

    def set_selected_index(self, index):
        """Highlights the point at the given setlist index."""
        if 0 <= index < len(self.data_points):
            self.selected_index = index
        else:
            self.selected_index = -1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.bg_color)
        
        if not self.data_points:
            painter.setPen(self.text_color)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No analysis data available")
            return

        w, h = self.width(), self.height()
        padding = 40
        graph_w = w - (padding * 2)
        graph_h = h - (padding * 2)
        
        count = len(self.data_points)
        if count < 2: 
            painter.setPen(self.text_color)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Need at least 2 songs for flow graph")
            return

        # Find max/min for scaling
        all_bpm = [d['bpm'] for d in self.data_points]
        max_bpm = max(all_bpm)
        min_bpm = min(all_bpm)
        if max_bpm == min_bpm: max_bpm += 1
        
        # Scaling helper
        def get_pos(idx, bpm, energy, energy_ai=None):
            x = padding + (idx * graph_w / (count - 1))
            y_bpm = padding + (graph_h - ((bpm - min_bpm) * graph_h / (max_bpm - min_bpm)))
            y_energy = padding + (graph_h - (energy * graph_h / 10.0))
            y_ghost = None
            if energy_ai is not None:
                y_ghost = padding + (graph_h - (energy_ai * graph_h / 10.0))
            return QPointF(x, y_bpm), QPointF(x, y_energy), (QPointF(x, y_ghost) if y_ghost else None)

        # Draw Grid Lines
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1, Qt.PenStyle.DashLine))
        for i in range(5):
            y = padding + (i * graph_h / 4)
            painter.drawLine(padding, int(y), w - padding, int(y))

        # Build paths
        bpm_path = []
        energy_path = []
        ghost_path = []
        for i, d in enumerate(self.data_points):
            p_bpm, p_eng, p_ghost = get_pos(i, d['bpm'], d['energy'], d.get('energy_ai'))
            bpm_path.append(p_bpm)
            energy_path.append(p_eng)
            if p_ghost: ghost_path.append(p_ghost)

        # 1. Draw Ghost AI Path (if deviation exists)
        if len(ghost_path) >= 2:
            pen_ghost = QPen(QColor(255, 255, 255, 60), 1, Qt.PenStyle.DotLine)
            painter.setPen(pen_ghost)
            for i in range(len(ghost_path)-1):
                painter.drawLine(ghost_path[i], ghost_path[i+1])

        # 2. Draw Main Energy Area & Line
        poly_energy = [QPointF(padding, padding + graph_h)] + energy_path + [QPointF(padding + graph_w, padding + graph_h)]
        painter.setBrush(QBrush(QColor(0, 240, 255, 40)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(poly_energy)
        
        pen_energy = QPen(self.energy_color, 2)
        painter.setPen(pen_energy)
        for i in range(len(energy_path)-1):
            painter.drawLine(energy_path[i], energy_path[i+1])

        # 3. Draw BPM Line
        pen_bpm = QPen(self.bpm_color, 3)
        painter.setPen(pen_bpm)
        for i in range(len(bpm_path)-1):
            painter.drawLine(bpm_path[i], bpm_path[i+1])
            
        # 4. Dots
        painter.setBrush(QBrush(self.text_color))
        for p in bpm_path:
            painter.drawEllipse(p, 3, 3)

        # 5. Selected Song Highlight
        if 0 <= self.selected_index < len(bpm_path):
            p_bpm = bpm_path[self.selected_index]
            p_energy = energy_path[self.selected_index]

            # Outer halo
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 200), 2))
            painter.drawEllipse(p_bpm, 8, 8)
            painter.drawEllipse(p_energy, 8, 8)

            # Inner center marker
            painter.setBrush(QBrush(QColor(255, 255, 255)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(p_bpm, 4, 4)
            painter.drawEllipse(p_energy, 4, 4)

        # Labels
        painter.setFont(self.font)
        painter.setPen(self.bpm_color)
        painter.drawText(10, 20, f"MAX BPM: {int(max_bpm)}")
        painter.setPen(self.energy_color)
        painter.drawText(10, 40, "PRIMARY FLOW (1-10)")
        if ghost_path:
            painter.setPen(QColor(255, 255, 255, 120))
            painter.drawText(10, 60, "--- AI GHOST")
        painter.end()
