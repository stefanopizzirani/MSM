import librosa
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

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

    def stop(self):
        self.is_cancelled = True

    def run(self):
        import os
        base_dir = os.path.join("music", "Songs")
        
        for i, title in enumerate(self.song_titles):
            if self.is_cancelled: break
            self.progress.emit(i, title)
            
            # Find audio file
            song_dir = self.finder(base_dir, title)
            target_file = None
            if song_dir and os.path.isdir(song_dir):
                files = [os.path.join(song_dir, f) for f in os.listdir(song_dir) if f.endswith(('.wav', '.mp3'))]
                for f in files:
                    if title.lower() in os.path.basename(f).lower():
                        target_file = f
                        break
                if not target_file and files:
                    target_file = files[0]
            
            if target_file:
                # Pass the genre profile to the analyzer
                bpm, key, energy = self.analyzer.analyze(target_file, profile=self.profile)
                if bpm:
                    self.data_manager.update_song_analysis(title, bpm, key, energy)
                    self.song_finished.emit(title, bpm, key, energy)
            
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
