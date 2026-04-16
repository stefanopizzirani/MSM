import os
import logging
import librosa
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from thefuzz import process

class SongLoaderWorker(QThread):
    finished = pyqtSignal(bool)

    def __init__(self, audio_engine, song_title):
        super().__init__()
        self.audio_engine = audio_engine
        self.song_title = song_title

    def run(self):
        success = self.audio_engine.load_song(self.song_title)
        self.finished.emit(success)

class BPMWorker(QThread):
    finished = pyqtSignal(float, str, str)  
    error = pyqtSignal(str)

    def __init__(self, target_file, source_type, song_title):
        super().__init__()
        self.target_file = target_file
        self.source_type = source_type
        self.song_title = song_title

    def run(self):
        try:
            # OPTIMIZATION: Force a low sample rate (11025Hz). 
            # This cuts memory usage in half and doubles processing speed!
            TARGET_SR = 11025 

            y, sr = librosa.load(self.target_file, sr=TARGET_SR, duration=60.0)
            tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
            rms_energy = np.mean(librosa.feature.rms(y=y))
            
            if len(beats) < 30 or rms_energy < 0.01:
                # Full file load, but blazing fast because of low sample rate
                y_full, sr_full = librosa.load(self.target_file, sr=TARGET_SR, duration=None) 
                tempo, _ = librosa.beat.beat_track(y=y_full, sr=sr_full)
            
            bpm_value = float(tempo[0]) if hasattr(tempo, '__len__') else float(tempo)
            self.finished.emit(bpm_value, self.song_title, self.source_type)
            
        except Exception as e:
            self.error.emit(str(e))

class BatchAnalysisWorker(QThread):
    """
    Worker that iterates through a list of songs and performs full analysis.
    """
    progress = pyqtSignal(int, str) # current_idx, song_title
    song_finished = pyqtSignal(str, float, str, float) # title, bpm, key, energy
    finished = pyqtSignal()

    def __init__(self, data_manager, song_titles):
        super().__init__()
        self.data_manager = data_manager
        self.song_titles = song_titles
        self.is_cancelled = False
        from utils import SongAnalyzer, find_file_case_insensitive
        self.analyzer = SongAnalyzer
        self.finder = find_file_case_insensitive
        
        # Load profile from config
        self.profile = self.data_manager.config.get("analysis_profile", "Balanced")

    def _get_supported_extensions(self):
        return ('.wav', '.mp3', '.ogg', '.flac', '.aif', '.aiff')

    def stop(self):
        self.is_cancelled = True

    def run(self):
        base_dir = os.path.join("music", "Songs")
        
        for i, title in enumerate(self.song_titles):
            if self.is_cancelled: break
            self.progress.emit(i, title)
            
            # Find audio file
            song_dir = self.finder(base_dir, title)
            target_file = None
            if song_dir and os.path.isdir(song_dir):
                all_files = [os.path.join(song_dir, f) for f in os.listdir(song_dir) 
                             if f.lower().endswith(self._get_supported_extensions())]
                
                if all_files:
                    # Use fuzzy match to find the best filename match (e.g. handle minor punctuation differences)
                    choices = {os.path.splitext(os.path.basename(f))[0]: f for f in all_files}
                    best_match = process.extractOne(title, list(choices.keys()))
                    
                    if best_match and best_match[1] >= 80:
                        target_file = choices[best_match[0]]
                    else:
                        # Try to find a drum stem
                        drums = [f for f in all_files if 'drum' in os.path.basename(f).lower()]
                        target_file = drums[0] if drums else all_files[0]
            
            if target_file:
                try:
                    # Pass the genre profile to the analyzer
                    bpm, key, energy = self.analyzer.analyze(target_file, profile=self.profile)
                    if bpm:
                        self.data_manager.update_song_analysis(title, bpm, key, energy)
                        self.song_finished.emit(title, bpm, key, energy)
                        continue
                except Exception as e:
                    logging.error(f"Analysis failed for {title}: {e}")
            
            # If we reach here, analysis failed or file was missing
            # Re-emit current data so the UI refreshes/unblocks
            analysis = self.data_manager.get_song_analysis(title)
            self.song_finished.emit(title, analysis['bpm'], analysis['key'], analysis['energy'])
            
        self.finished.emit()

class SpotifyAnalysisWorker(QThread):
    """
    Worker that fetches metadata from Spotify for a list of songs.
    """
    progress = pyqtSignal(int, str)
    song_finished = pyqtSignal(str, float, str, float)
    finished = pyqtSignal()

    def __init__(self, data_manager, song_titles):
        super().__init__()
        self.data_manager = data_manager
        self.song_titles = song_titles
        self.is_cancelled = False
        
        from utils import SpotifyAnalyzer
        config = self.data_manager.config
        self.analyzer = SpotifyAnalyzer(
            config.get("spotify_client_id"),
            config.get("spotify_client_secret")
        )

    def stop(self):
        self.is_cancelled = True

    def run(self):
        for i, title in enumerate(self.song_titles):
            if self.is_cancelled: break
            self.progress.emit(i, title)
            
            features = self.analyzer.fetch_features(title)
            if features:
                bpm, key, energy = features
                # Store as spotify_data to preserve AI/Local score
                self.data_manager.update_song_analysis(title, spotify_data={'bpm': bpm, 'key': key, 'energy': energy})
                self.song_finished.emit(title, bpm, key, energy)
                
        self.finished.emit()
