"""
Module: data_manager.py
Meaning: Manages all persistent data (database, setlists, and lyrics parsing).
Working Procedure: Loads and saves the SQLite database containing song metadata, 
handles migration from old JSON formats, manages CSV files for setlists, 
and parses ChordPro text files into HTML for display.
"""
import re
import json
import csv
import sqlite3
import logging
from pathlib import Path
from utils import find_file_case_insensitive

logger = logging.getLogger("DataManager")

class DataManager:
    NOTES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
    FLATS = {'Db': 'C#', 'Eb': 'D#', 'Gb': 'F#', 'Ab': 'G#', 'Bb': 'A#'}
    
    CHORD_REGEX = re.compile(r'^([A-G][#b]?)(.*)')
    CHORDPRO_SPLIT_REGEX = re.compile(r'(\[[^\]]+\])')

    def __init__(self, db_path="songs.db", json_db_path="songs_database.json", config_path="config.json", patch_db_path="patch.db"):
        self.db_path = Path(db_path)
        self.json_db_path = Path(json_db_path)
        self.config_path = Path(config_path)
        self.patch_db_path = Path(patch_db_path)
        self.mappings_path = Path("mappings.json")
        
        # Initialize SQLite and handle any migration from the old JSON file
        self._init_sqlite()
        
        # Load the SQLite data into memory so the rest of the app works exactly as before
        self.database = self.load_database()
        self._migrate_legacy_bpm_field()
        
        # Load configuration and mappings (keeping them separate from SQL)
        self.config = self.load_config()
        self.mappings = self.load_mappings()

        # Initialize Patch SQLite and handle migration from config.json
        self._init_patch_db()
        self.patches = self.load_patches()

    def _init_sqlite(self):
        """Initializes the database, adding an auto-incrementing ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # We now use an auto-incrementing integer as the Primary Key
        # The title is kept UNIQUE so we don't accidentally get duplicates
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT UNIQUE,
                data TEXT
            )
        ''')
        conn.commit()

        # --- Migration Logic ---
        if self.json_db_path.exists():
            print(f"Migrating {self.json_db_path} to SQLite with unique IDs...")
            try:
                with self.json_db_path.open('r', encoding='utf-8') as f:
                    old_data = json.load(f)
                
                keys_to_exclude = {"mixer_settings", "sound_settings", "dmx_settings"}
                
                for song_title, song_data in old_data.items():
                    if song_title not in keys_to_exclude:
                        # INSERT OR IGNORE prevents errors if the title already exists
                        cursor.execute(
                            "INSERT OR IGNORE INTO songs (title, data) VALUES (?, ?)",
                            (song_title, json.dumps(song_data))
                        )
                
                conn.commit()
                
                imported_path = self.json_db_path.with_name(self.json_db_path.name + "_imported")
                self.json_db_path.rename(imported_path)
                print("Migration complete. Old file renamed to:", imported_path.name)
            except Exception as e:
                print(f"Migration failed: {e}")
        
        conn.close()

    def load_database(self):
        """Loads the database and injects the unique ID into the song data."""
        db_dict = {}
        if not self.db_path.exists():
            return db_dict
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Select the ID as well to inject it into the working memory
            cursor.execute("SELECT id, title, data FROM songs")
            for song_id, title, data_json in cursor.fetchall():
                song_data = json.loads(data_json)
                
                # INJECT the database ID into the dictionary so your app can use it!
                song_data["id"] = song_id 
                
                # We still key the dictionary by title for backward compatibility with your app
                db_dict[title] = song_data
        except sqlite3.OperationalError:
            pass
            
        conn.close()
        return db_dict

    def _migrate_legacy_bpm_field(self):
        """
        Normalizes legacy lowercase 'bpm' to canonical 'BPM' and removes 'bpm'.
        Persists only when at least one song needed migration.
        """
        migrated = False
        for song_data in self.database.values():
            if not isinstance(song_data, dict):
                continue
            if "bpm" in song_data:
                if "BPM" not in song_data:
                    song_data["BPM"] = song_data["bpm"]
                del song_data["bpm"]
                migrated = True

        if migrated:
            self.save_database()

    def _init_patch_db(self):
        """Initializes the patch database and migrates data from config.json if needed."""
        conn = sqlite3.connect(self.patch_db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS patches (
                name TEXT PRIMARY KEY,
                actions TEXT
            )
        ''')
        conn.commit()

        # Migration from config.json
        if "patch_library" in self.config:
            logger.info("Migrating patches from config.json to patch.db...")
            for name, actions in self.config["patch_library"].items():
                cursor.execute(
                    "INSERT OR IGNORE INTO patches (name, actions) VALUES (?, ?)",
                    (name, json.dumps(actions))
                )
            conn.commit()
            del self.config["patch_library"]
            self.save_config()
        conn.close()

    def save_database(self):
        """Saves updates. Uses the ID if it exists to update safely."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT title FROM songs")
        existing_titles = {row[0] for row in cursor.fetchall()}
        current_titles = set(self.database.keys())
        
        for title, song_data in self.database.items():
            # Create a copy so we don't accidentally save the 'id' key inside the JSON data blob
            # (since the ID is already handled by the SQL column)
            data_to_save = song_data.copy()
            song_id = data_to_save.pop("id", None) 
            
            if song_id is not None:
                # If we have an ID, update the existing row (handles title changes safely!)
                cursor.execute(
                    "UPDATE songs SET title = ?, data = ? WHERE id = ?",
                    (title, json.dumps(data_to_save), song_id)
                )
            else:
                # If there's no ID, it's a new song. Insert it.
                cursor.execute(
                    "INSERT INTO songs (title, data) VALUES (?, ?)",
                    (title, json.dumps(data_to_save))
                )
            
        # Delete songs removed from the dictionary
        # This works safely because if a song was renamed, the UPDATE above already 
        # changed its title in the DB, so this DELETE statement will gracefully do nothing.
        for title_to_remove in (existing_titles - current_titles):
            cursor.execute("DELETE FROM songs WHERE title = ?", (title_to_remove,))
            
        conn.commit()
        conn.close()
        
        # Reload the database into memory so newly inserted songs get their generated IDs assigned
        self.database = self.load_database()

    def load_patches(self):
        """Loads all patches from the SQLite database."""
        patches = {}
        if not self.patch_db_path.exists():
            return patches
            
        conn = sqlite3.connect(self.patch_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name, actions FROM patches")
        for name, actions_json in cursor.fetchall():
            patches[name] = json.loads(actions_json)
        conn.close()
        return patches

    def save_patches(self):
        """Persists the current in-memory patches dictionary to the SQLite database."""
        conn = sqlite3.connect(self.patch_db_path)
        cursor = conn.cursor()
        # For simplicity in maintaining order and renames, we clear and re-insert
        cursor.execute("DELETE FROM patches")
        for name, actions in self.patches.items():
            cursor.execute("INSERT INTO patches (name, actions) VALUES (?, ?)",
                           (name, json.dumps(actions)))
        conn.commit()
        conn.close()

    def get_song_analysis(self, song_title):
        """Returns analysis metadata (bpm, key, energy, manual, spotify) if available."""
        song_data = self.database.get(song_title, {})
        
        # Determine "Effective" energy (Manual > Cloud > local AI)
        manual_energy = song_data.get("manual_energy")
        cloud = song_data.get("spotify_data")
        local_ai = song_data.get("energy", 5.0)
        
        effective_energy = manual_energy if manual_energy is not None else (cloud['energy'] if cloud else local_ai)
        
        return {
            "bpm": song_data.get("BPM", song_data.get("bpm", 120.0)),
            "key": song_data.get("key", "C"),
            "energy": effective_energy,  # Primary score for the whole app
            "energy_ai_baseline": local_ai,
            "manual_energy": manual_energy,
            "spotify_data": cloud
        }

    def update_song_analysis(self, song_title, bpm=None, key=None, energy=None, manual_energy=None, spotify_data=None):
        """Updates analysis data for a song in the database and memory."""
        # Ensure the song exists in memory
        if song_title not in self.database:
            self.database[song_title] = {}
            
        data = self.database[song_title]
        if bpm is not None:
            data["BPM"] = int(round(float(bpm)))
            if "bpm" in data:
                del data["bpm"]
        if key is not None: data['key'] = key
        # 'energy' is the AI local score
        if energy is not None:
            data['energy'] = int(round(float(energy)))
        # 'manual_energy' is the user override
        if manual_energy is not None: data['manual_energy'] = manual_energy
        # 'spotify_data' contains cloud features
        if spotify_data is not None:
            normalized_spotify = spotify_data.copy() if isinstance(spotify_data, dict) else {}
            if 'bpm' in normalized_spotify and normalized_spotify['bpm'] is not None:
                normalized_spotify['bpm'] = int(round(float(normalized_spotify['bpm'])))
            if 'energy' in normalized_spotify and normalized_spotify['energy'] is not None:
                normalized_spotify['energy'] = int(round(float(normalized_spotify['energy'])))
            data['spotify_data'] = normalized_spotify
        
        # PERSIST TO SQLITE
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Since we store 'data' as a JSON blob, we just dumped the updated dict
            cursor.execute(
                "UPDATE songs SET data = ? WHERE title = ?",
                (json.dumps(data), song_title)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to persist analysis for {song_title}: {e}")

    def get_optimizer_settings(self):
        """Returns saved optimizer preferences (mode, weight, etc)."""
        return self.config.get("optimizer_settings", {
            "mode": "Energy Build",
            "harmonic_weight": 0.2,
            "lock_first": False
        })

    def save_optimizer_settings(self, settings):
        """Persists optimizer preferences."""
        self.config["optimizer_settings"] = settings
        self.save_config()

    def load_config(self):
        """Loads non-song configuration (global settings). Handles migration if needed."""
        config = {}
        if self.config_path.exists():
            try:
                with self.config_path.open('r', encoding='utf-8') as f:
                    config = json.load(f)
            except Exception:
                pass
        
        # --- Migration from Database ---
        migrated = False
        keys_to_migrate = ["mixer_settings", "sound_settings", "dmx_settings"]
        
        for key in keys_to_migrate:
            if key in self.database and key not in config:
                config[key] = self.database[key]
                migrated = True
        
        if migrated:
            # Save the new config
            self.config = config 
            self.save_config()
                
            # Remove from database dictionary
            for key in keys_to_migrate:
                if key in self.database:
                    del self.database[key]
            self.save_database()
            
        return config

    def save_config(self):
        """Saves the current configuration dictionary to file."""
        with self.config_path.open('w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=4)

    def load_mappings(self):
        """Loads input mappings for MIDI/Keyboard."""
        if self.mappings_path.exists():
            try:
                with self.mappings_path.open('r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
                
        # Return default mappings
        return {
            "NEXT_SONG": {"midi": [], "keys": ["space", "page down"]},
            "PREV_SONG": {"midi": [], "keys": ["page up"]},
            "PLAY_PAUSE": {"midi": [], "keys": []},
            "STOP": {"midi": [], "keys": []},
            "SCROLL_UP": {"midi": [], "keys": ["left", "up"]},
            "SCROLL_DOWN": {"midi": [], "keys": ["right", "down"]},
            "MUTE_MASTER": {"midi": [], "keys": []},
            "MUTE_CLICK": {"midi": [], "keys": []},
            "MUTE_GROUP_1": {"midi": [], "keys": ["1"]},
            "MUTE_GROUP_2": {"midi": [], "keys": ["2"]},
            "MUTE_GROUP_3": {"midi": [], "keys": ["3"]},
            "MUTE_GROUP_4": {"midi": [], "keys": ["4"]},
            "MUTE_FX_1": {"midi": [], "keys": []},
            "MUTE_FX_2": {"midi": [], "keys": []},
            "MUTE_FX_3": {"midi": [], "keys": []},
            "MUTE_FX_4": {"midi": [], "keys": []},
            "TOGGLE_AUTOSCROLL": {"midi": [], "keys": []}
        }

    def save_mappings(self):
        with self.mappings_path.open('w', encoding='utf-8') as f:
            json.dump(self.mappings, f, indent=4)

    def get_all_csv_files(self):
        """Returns a list of all CSV filenames (setlists) in the current directory."""
        return [file.name for file in Path('.').glob("*.csv")]

    def load_setlist(self, csv_path):
        """Reads a CSV setlist and returns a list of song titles."""
        setlist = []
        csv_file = Path(csv_path)
        
        if csv_file.exists():
            with csv_file.open(newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader, None) # Skip header
                for row in reader:
                    if row: setlist.append(row[0])
        return setlist

    def save_setlist(self, csv_path, setlist):
        """Writes a list of song titles to a CSV file."""
        with Path(csv_path).open('w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Title"])
            for song in setlist: 
                writer.writerow([song])

    def transpose_single_chord(self, chord, steps):
        """Mathematically transposes a single musical chord up or down."""
        if steps == 0: return chord
        
        chord_match = self.CHORD_REGEX.match(chord)
        if not chord_match: return chord
        
        root, extension = chord_match.groups()
        root = self.FLATS.get(root, root) 
        
        try:
            new_index = (self.NOTES.index(root) + int(steps)) % 12
            return f"{self.NOTES[new_index]}{extension}"
        except ValueError: 
            return chord

    def parse_chordpro_line(self, line, transpose_steps):
        """Parses a line of ChordPro text and transposes chords."""
        if not line.strip(): return " "
        
        if line.startswith('{') and line.endswith('}'):
            return f"<span style='color:#28a745; font-style:italic;'>{line[1:-1]}</span>"
        
        if '[' not in line: return line.lower()
        
        parts = self.CHORDPRO_SPLIT_REGEX.split(line)
        c_str, c_html, l_str = "", "", ""
        
        for part in parts:
            if not part: continue
            
            if part.startswith('[') and part.endswith(']'):
                chord = self.transpose_single_chord(part[1:-1], transpose_steps)
                diff = len(l_str) - len(c_str)
                
                if diff > 0:
                    c_str += " " * diff
                    c_html += " " * diff
                elif len(c_str) > 0:
                    pad = (-diff) + 1
                    c_str += " " * pad
                    c_html += " " * pad
                    l_str += " " * pad

                c_str += chord
                c_html += f"<a href='chord:{chord}' style='color:#1199FF; font-weight:bold; text-decoration:none;'>{chord}</a>"
            else:
                part_lower = part.lower() 
                l_str += part_lower
                
        return f"{c_html}\n{l_str}"

    def parse_chordpro(self, song_title, transpose_steps=0, font_size=14):
        """Loads a ChordPro file, parses all lines, and returns the HTML block."""
        chordpro_dir = Path("music") / "ChordPRO"
        filepath = find_file_case_insensitive(str(chordpro_dir), f"{song_title}.pro")
        
        if not filepath: 
            return f"<div style='color:red;'>File not found: {song_title}</div>"
        
        lines = Path(filepath).read_text(encoding='utf-8').splitlines()
        parsed = [self.parse_chordpro_line(l, transpose_steps) for l in lines]
        
        return (f"<pre style='font-family:monospace; font-size:{font_size}pt; "
                f"line-height:1.2; margin:0;'>{'\n'.join(parsed)}</pre>")

    def parse_chordpro_line_web(self, line, transpose_steps):
        """Parses a line of ChordPro specifically for responsive web views."""
        if not line.strip(): return "<div class='chord-line' style='min-height: 1em;'></div>"
        
        if line.startswith('{') and line.endswith('}'):
            return f"<div class='chord-line'><span class='annotation' style='color:#28a745; font-style:italic;'>{line[1:-1]}</span></div>"
        
        if '[' not in line: 
            return f"<div class='chord-line'><span class='chord-block'><span class='chord'>&nbsp;</span><span class='lyric'>{line.lower()}</span></span></div>"
            
        parts = self.CHORDPRO_SPLIT_REGEX.split(line)
        html = "<div class='chord-line'>"
        
        # Track if we are inside an unclosed chord block
        open_block = False
        
        for part in parts:
            if not part: continue
            
            if part.startswith('[') and part.endswith(']'):
                if open_block: html += "<span class='lyric'>&nbsp;</span></span>"
                chord = self.transpose_single_chord(part[1:-1], transpose_steps)
                html += f"<span class='chord-block'><span class='chord'>{chord}</span>"
                open_block = True
            else:
                if open_block:
                    html += f"<span class='lyric'>{part.lower()}</span></span>"
                    open_block = False
                else:
                    html += f"<span class='chord-block'><span class='chord'>&nbsp;</span><span class='lyric'>{part.lower()}</span></span>"
                    
        if open_block: html += "<span class='lyric'>&nbsp;</span></span>"
        html += "</div>"
        return html

    def parse_chordpro_web(self, song_title, transpose_steps=0):
        chordpro_dir = Path("music") / "ChordPRO"
        filepath = find_file_case_insensitive(str(chordpro_dir), f"{song_title}.pro")
        
        if not filepath: 
            return f"<div style='color:red;'>File not found: {song_title}</div>"
            
        lines = Path(filepath).read_text(encoding='utf-8').splitlines()
        parsed = [self.parse_chordpro_line_web(l, transpose_steps) for l in lines]
        return "".join(parsed)
        
    def get_dmx_config(self):
        """Returns the DMX configuration from global config."""
        return self.config.get("dmx_settings", {
            'accent_r': 255, 'accent_g': 0, 'accent_b': 0, 'accent_dim': 255,
            'std_r': 0, 'std_g': 255, 'std_b': 0, 'std_dim': 255
        })
    
    def save_dmx_config(self, config):
        """Saves the DMX configuration to global config."""
        self.config["dmx_settings"] = config
        self.save_config()

    def get_resolved_patch(self, song_title):
        """
        Returns a list of actions by combining the global patch assigned to the song
        with any legacy 'PC_chX' commands still present in the song data.
        """
        song_data = self.database.get(song_title, {})
        actions = []

        # 1. Add actions from assigned Global Patches
        patch_names = song_data.get("Patches")
        if isinstance(patch_names, list):
            for patch_name in patch_names:
                if patch_name in self.patches:
                    for act in self.patches[patch_name]:
                        action = act.copy() if isinstance(act, dict) else {}
                        if action:
                            source = action.get("source_patch") or action.get("_source_patch")
                            action["_patch_name"] = source or patch_name
                            actions.append(action)
        else:
            # Backward compatibility with legacy single-patch field
            patch_name = song_data.get("Patch")
            if patch_name and patch_name in self.patches:
                for act in self.patches[patch_name]:
                    action = act.copy() if isinstance(act, dict) else {}
                    if action:
                        source = action.get("source_patch") or action.get("_source_patch")
                        action["_patch_name"] = source or patch_name
                        actions.append(action)

        # 2. Replicate Legacy MIDI PC structure (PC_ch0, etc.)
        for key, value in song_data.items():
            if key.startswith("PC_ch") and isinstance(value, int):
                try:
                    ch = int(key.replace("PC_ch", ""))
                    if 0 <= ch <= 15:
                        actions.append({"type": "pc", "channel": ch + 1, "program": value})
                except: pass
        
        return actions