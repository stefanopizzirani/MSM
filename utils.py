import numpy as np
import os
import logging
import librosa
from pathlib import Path
from thefuzz import process

logger = logging.getLogger("MSM2_UTILS")

class MusicMath:
    """
    Helps with harmonic compatibility and Camelot Wheel conversions.
    """
    # Key to Camelot Mapping (B=Major, A=Minor)
    # Circle of Fifths order starting from 1B (B Major)
    CAMELOT_MODES = {
        'MAJOR': {
            'B': '1B', 'F#': '2B', 'Gb': '2B', 'Db': '3B', 'C#': '3B',
            'Ab': '4B', 'G#': '4B', 'Eb': '5B', 'D#': '5B', 'Bb': '6B', 'A#': '6B',
            'F': '7B', 'C': '8B', 'G': '9B', 'D': '10B', 'A': '11B', 'E': '12B'
        },
        'MINOR': {
            'G#m': '1A', 'Abm': '1A', 'D#m': '2A', 'Ebm': '2A', 'Bbm': '3A', 'A#m': '3A',
            'Fm': '4A', 'Cm': '5A', 'Gm': '6A', 'Dm': '7A', 'Am': '8A', 'Em': '9A',
            'Bm': '10A', 'F#m': '11A', 'Gbm': '11A', 'C#m': '12A', 'Dbm': '12A'
        }
    }

    @staticmethod
    def get_camelot(key_str):
        """Converts a key string (e.g. 'C Major' or 'Am') to Camelot notation ('8B' or '8A')."""
        if not key_str: return None
        
        k = key_str.strip()
        # Handle some common variations
        k = k.replace(' Major', '').replace(' major', '').replace('Maj', '').replace('maj', '')
        
        if k.endswith('m') or k.endswith(' Minor') or k.endswith(' minor') or k.endswith('min') or 'm' in k:
            # Minor
            clean_k = k.replace(' Minor', '').replace(' minor', '').replace('min', '').replace('mn', '')
            if not clean_k.endswith('m'): clean_k += 'm'
            return MusicMath.CAMELOT_MODES['MINOR'].get(clean_k)
        else:
            # Major
            return MusicMath.CAMELOT_MODES['MAJOR'].get(k)

    @staticmethod
    def get_compatibility(cam1, cam2):
        """
        Returns a compatibility score (0-100) based on Camelot Wheel distance.
        100 = Perfect (Same or Relative)
        80  = Strong (Perfect Fifth)
        0-20 = Clashing
        """
        if not cam1 or not cam2: return 0
        if cam1 == cam2: return 100
        
        try:
            num1 = int(cam1[:-1])
            mode1 = cam1[-1]
            num2 = int(cam2[:-1])
            mode2 = cam2[-1]
            
            # 1. Relative Major/Minor (8B <-> 8A)
            if num1 == num2 and mode1 != mode2:
                return 100
                
            # 2. Perfect Fifth (Shift by 1 on the wheel)
            dist = abs(num1 - num2)
            if dist == 1 or dist == 11: # Wrap around 12-1
                if mode1 == mode2:
                    return 80
        except Exception:
            return 0
                
        return 10 # Clashing

