import os
import logging
import random
import math
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem, 
                             QPushButton, QComboBox, QLabel, QHeaderView, QProgressBar, QMessageBox, QSlider)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QColor

from ui_components import EnergyFlowWidget, CompatibilityIndicator
from ui.workers import BatchAnalysisWorker
from utils import MusicMath, find_file_case_insensitive
from dialogs import SortSettingsDialog

logger = logging.getLogger("MSM2_OPTIMIZER")

class SetlistOptimizerTab(QWidget):
    def __init__(self, data_manager, parent=None):
        super().__init__(parent)
        self.data_manager = data_manager
        self.analysis_worker = None
        self.current_songs = [] # List of dicts with full metadata
        
        self.init_ui()
        self.refresh_setlist_list()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        
        # --- Top Controls ---
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("<b>Setlist:</b>"))
        self.combo_setlists = QComboBox()
        self.combo_setlists.currentTextChanged.connect(self.on_setlist_changed)
        top_layout.addWidget(self.combo_setlists, 1)
        
        self.btn_analyze = QPushButton("🔍 Analyze Audio")
        self.btn_analyze.clicked.connect(self.run_full_analysis)
        self.btn_analyze.setStyleSheet("background-color: #3584e4; color: white; font-weight: bold;")
        top_layout.addWidget(self.btn_analyze)
        
        self.btn_sort = QPushButton("🪄 Magic Sort (Flow)")
        self.btn_sort.clicked.connect(self.magic_sort)
        self.btn_sort.setStyleSheet("background-color: #28a745; color: white; font-weight: bold;")
        top_layout.addWidget(self.btn_sort)
        
        main_layout.addLayout(top_layout)
        
        # --- Progress Bar ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)
        
        # --- Tree (Replacing Table) ---
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["#", "Song Title", "Key", "Camelot", "BPM", "Energy", "Manual Adj", "Flow %"])
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents) # #
        self.tree.header().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents) # Adj
        self.tree.setIndentation(0)
        self.tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.tree.setDropIndicatorShown(True)
        # Store original dropEvent and override (like SetlistTab)
        self.original_drop_event = self.tree.dropEvent 
        self.tree.dropEvent = self._custom_drop_event
        self.tree.currentItemChanged.connect(self.on_tree_selection_changed)
        self.tree.model().rowsMoved.connect(self.on_order_changed)
        self.tree.viewport().installEventFilter(self)
        main_layout.addWidget(self.tree)
        
        # --- Energy Chart ---
        main_layout.addWidget(QLabel("<b>Energy & BPM Flow Profile:</b>"))
        self.flow_graph = EnergyFlowWidget()
        main_layout.addWidget(self.flow_graph)
        
        # --- Bottom Buttons ---
        bot_layout = QHBoxLayout()
        
        self.btn_spotify = QPushButton("🟢 Fetch Cloud Data (Spotify)")
        self.btn_spotify.clicked.connect(self.run_spotify_fetch)
        self.btn_spotify.setStyleSheet("background-color: #1DB954; color: white; font-weight: bold;")
        bot_layout.addWidget(self.btn_spotify)
        
        self.btn_save = QPushButton("Save Optimized Order")
        self.btn_save.clicked.connect(self.save_optimized_setlist)
        self.btn_save.setEnabled(False)
        bot_layout.addStretch()
        bot_layout.addWidget(self.btn_save)
        main_layout.addLayout(bot_layout)

    def _custom_drop_event(self, event):
        """Unified DND logic from SetlistTab."""
        item = self.tree.currentItem()
        if not item:
            self.original_drop_event(event)
            return

        self.original_drop_event(event)
        self.on_order_changed()

    def eventFilter(self, obj, event):
        """
        While dragging inside the optimizer tree, move graph highlight dynamically
        to the row currently hovered by the drag cursor.
        """
        if obj is self.tree.viewport() and event.type() == QEvent.Type.DragMove:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            hover_item = self.tree.itemAt(pos)
            if hover_item:
                idx = self.tree.indexOfTopLevelItem(hover_item)
                self.flow_graph.set_selected_index(idx)
        return super().eventFilter(obj, event)

    def on_order_changed(self, *args):
        """Called when a user manually drags/drops a row."""
        new_order = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            # Re-read Metadata from UserRole on column 1
            song_data = item.data(1, Qt.ItemDataRole.UserRole)
            if song_data:
                # Update current_songs with latest manual energy & Cloud from DB
                db_data = self.data_manager.database.get(song_data['title'], {})
                song_data['manual_energy'] = db_data.get('manual_energy')
                song_data['spotify_data'] = db_data.get('spotify_data')
                new_order.append(song_data)
        
        self.current_songs = new_order
        self.refresh_table_sync()
        self.on_tree_selection_changed(self.tree.currentItem(), None)
        self.btn_save.setEnabled(True)

    def on_tree_selection_changed(self, current, previous):
        """Highlights the selected song point in the flow graph."""
        if not current:
            self.flow_graph.set_selected_index(-1)
            return
        idx = self.tree.indexOfTopLevelItem(current)
        self.flow_graph.set_selected_index(idx)

    def on_manual_energy_changed(self, title, val):
        """Slider Callback: Updates DB and refreshes Flow Graph immediately."""
        effective_val = val / 10.0 # Scale slider 10-100 to 1.0-10.0
        self.data_manager.update_song_analysis(title, manual_energy=effective_val)
        
        # Sync the manual energy into current_songs list
        for s in self.current_songs:
            if s['title'] == title:
                s['manual_energy'] = effective_val
                break
        
        self.refresh_table_sync()
        self.btn_save.setEnabled(True)

    def refresh_table_sync(self):
        """Updates indices, indicators and graph without deleting items."""
        graph_data = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            song = self.current_songs[i]
            
            # Re-fetch latest from DB (which already resolves Manual > Cloud > AI)
            analysis = self.data_manager.get_song_analysis(song['title'])
            effective_energy = analysis['energy']
            local_ai = analysis['energy_ai_baseline']
            
            item.setText(0, str(i+1))
            item.setText(5, f"{float(effective_energy):.2f}")
            
            # Ensure metadata is current (for next drag)
            item.setData(1, Qt.ItemDataRole.UserRole, song)
            
            # --- Slider (Manual Adj) ---
            if not self.tree.itemWidget(item, 6):
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(10, 100) # 1.0 to 10.0
                slider.setValue(int(effective_energy * 10))
                slider.setFixedWidth(80)
                # Capture title via lambda
                title = song['title']
                slider.valueChanged.connect(lambda v, t=title: self.on_manual_energy_changed(t, v))
                self.tree.setItemWidget(item, 6, slider)
            
            # Update Compatibility Indicator (Column 7)
            score = 0
            if i > 0:
                prev_cam = self.current_songs[i-1].get('camelot')
                curr_cam = song.get('camelot')
                score = MusicMath.get_compatibility(prev_cam, curr_cam)
            
            indicator = CompatibilityIndicator(score)
            self.tree.setItemWidget(item, 7, indicator)
            
            # Pack data for Flow Graph (including Ghost AI)
            graph_data.append({
                'bpm': int(round(float(song['bpm']))),
                'energy': effective_energy,
                'energy_ai': local_ai if effective_energy != local_ai else None
            })
            
        self.flow_graph.set_data(graph_data)
        self.on_tree_selection_changed(self.tree.currentItem(), None)

    def refresh_setlist_list(self):
        """Populates the combo box with available CSV setlists."""
        self.combo_setlists.blockSignals(True)
        self.combo_setlists.clear()
        csv_files = self.data_manager.get_all_csv_files()
        self.combo_setlists.addItems(csv_files)
        self.combo_setlists.blockSignals(False)
        if csv_files:
            self.on_setlist_changed(csv_files[0])

    def sync_with_setlist(self, csv_name):
        """
        Refreshes optimizer setlist sources and reloads a specific setlist if available.
        Keeps optimizer in sync when Setlist tab edits/removes songs.
        """
        csv_files = self.data_manager.get_all_csv_files()
        self.combo_setlists.blockSignals(True)
        self.combo_setlists.clear()
        self.combo_setlists.addItems(csv_files)
        self.combo_setlists.blockSignals(False)

        if not csv_files:
            self.tree.clear()
            self.current_songs = []
            self.flow_graph.set_data([])
            self.btn_save.setEnabled(False)
            return

        target_csv = csv_name if csv_name in csv_files else csv_files[0]
        self.combo_setlists.setCurrentText(target_csv)
        self.on_setlist_changed(target_csv)

    def on_setlist_changed(self, csv_name):
        if not csv_name: return
        titles = self.data_manager.load_setlist(csv_name)
        self.load_song_metadata(titles)
        self.refresh_table()
        self.btn_save.setEnabled(False)

    def load_song_metadata(self, titles):
        """Fetches metadata from DataManager for the given titles."""
        self.current_songs = []
        for i, title in enumerate(titles):
            data = self.data_manager.get_song_analysis(title)
            data['title'] = title
            data['camelot'] = MusicMath.get_camelot(data.get('key'))
            # Ensure manual_energy and cloud data are captured
            db_data = self.data_manager.database.get(title, {})
            data['manual_energy'] = db_data.get('manual_energy')
            data['spotify_data'] = db_data.get('spotify_data')
            self.current_songs.append(data)

    def refresh_table(self):
        self.tree.clear()
        
        for i, song in enumerate(self.current_songs):
            # Fetch latest scores via centralized logic
            analysis = self.data_manager.get_song_analysis(song['title'])
            effective_energy = analysis['energy']

            item = QTreeWidgetItem([
                str(i+1),
                song['title'],
                song.get('key', '?'),
                song.get('camelot', '?'),
                f"{int(round(float(song['bpm'])))}",
                f"{float(effective_energy):.2f}",
                "", # Slider Adj
                ""  # Flow % Indicator
            ])
            
            # Store metadata for DND reordering on Title column
            item.setData(1, Qt.ItemDataRole.UserRole, song)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            self.tree.addTopLevelItem(item)
            
        if self.tree.topLevelItemCount() > 0 and not self.tree.currentItem():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.refresh_table_sync()

    def run_spotify_fetch(self):
        """Starts batch cloud fetch from Spotify."""
        titles = [s['title'] for s in self.current_songs]
        if not titles: return
        
        self.btn_spotify.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(titles))
        self.progress_bar.setValue(0)
        
        from ui.workers import SpotifyAnalysisWorker
        self.analysis_worker = SpotifyAnalysisWorker(self.data_manager, titles)
        self.analysis_worker.progress.connect(self.on_analysis_progress)
        self.analysis_worker.song_finished.connect(self.on_song_analyzed)
        self.analysis_worker.finished.connect(self.on_analysis_finished)
        self.analysis_worker.start()

    def run_full_analysis(self):
        """Starts batch analysis for all songs in the current set."""
        titles = [s['title'] for s in self.current_songs]
        if not titles: return
        
        self.btn_analyze.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(titles))
        self.progress_bar.setValue(0)
        
        self.analysis_worker = BatchAnalysisWorker(self.data_manager, titles)
        self.analysis_worker.progress.connect(self.on_analysis_progress)
        self.analysis_worker.song_finished.connect(self.on_song_analyzed)
        self.analysis_worker.finished.connect(self.on_analysis_finished)
        self.analysis_worker.start()

    def on_analysis_progress(self, idx, title):
        self.progress_bar.setValue(idx + 1)
        self.progress_bar.setFormat(f"Analyzing: {title}...")

    def on_song_analyzed(self, title, bpm, key, energy):
        # Update local list
        for s in self.current_songs:
            if s['title'] == title:
                s['bpm'] = int(round(float(bpm)))
                s['key'] = key
                s['camelot'] = MusicMath.get_camelot(key)
                s['energy'] = round(float(energy), 2)
                break
        self.refresh_table()

    def on_analysis_finished(self):
        self.progress_bar.setVisible(False)
        self.btn_analyze.setEnabled(True)
        self.btn_spotify.setEnabled(True)
        self.refresh_table()
        QMessageBox.information(self, "Analysis Complete", "All data refreshed and cached.")

    def magic_sort(self):
        """
        Calculates the smoothest setlist flow based on user-selected criteria.
        """
        if not self.current_songs: return
        
        # Load saved settings
        saved_settings = self.data_manager.get_optimizer_settings()
        
        dlg = SortSettingsDialog(saved_settings, self)
        if not dlg.exec(): return
        
        settings = dlg.get_settings()
        self.data_manager.save_optimizer_settings(settings)

        mode = settings["mode"]
        h_weight = settings["harmonic_weight"]
        lock_first = settings["lock_first"]
        
        # --- MODE 1: Strict Build Modes (Global Gradient) ---
        if mode in ["Energy Build", "BPM Build"]:
            if mode == "BPM Build":
                sorted_songs = sorted(self.current_songs, key=lambda x: x['bpm'])
            else:
                sorted_songs = sorted(self.current_songs, key=lambda x: x['energy'])

            # Multi-pass polishing
            max_e_drop = h_weight * 2.5 
            max_b_drop = h_weight * 25.0

            if lock_first:
                first_title = self.current_songs[0]['title']
                for i, s in enumerate(sorted_songs):
                    if s['title'] == first_title:
                        sorted_songs.insert(0, sorted_songs.pop(i))
                        break

            for _ in range(3):
                start_idx = 1 if lock_first else 0
                for i in range(start_idx, len(sorted_songs) - 2):
                    s1, s2, s3 = sorted_songs[i], sorted_songs[i+1], sorted_songs[i+2]
                    curr_h = MusicMath.get_compatibility(s1.get('camelot'), s2.get('camelot')) + \
                             MusicMath.get_compatibility(s2.get('camelot'), s3.get('camelot'))
                    swap_h = MusicMath.get_compatibility(s1.get('camelot'), s3.get('camelot')) + \
                             MusicMath.get_compatibility(s3.get('camelot'), s2.get('camelot'))
                    
                    if swap_h > curr_h:
                        if mode == "BPM Build":
                            if s3['bpm'] >= s1['bpm'] - max_b_drop:
                                 sorted_songs[i+1], sorted_songs[i+2] = sorted_songs[i+2], sorted_songs[i+1]
                        else:
                            if s3['energy'] >= s1['energy'] - max_e_drop:
                                 sorted_songs[i+1], sorted_songs[i+2] = sorted_songs[i+2], sorted_songs[i+1]
            self.current_songs = sorted_songs

        # --- MODE 2: Musical/Custom Modes ---
        else:
            if settings.get("use_global_opt", True):
                self.current_songs = self._global_sort(mode, h_weight, lock_first)
            else:
                self.current_songs = self._greedy_sort(mode, h_weight, lock_first)

        self.refresh_table()
        self.btn_save.setEnabled(True)

    def _greedy_sort(self, mode, h_weight, lock_first):
        """
        Greedy sequence finder with 'Directional Gravity'.
        Finds the most musical path that still maintains an upward energy trend.
        """
        f_weight = 1.0 - h_weight
        all_possible_sequences = []
        start_indices = [0] if lock_first else range(len(self.current_songs))
        
        for start_idx in start_indices:
            unplaced = self.current_songs.copy()
            seq = [unplaced.pop(start_idx)]
            total_seq_score = 0
            
            while unplaced:
                prev = seq[-1]
                best_next_score = -10000
                best_next_idx = 0
                for i, song in enumerate(unplaced):
                    # 1. Harmonic Compatibility
                    h_score = MusicMath.get_compatibility(prev.get('camelot'), song.get('camelot'))
                    
                    # 2. Flow Score with Build Bias
                    diff = song['energy'] - prev['energy']
                    if diff >= 0:
                        flow_score = 100 - (diff * 5)
                    else:
                        # Heavy penalty for dropping energy in any mode
                        flow_score = 100 - (abs(diff) * 40)
                    
                    trans_score = (h_score * h_weight) + (flow_score * f_weight)
                    
                    if trans_score > best_next_score:
                        best_next_score = trans_score
                        best_next_idx = i
                        
                total_seq_score += best_next_score
                seq.append(unplaced.pop(best_next_idx))
            
            # --- Directional Gravity ---
            # Bonus points for finishing at a higher energy than we started.
            # This prevents the '10 to 0' inverted flow from winning overall.
            energy_gain = seq[-1]['energy'] - seq[0]['energy']
            total_seq_score += (energy_gain * 200) # Heavy bonus/penalty for build direction
                
            all_possible_sequences.append((total_seq_score, seq))
            
        all_possible_sequences.sort(key=lambda x: x[0], reverse=True)
        return all_possible_sequences[0][1]

    def _calculate_transition_score(self, prev, curr, mode, h_weight):
        """Calculates a score (0-100) for the transition between two songs."""
        f_weight = 1.0 - h_weight
        
        # 1. Harmonic Compatibility
        h_score = MusicMath.get_compatibility(prev.get('camelot'), curr.get('camelot'))
        
        # 2. Flow Score (Energy/BPM)
        if mode == "BPM Build":
            diff = curr['bpm'] - prev['bpm']
            # BPM jumps are more sensitive
            if diff >= 0:
                flow_score = 100 - (diff * 2) 
            else:
                flow_score = 100 - (abs(diff) * 15)
        else:
            # Energy Mode
            diff = curr['energy'] - prev['energy']
            if diff >= 0:
                flow_score = 100 - (diff * 5)
            else:
                flow_score = 100 - (abs(diff) * 40)
        
        return (h_score * h_weight) + (flow_score * f_weight)

    def _calculate_sequence_score(self, sequence, mode, h_weight):
        """Evaluates the total score of a sequence."""
        if not sequence: return -10000
        
        total_score = 0
        for i in range(len(sequence) - 1):
            total_score += self._calculate_transition_score(sequence[i], sequence[i+1], mode, h_weight)
            
        # Directional Gravity Bonus
        energy_gain = sequence[-1]['energy'] - sequence[0]['energy']
        total_score += (energy_gain * 200)
        
        return total_score

    def _global_sort(self, mode, h_weight, lock_first):
        """
        Global optimization using Simulated Annealing.
        Finds the best overall sequence by permuting and cooling.
        """
        # 1. Start with a decent baseline (Greedy)
        current_seq = self._greedy_sort(mode, h_weight, lock_first)
        current_score = self._calculate_sequence_score(current_seq, mode, h_weight)
        
        best_seq = current_seq.copy()
        best_score = current_score
        
        # 2. Annealing Parameters
        temp = 100.0
        cooling_rate = 0.9995
        min_temp = 0.01
        iterations = 0
        
        # Fix range for swaps
        start_idx = 1 if lock_first else 0
        indices = list(range(start_idx, len(current_seq)))
        
        if len(indices) < 2: return current_seq

        while temp > min_temp and iterations < 20000:
            iterations += 1
            
            # Create neighbor by swapping two random songs
            new_seq = current_seq.copy()
            i, j = random.sample(indices, 2)
            new_seq[i], new_seq[j] = new_seq[j], new_seq[i]
            
            new_score = self._calculate_sequence_score(new_seq, mode, h_weight)
            
            # Acceptance probability
            if new_score > current_score:
                accept = True
            else:
                # Metropolis Criterion
                delta = new_score - current_score
                prob = math.exp(delta / temp)
                accept = random.random() < prob
                
            if accept:
                current_seq = new_seq
                current_score = new_score
                
                if current_score > best_score:
                    best_seq = current_seq.copy()
                    best_score = current_score
            
            temp *= cooling_rate
            
        logger.info(f"Global Optimization Finished: {iterations} iterations, Last Temp: {temp:.2f}, Score: {best_score:.2f}")
        return best_seq

    def save_optimized_setlist(self):
        csv_name = self.combo_setlists.currentText()
        if not csv_name: return
        
        titles = [s['title'] for s in self.current_songs]
        self.data_manager.save_setlist(csv_name, titles)
        self.btn_save.setEnabled(False)
        QMessageBox.information(self, "Success", f"Setlist '{csv_name}' has been updated.")
        
        # Signal SetlistTab to reload if it's currently showing this CSV
        # (This will be handled via main window later)

    def set_theme(self, colors):
        self.setStyleSheet(f"background-color: {colors['primary']}; color: {colors['text']};")
        self.tree.setStyleSheet(f"background-color: {colors['card']}; color: {colors['text']};")
        self.flow_graph.set_theme(colors)