class SongAnalyzer:
    """
    Uses Librosa to extract musical features from audio files.
    """
    PROFILES = { # This is still used by SongAnalyzer, so keep it.
        'Balanced': {'loudness': 0.4, 'flatness': 0.3, 'brightness': 0.15, 'percussion': 0.15},
        'Electronic': {'loudness': 0.5, 'flatness': 0.4, 'brightness': 0.05, 'percussion': 0.05},
        'Rock': {'loudness': 0.3, 'flatness': 0.2, 'brightness': 0.3, 'percussion': 0.2},
        'Pop': {'loudness': 0.4, 'flatness': 0.2, 'brightness': 0.25, 'percussion': 0.15}
    }

    @staticmethod
    def analyze(filepath, profile='Balanced'):
        """
        Analyzes a single audio file. Returns (bpm, key, energy_score).
        """
        if not filepath or not os.path.exists(filepath):
            return None, None, 0.0
            
        try:
            # Load only first 60 seconds for speed
            y, sr = librosa.load(filepath, duration=60)
            
            # 1. BPM Detection
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            if isinstance(tempo, (list, np.ndarray)):
                tempo = tempo[0]
            bpm = float(tempo)
            
            # 2. Key Detection
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
            chroma_avg = chroma.mean(axis=1)
            key = SongAnalyzer._detect_key_from_chroma(chroma_avg)
            
            # 3. Advanced Perceptual "Power" Calculation
            w = SongAnalyzer.PROFILES.get(profile, SongAnalyzer.PROFILES['Balanced'])
            
            # A. Loudness (Base)
            rms_raw = np.mean(librosa.feature.rms(y=y))
            loudness_score = np.clip(rms_raw / 0.25, 0, 1)

            # B. Spectral Flatness (Power/Distortion/Noise density)
            flatness = np.mean(librosa.feature.spectral_flatness(y=y))
            flatness_score = np.clip(flatness * 15, 0, 1)

            # C. Spectral Centroid (Brightness/Harshness)
            centroid = np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))
            centroid_score = np.clip(centroid / 4000, 0, 1)

            # D. Zero Crossing Rate (Percussive/Noise density)
            zcr = np.mean(librosa.feature.zero_crossing_rate(y))
            zcr_score = np.clip(zcr * 8, 0, 1)

            # Weighted combination (Adjusted by Profile)
            combined_power = (loudness_score * w['loudness']) + \
                             (flatness_score * w['flatness']) + \
                             (centroid_score * w['brightness']) + \
                             (zcr_score * w['percussion'])
            
            # E. BPM Weighting (Faster = slightly more energy feeling)
            bpm_boost = np.clip((bpm - 110) / 250, 0, 0.15)
            
            energy = float(np.clip((combined_power + bpm_boost) * 10, 1.0, 10.0))
            
            return bpm, key, energy
        except Exception as e:
            logger.error(f"Analysis Failed for {filepath}: {e}")
            return None, None, 0.0

    @staticmethod
    def _detect_key_from_chroma(chroma):
        """Compares chroma to musical scale profiles."""
        major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
        notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        
        best_score = -1
        best_key = "C"

        for i in range(12):
            rotated_maj = np.roll(major_profile, i)
            rotated_min = np.roll(minor_profile, i)
            score_maj = np.corrcoef(chroma, rotated_maj)[0, 1]
            score_min = np.corrcoef(chroma, rotated_min)[0, 1]
            if score_maj > best_score:
                best_score = score_maj
                best_key = f"{notes[i]}"
            if score_min > best_score:
                best_score = score_min
                best_key = f"{notes[i]}m"
        return best_key

class SpotifyAnalyzer:
    """
    Fetches official musical features from Spotify API.
    """
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.sp = None
        
        if client_id and client_secret:
            try:
                import spotipy
                from spotipy.oauth2 import SpotifyClientCredentials
                auth_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
                self.sp = spotipy.Spotify(auth_manager=auth_manager)
            except Exception as e:
                logger.error(f"Spotify Init Failed: {e}")

    def fetch_features(self, song_title):
        """Searches for a song and returns (bpm, key, energy)."""
        if not self.sp: return None
        
        try:
            results = self.sp.search(q=song_title, limit=1, type='track')
            tracks = results.get('tracks', {}).get('items', [])
            if not tracks: return None
            
            track = tracks[0]
            track_id = track['id']
            features = self.sp.audio_features([track_id])[0]
            
            if not features: return None
            
            # Map Spotify Key (0-11) to our format
            notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
            key_idx = features['key']
            mode = features['mode'] # 1=Major, 0=Minor
            key_str = notes[key_idx] if 0 <= key_idx < 12 else "C"
            if mode == 0: key_str += "m"
            
            # Spotify Energy is 0.0 - 1.0, map to our 1.0 - 10.0
            energy = float(features['energy'] * 10.0)
            bpm = float(features['tempo'])
            
            return bpm, key_str, energy
        except Exception as e:
            logger.error(f"Spotify Search Failed for {song_title}: {e}")
            return None

def find_file_case_insensitive(directory, target_name):
    """
    Searches for a file or folder using Fuzzy matching.
    Accepts both strings and Path objects. 
    Returns the full path of the best match found as a string.
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return None

    available_options = [p.name for p in dir_path.iterdir()]
    if not available_options:
        return None

    target_lower = target_name.lower()
    for option in available_options:
        if option.lower() == target_lower:
            return str(dir_path / option)

    fuzzy_result = process.extractOne(target_name, available_options)
    if fuzzy_result:
        best_match, score = fuzzy_result[0], fuzzy_result[1]
        if score >= 70:
            return str(dir_path / best_match)

    return None