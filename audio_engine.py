"""
Module: audio_engine.py
Meaning: Manages the loading, mixing, and playback of audio stems.
Working Procedure: Uses a Producer-Consumer threading model. A background 
thread reads and mixes audio from disk, pushing it to a thread-safe queue 
consumed by a lock-free, ultra-fast audio callback.
"""
import os
import threading
import queue
import sounddevice as sd
import soundfile as sf
import numpy as np
import logging
from numba import njit
from rubberband_wrapper import RubberBandStretcher
from utils import find_file_case_insensitive

# --- FAST AUDIO DSP FUNCTIONS ---
@njit(cache=True)
def apply_hpf_fast(chunk, alpha, prev_in, prev_out):
    """
    JIT-compiled high-pass filter. 
    Processes samples in-place for maximum performance.
    """
    frames = chunk.shape[0]
    channels = chunk.shape[1]
    
    for n in range(frames):
        for c in range(channels):
            x = chunk[n, c]
            # y[n] = alpha * (y[n-1] + x[n] - x[n-1])
            y = alpha * (prev_out[c] + x - prev_in[c])
            
            # Update states
            prev_in[c] = x
            prev_out[c] = y
            
            # Overwrite chunk array in-place
            chunk[n, c] = y

def apply_fade_in_fast(chunk, fade_ramp, fade_index):
    """
    JIT-compiled fade-in.
    Processes in-place and handles chunks of any size perfectly.
    Returns the updated fade_index.
    """
    frames = chunk.shape[0]
    fade_len = fade_ramp.shape[0]
    
    # Calculate how many frames we can fade in this specific chunk
    frames_to_fade = min(frames, fade_len - fade_index)
    
    for n in range(frames_to_fade):
        # fade_ramp is a 2D array (fade_len, 1), so we access [..., 0]
        gain = fade_ramp[fade_index + n, 0] 
        chunk[n, 0] *= gain # Left channel
        chunk[n, 1] *= gain # Right channel
        
    return fade_index + frames_to_fade

@njit(cache=True)
def apply_limiter_fast(chunk):
    """
    Pro-grade soft-clipper to prevent digital harshness when summing stems.
    Starts smoothing signals above 0.9 amplitude.
    """
    frames = chunk.shape[0]
    for n in range(frames):
        for c in range(2):
            x = chunk[n, c]
            if x > 0.9:
                chunk[n, c] = 0.9 + (x - 0.9) / (1.0 + (x - 0.9)**2)
            elif x < -0.9:
                chunk[n, c] = -0.9 + (x + 0.9) / (1.0 + (x + 0.9)**2)
            if chunk[n, c] > 1.0: chunk[n, c] = 1.0
            elif chunk[n, c] < -1.0: chunk[n, c] = -1.0

logger = logging.getLogger("AudioEngine")

class AudioEngine:
    def __init__(self):
        self.samplerate = 44100
        self.blocksize = 8192
        self.stream = None
        
        self.lock = threading.Lock()
        self.loading_new_song = False 
        self.load_id = 0
        
        # Pitch & Speed State
        self.pitch_semitones = 0
        self.speed_ratio = 1.0
        self.stretcher = None 
        self.stretcher_unpitched = None # NEW: Parallel stretcher for drums
        self.set_pitch_stretcher()
        
        # Producer-Consumer Queue
        self.audio_queue = queue.Queue(maxsize=5)
        self.producer_thread = None
        self.engine_running = True 
        
        # Audio state
        self.stems_data = {}   
        self.stems_list = []   
        self.stems_metadata = [] # List of stem names for immediate mixer building
        self.volumes = np.array([], dtype='float32')
        self.mutes = np.array([], dtype='bool')
        self.pans = np.array([], dtype='float32')
        self.solos = np.array([], dtype='bool')
        
        self.is_playing = False
        self.is_loaded = False # Decoded audio data is ready for playback
        self.current_frame = 0 
        self.max_frames = 0    
        self.seek_request = None 
        
        # Metering State
        self.last_peaks = [0.0, 0.0] # [Left, Right]

        # Master Controls
        self.master_volume = 1.0
        self.master_mute = False
        self.master_pan = 0.0
        self.master_left_gain = 1.0
        self.master_right_gain = 1.0
        # Looping state
        self.loop_start_frame = 0
        self.loop_end_frame = 0
        self.loop_enabled = False
        self.pending_loop = None # (start_pct, end_pct)

        # Buffering & Fades
        self.residual_buffer = np.zeros((0, 2), dtype='float32')
        self.fade_len = 1024 
        self.fade_ramp = np.linspace(0, 1, self.fade_len, dtype='float32').reshape(-1, 1)
        self.fade_index = self.fade_len

        # --- HIGH-PASS FILTER (ANTI-MUD) ---
        self.hpf_prev_in = np.zeros(2, dtype='float32')  # x[n-1]
        self.hpf_prev_out = np.zeros(2, dtype='float32') # y[n-1]
        self.hpf_alpha = 1.0 

        # Stream Initialization
        try:
            self.stream = sd.OutputStream(
                samplerate=self.samplerate, 
                blocksize=self.blocksize,
                channels=2, 
                latency='low', 
                callback=self.audio_callback
            )
            if self.stream is not None:
                self.stream.start()
        except Exception as e:
            logger.error(f"Failed to start audio stream: {e}")
        
        self.producer_thread = threading.Thread(target=self.producer_loop, daemon=True)
        self.producer_thread.start()

    def set_buffer_size(self, new_size):
        """Allows the user to dynamically change the audio buffer latency."""
        if new_size not in [64, 128, 256, 512, 1024, 2048, 4096]:
            logger.warning(f"Invalid blocksize requested: {new_size}")
            return
            
        self.blocksize = new_size
        logger.info(f"Audio buffer size (latency) set to {self.blocksize} samples.")
        
        # If the stream is currently active, we need to restart it
        if self.stream and self.stream.active:
            was_playing = self.is_playing
            self.stop()
            self.stream.close()
            
            # Re-initialize the stream with the new blocksize
            self.stream = sd.OutputStream(
                samplerate=self.samplerate,
                blocksize=self.blocksize,
                channels=2,
                callback=self.audio_callback
            )
            self.stream.start()
            if was_playing:
                self.play()

    def scan_song_stems(self, song_title):
        """
        Fast scan to find stem names without opening audio files.
        Populates stems_metadata so the UI can build the mixer immediately.
        """
        self.loading_new_song = True
        with self.lock:
            self.unload_song()
            self.is_loaded = False
            self.stems_metadata = []
            self.stems_data = {}
            
            songs_base_dir = os.path.join("music", "Songs")
            song_dir = find_file_case_insensitive(songs_base_dir, song_title)
            
            if song_dir and os.path.isdir(song_dir):
                all_audio_files = [os.path.join(song_dir, f) for f in os.listdir(song_dir) 
                                   if f.endswith(('.wav', '.mp3', '.ogg', '.flac'))]
                
                full_mix_files = [f for f in all_audio_files if os.path.splitext(os.path.basename(f))[0].lower() == song_title.lower()]
                stem_files = [f for f in all_audio_files if f not in full_mix_files]
                targets = stem_files if stem_files else (full_mix_files if full_mix_files else all_audio_files[:1])
                
                for i, path in enumerate(targets):
                    name = os.path.splitext(os.path.basename(path))[0]
                    self.stems_metadata.append(name)
                    # Use index as placeholder so Mixer can keep references
                    self.stems_data[name] = i
                
                self.volumes = np.ones(len(targets), dtype='float32') * 0.8
                self.mutes = np.zeros(len(targets), dtype='bool')
                self.pans = np.zeros(len(targets), dtype='float32')
                self.solos = np.zeros(len(targets), dtype='bool')
                    
            logger.info(f"Scanned song '{song_title}': Found {len(self.stems_metadata)} stems.")
            self.loading_new_song = False
            return self.stems_metadata

    def load_song(self, song_title):
        # 1. Generate a unique ID for this specific load request.
        # If the user clicks another song quickly, this ID will increment.
        self.load_id += 1
        current_load_id = self.load_id
        
        # Tell the producer thread to stop trying to read audio
        self.loading_new_song = True 

        with self.lock:
            # 2. Early Abort: If the user clicked another song while we 
            # were waiting for the lock, abort this stale request immediately.
            if self.load_id != current_load_id:
                return False

            self.unload_song()
            songs_base_dir = os.path.join("music", "Songs")
            song_dir = find_file_case_insensitive(songs_base_dir, song_title)

            loaded_files = []
            if song_dir and os.path.isdir(song_dir):
                all_audio_files = [os.path.join(song_dir, f) for f in os.listdir(song_dir) 
                                   if f.endswith(('.wav', '.mp3', '.ogg', '.flac'))]
                
                # Logic: check for stems vs full mix
                # Full mix is assumed to be named exactly as the song title
                full_mix_files = [f for f in all_audio_files if os.path.splitext(os.path.basename(f))[0].lower() == song_title.lower()]
                stem_files = [f for f in all_audio_files if f not in full_mix_files]

                if stem_files:
                    loaded_files = stem_files
                elif full_mix_files:
                    loaded_files = [full_mix_files[0]]
                elif all_audio_files:
                    loaded_files = [all_audio_files[0]]

            if not loaded_files:
                self.loading_new_song = False
                return False

            volumes_list, mutes_list, pans_list, solos_list = [], [], [], []
            target_samplerate = None 

            for filepath in loaded_files:
                # 3. Mid-Load Abort: Disk I/O is slow. If the user clicked 
                # a new song while we were reading files, abort instantly.
                if self.load_id != current_load_id:
                    self.unload_song() # Clean up what we opened so far
                    return False
                    
                stem_name = os.path.splitext(os.path.basename(filepath))[0]
                try:
                    f = sf.SoundFile(filepath)
                    
                    # Sample Rate Validation
                    if target_samplerate is None:
                        target_samplerate = f.samplerate
                    elif f.samplerate != target_samplerate:
                        logger.warning(f"Stem Mismatch: '{stem_name}' has sr={f.samplerate}, expected {target_samplerate}. Skipping.")
                        f.close()
                        continue
                        
                    self.stems_list.append(f)
                    volumes_list.append(0.8)
                    mutes_list.append(False)
                    pans_list.append(0.0)
                    solos_list.append(False)
                    self.stems_data[stem_name] = len(self.stems_list) - 1
                except Exception as e:
                    logger.error(f"Failed to open audio file {filepath}: {e}")

            if not self.stems_list:
                self.loading_new_song = False
                return False

            self.is_loaded = True

            # Dynamic Engine Reconfiguration
            if target_samplerate and target_samplerate != self.samplerate:
                self.samplerate = target_samplerate
                if self.stream is not None:
                    try:
                        self.stream.stop()
                        self.stream.close()
                    except Exception: pass
                    
                self.stream = sd.OutputStream(
                    samplerate=self.samplerate, 
                    blocksize=self.blocksize,
                    channels=2, 
                    latency='low', 
                    callback=self.audio_callback
                )
                self.stream.start()
                logger.info(f"Audio Engine: Reconfigured hardware to {self.samplerate}Hz")
                self.set_pitch_stretcher()

            # Finalize loading state
            self.max_frames = max(f.frames for f in self.stems_list)
            self.volumes = np.array(volumes_list, dtype='float32')
            self.mutes = np.array(mutes_list, dtype='bool')
            self.pans = np.array(pans_list, dtype='float32')
            self.solos = np.array(solos_list, dtype='bool')
            
            # Apply pending loop if exists
            if self.pending_loop:
                s_pct, e_pct = self.pending_loop
                self.loop_start_frame = int(s_pct * self.max_frames)
                self.loop_end_frame = int(e_pct * self.max_frames)
                self.loop_enabled = True
                self.set_position(s_pct * (self.max_frames / self.samplerate))
                self.pending_loop = None
            
            # 4. Only release the loading lock if NO new song was requested
            # This completely fixes the race condition that was crashing the app.
            if self.load_id == current_load_id:
                self.loading_new_song = False
                return True
            else:
                return False

    def producer_loop(self):
        while self.engine_running:
            if not self.is_playing or self.loading_new_song:
                sd.sleep(10)
                continue

            with self.lock:
                if self.seek_request is not None:
                    self.current_frame = int(self.seek_request)
                    for f in self.stems_list:
                        try: f.seek(min(self.current_frame, f.frames))
                        except Exception: pass
                    self.flush_queue()
                    self.residual_buffer = np.zeros((0, 2), dtype='float32')
                    self.hpf_prev_in.fill(0); self.hpf_prev_out.fill(0) # Reset filtro su seek
                    self.fade_index = 0
                    self.needs_fade_in = True
                    if self.stretcher and hasattr(self.stretcher, 'reset'):
                        self.stretcher.reset()
                    if self.stretcher_unpitched and hasattr(self.stretcher_unpitched, 'reset'):
                        self.stretcher_unpitched.reset()
                    self.seek_request = None

                frames_left = self.max_frames - self.current_frame
                
                # LOOPING LOGIC: Wrap around if loop is enabled and we reached the end
                if self.loop_enabled and self.loop_end_frame > self.loop_start_frame:
                    if self.current_frame >= self.loop_end_frame:
                        self.current_frame = self.loop_start_frame
                        for f in self.stems_list:
                            try: f.seek(self.current_frame)
                            except Exception: pass
                        self.flush_queue()
                        self.residual_buffer = np.zeros((0, 2), dtype='float32')
                        self.fade_index = 0 # Optional: crossfade/fade on loop?
                        frames_left = self.loop_end_frame - self.current_frame
                    else:
                        frames_left = min(frames_left, self.loop_end_frame - self.current_frame)

                if frames_left <= 0:
                    self.is_playing = False
                    continue

                read_frames = min(self.blocksize, frames_left)
                
                # Create separate mix chunks
                mixed_pitched = np.zeros((read_frames, 2), dtype='float32')
                mixed_unpitched = np.zeros((read_frames, 2), dtype='float32')

                # Identify the drum stem indices (case-insensitive check)
                drum_indices = {idx for name, idx in self.stems_data.items() if 'drum' in name.lower()}

                any_solo = np.any(self.solos)

                if not self.master_mute and len(self.stems_list) > 0:
                    for i, f in enumerate(self.stems_list):
                        try:
                            data = f.read(read_frames, dtype='float32', always_2d=True)
                            
                            is_audible = self.solos[i] if any_solo else not self.mutes[i]
                            if data.shape[0] == 0 or not is_audible: continue

                            vol = self.volumes[i]
                            pan = self.pans[i]
                            
                            # Pre-calculate individual stem L/R gains
                            l_gain = vol * min(1.0, 1.0 - pan)
                            r_gain = vol * min(1.0, 1.0 + pan)
                            
                            # Route to the correct mix chunk
                            if i in drum_indices:
                                mixed_unpitched[:data.shape[0], 0] += data[:, 0] * l_gain
                                mixed_unpitched[:data.shape[0], 1] += data[:, 1] * r_gain
                            else:
                                mixed_pitched[:data.shape[0], 0] += data[:, 0] * l_gain
                                mixed_pitched[:data.shape[0], 1] += data[:, 1] * r_gain
                        except Exception: continue

                # Apply Master Volume and Pan to both chunks
                mixed_pitched *= self.master_volume
                mixed_pitched[:, 0] *= self.master_left_gain
                mixed_pitched[:, 1] *= self.master_right_gain

                mixed_unpitched *= self.master_volume
                mixed_unpitched[:, 0] *= self.master_left_gain
                mixed_unpitched[:, 1] *= self.master_right_gain
                
                # --- DYNAMIC HIGH-PASS FILTER LOGIC (Only on Pitched) ---
                if self.pitch_semitones < 0:
                    self.hpf_alpha = np.clip(1.0 + (self.pitch_semitones * 0.005), 0.92, 0.999)
                    # Pass the arrays directly to the compiled Numba function
                    apply_hpf_fast(mixed_pitched, self.hpf_alpha, self.hpf_prev_in, self.hpf_prev_out)
                else:
                    self.hpf_alpha = 1.0

                # Pitch Shift & Sync
                if self.stretcher is not None and self.stretcher_unpitched is not None:
                    try:
                        out_pitched = self.stretcher.process_and_retrieve(mixed_pitched)
                        out_unpitched = self.stretcher_unpitched.process_and_retrieve(mixed_unpitched)
                        
                        # Match lengths (RubberBand buffers might return slight variances)
                        min_len = min(len(out_pitched), len(out_unpitched))
                        processed_output = out_pitched[:min_len] + out_unpitched[:min_len]
                    except Exception: 
                        processed_output = mixed_pitched + mixed_unpitched
                else:
                    processed_output = mixed_pitched + mixed_unpitched

                # Handle Fades
                if self.fade_index < self.fade_len:
                    self.fade_index = apply_fade_in_fast(processed_output, self.fade_ramp, self.fade_index)

                # --- FINAL PRO PROTECTION: MASTER LIMITER ---
                # Ensures the summed result of all stems doesn't clip the hardware output
                apply_limiter_fast(processed_output)
                
                # --- PEAK METERING ---
                if processed_output.shape[0] > 0:
                    self.last_peaks[0] = np.max(np.abs(processed_output[:, 0]))
                    self.last_peaks[1] = np.max(np.abs(processed_output[:, 1]))

                self.residual_buffer = np.concatenate((self.residual_buffer, processed_output), axis=0)
                self.current_frame += read_frames

            while len(self.residual_buffer) >= self.blocksize and not self.loading_new_song and self.is_playing:
                chunk_to_send = self.residual_buffer[:self.blocksize].copy()
                try:
                    self.audio_queue.put(chunk_to_send, timeout=0.1)
                    self.residual_buffer = self.residual_buffer[self.blocksize:]
                except queue.Full:
                    break
                    
    def audio_callback(self, outdata, frames, time, status):
        """
        Ultra-fast, lock-free audio callback.
        Consumes from the audio queue and writes directly to the hardware buffer.
        """
        # 1. Handle hardware status flags (underflows/overflows)
        # Avoid print() statements here in production as I/O can block the audio thread!
        if status:
            pass 

        # 2. Silence if paused or loading
        if not self.is_playing or self.loading_new_song:
            outdata.fill(0)
            return

        # 3. Non-blocking queue retrieval
        try:
            # get_nowait() is critical. NEVER use get() with a timeout here.
            data = self.audio_queue.get_nowait()
            
            # 4. Direct memory copy
            # Since self.blocksize perfectly matches the stream's blocksize, 
            # this will almost always be a 1:1 direct array assignment.
            if len(data) == frames:
                outdata[:] = data
            else:
                # Safe fallback if lengths mismatch for any reason
                valid_frames = min(len(data), frames)
                outdata[:valid_frames] = data[:valid_frames]
                outdata[valid_frames:].fill(0)
                
        except queue.Empty:
            # BUFFER UNDERRUN: Producer loop couldn't process DSP fast enough.
            # Output absolute silence to prevent looping the previous buffer (screeching).
            outdata.fill(0)

    def play(self):
        if not self.stems_list or self.is_playing: return
        self.is_playing = True
        
    def pause(self):
        self.is_playing = False
        with self.lock:
            self.flush_queue()
            # Wipe out any un-queued audio so it doesn't leak later
            self.residual_buffer = np.zeros((0, 2), dtype='float32')

    def stop(self):
        """Stops playback and resets to the beginning without unloading stems."""
        self.is_playing = False
        with self.lock:
            self.flush_queue()
            self.current_frame = 0
            self.residual_buffer = np.zeros((0, 2), dtype='float32')
            # Reset file cursors to start
            for f in self.stems_list:
                try: f.seek(0)
                except Exception: pass
            self.hpf_prev_in.fill(0); self.hpf_prev_out.fill(0)
            self.fade_index = 0
            self.seek_request = None

    def unload_song(self):
        self.is_playing = False
        self.flush_queue()
        self.current_frame = 0
        self.max_frames = 0
        self.residual_buffer = np.zeros((0, 2), dtype='float32')
        self.hpf_prev_in.fill(0); self.hpf_prev_out.fill(0)
        self.loop_enabled = False

        for f in self.stems_list:
            try: f.close()
            except Exception: pass
        self.stems_list = []
        self.stems_data.clear()

    def flush_queue(self):
        """Empties the audio queue safely without blocking."""
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def seek(self, seconds):
        if self.max_frames == 0: return
        frames_to_move = int(seconds * self.samplerate)
        base_frame = float(self.seek_request) if self.seek_request is not None else float(self.current_frame)
        target = base_frame + frames_to_move
        self.seek_request = int(max(0, min(target, self.max_frames)))

    def set_position(self, seconds):
        if self.max_frames == 0: return
        target_frame = int(seconds * self.samplerate)
        self.seek_request = int(max(0, min(target_frame, self.max_frames)))

    def set_volume(self, stem_name, volume):
        if stem_name in self.stems_data: 
            self.volumes[self.stems_data[stem_name]] = volume

    def set_pan(self, stem_name, pan):
        if stem_name in self.stems_data:
            self.pans[self.stems_data[stem_name]] = pan

    def set_solo(self, stem_name, is_soloed):
        if stem_name in self.stems_data:
            self.solos[self.stems_data[stem_name]] = is_soloed

    def set_loop(self, start_pct, end_pct):
        """Sets loop points based on normalized 0.0-1.0 progress."""
        if self.max_frames == 0: 
            self.pending_loop = (start_pct, end_pct)
            print(f"Pending loop set: {start_pct} to {end_pct}")
            return
            
        self.loop_start_frame = int(start_pct * self.max_frames)
        self.loop_end_frame = int(end_pct * self.max_frames)
        self.loop_enabled = True
        
        # JUMP to loop start immediately
        self.set_position(start_pct * (self.max_frames / self.samplerate))
        
        print(f"Loop enabled: {self.loop_start_frame} to {self.loop_end_frame}")

    def clear_loop(self):
        self.loop_enabled = False
        print("Loop disabled")

    def set_mute(self, stem_name, is_muted):
        if stem_name in self.stems_data: 
            self.mutes[self.stems_data[stem_name]] = is_muted

    def get_duration(self):
        return self.max_frames / self.samplerate if self.max_frames > 0 else 0

    def get_current_time(self):
        if self.max_frames == 0: return 0
        if self.seek_request is not None:
            return self.seek_request / self.samplerate
        return self.current_frame / self.samplerate

    def get_peaks(self):
        """Returns current master peaks and resets them for the next UI poll."""
        p = self.last_peaks[:]
        self.last_peaks = [0.0, 0.0]
        return p

    def set_master_volume(self, volume):
        self.master_volume = volume

    def set_master_mute(self, is_muted):
        self.master_mute = is_muted
    
    def set_master_pan(self, pan_value):
        """Pre-calculates master panning to save CPU cycles in the mixing loop."""
        self.master_pan = max(-1.0, min(1.0, pan_value))
        self.master_left_gain = min(1.0, 1.0 - self.master_pan)
        self.master_right_gain = min(1.0, 1.0 + self.master_pan)

    def set_pitch_stretcher(self):
        with self.lock:
            if self.pitch_semitones != 0 or self.speed_ratio != 1.0:
                scale = 2.0 ** (self.pitch_semitones / 12.0)
                hq_options = (
                    RubberBandStretcher.OptionProcessRealTime | 
                    RubberBandStretcher.OptionPitchHighConsistency |
                    RubberBandStretcher.OptionTransposerHighQuality |
                    RubberBandStretcher.OptionStretchPrecise
                )
                # Stretcher for pitched instruments
                self.stretcher = RubberBandStretcher(self.samplerate, 2, scale, self.speed_ratio, options=hq_options)
                
                # Stretcher for drums (scale = 1.0 means no pitch shift, but keeps exact sync latency and speed)
                self.stretcher_unpitched = RubberBandStretcher(self.samplerate, 2, 1.0, self.speed_ratio, options=hq_options)
            else:
                self.stretcher = None
                self.stretcher_unpitched = None
            
            # Reset filter states on pitch/speed change
            self.hpf_prev_in = np.zeros(2, dtype='float32')
            self.hpf_prev_out = np.zeros(2, dtype='float32')

    def set_pitch_shift(self, semitones):
        self.pitch_semitones = float(semitones)
        self.set_pitch_stretcher()

    def set_speed_ratio(self, ratio):
        self.speed_ratio = float(ratio)
        self.set_pitch_stretcher()
        
    def export_offline_mixdown(self, output_filepath, progress_callback=None):
        """
        Renders the current stems state (volumes, mutes, pitch) offline.
        Periodically calls progress_callback(int) to update the UI.
        """
        import os
        import soundfile as sf
        import numpy as np
        
        with self.lock:
            if getattr(self, 'stems_list', None) == [] or not self.stems_list:
                return False, "No stems loaded."
            
            # 1. Take a safe snapshot of the current mixer state
            filepaths = [f.name for f in self.stems_list]
            volumes = self.volumes.copy() if len(self.volumes) > 0 else np.ones(len(filepaths), dtype='float32') * 0.8
            mutes = self.mutes.copy() if len(self.mutes) > 0 else np.zeros(len(filepaths), dtype='bool')
            
            pitch_semitones = self.pitch_semitones
            samplerate = self.samplerate
            speed_ratio = self.speed_ratio
            drum_indices = {idx for name, idx in self.stems_data.items() if 'drum' in name.lower()}
            
            master_volume = self.master_volume
            master_left_gain = self.master_left_gain
            master_right_gain = self.master_right_gain

        try:
            # 2. Open fresh file handles
            files = [sf.SoundFile(p) for p in filepaths]
            max_frames = max(f.frames for f in files) if files else 0
            
            if max_frames == 0:
                return False, "No audio data found."
            
            # 3. Setup parallel stretchers
            stretcher = None
            stretcher_unpitched = None
            if pitch_semitones != 0 or speed_ratio != 1.0:
                from rubberband_wrapper import RubberBandStretcher
                scale = 2.0 ** (pitch_semitones / 12.0)
                hq_options = (
                    RubberBandStretcher.OptionProcessRealTime |
                    RubberBandStretcher.OptionPitchHighConsistency |
                    RubberBandStretcher.OptionTransposerHighQuality |
                    RubberBandStretcher.OptionStretchPrecise
                )
                stretcher = RubberBandStretcher(samplerate, 2, scale, speed_ratio, options=hq_options)
                stretcher_unpitched = RubberBandStretcher(samplerate, 2, 1.0, speed_ratio, options=hq_options)

            # 4. Open output file
            out_file = sf.SoundFile(output_filepath, 'w', samplerate, 2)
            
            blocksize = 8192
            current_frame = 0
            
            hpf_prev_in = np.zeros(2, dtype='float32')
            hpf_prev_out = np.zeros(2, dtype='float32')
            hpf_alpha = 1.0
            if pitch_semitones < 0:
                hpf_alpha = np.clip(1.0 + (pitch_semitones * 0.005), 0.92, 0.999)
            
            # 5. Iterative Fast Mixing Loop
            while current_frame < max_frames:
                read_frames = min(blocksize, max_frames - current_frame)
                mixed_pitched = np.zeros((read_frames, 2), dtype='float32')
                mixed_unpitched = np.zeros((read_frames, 2), dtype='float32')

                for i, f in enumerate(files):
                    if i < len(mutes) and mutes[i]:
                        f.seek(f.tell() + read_frames)
                        continue
                    
                    data = f.read(read_frames, dtype='float32', always_2d=True)
                    if data.shape[0] < read_frames:
                        padded = np.zeros((read_frames, 2), dtype='float32')
                        padded[:data.shape[0]] = data[:, :2]
                        data = padded
                        
                    vol = volumes[i] if i < len(volumes) else 0.8
                    if i in drum_indices:
                        mixed_unpitched += data * vol
                    else:
                        mixed_pitched += data * vol

                mixed_pitched *= master_volume
                mixed_pitched[:, 0] *= master_left_gain
                mixed_pitched[:, 1] *= master_right_gain

                mixed_unpitched *= master_volume
                mixed_unpitched[:, 0] *= master_left_gain
                mixed_unpitched[:, 1] *= master_right_gain
                
                if pitch_semitones < 0:
                    apply_hpf_fast(mixed_pitched, hpf_alpha, hpf_prev_in, hpf_prev_out)

                if stretcher and stretcher_unpitched:
                    try:
                        out_p = stretcher.process_and_retrieve(mixed_pitched)
                        out_u = stretcher_unpitched.process_and_retrieve(mixed_unpitched)
                        min_len = min(len(out_p), len(out_u))
                        processed_output = out_p[:min_len] + out_u[:min_len]
                    except Exception:
                        processed_output = mixed_pitched + mixed_unpitched
                else:
                    processed_output = mixed_pitched + mixed_unpitched

                if len(processed_output) > 0:
                    out_file.write(processed_output)

                current_frame += read_frames
                
                # --- UPDATE PROGRESS BAR ---
                if progress_callback and (current_frame % (blocksize * 5) < blocksize):
                    # Map the WAV export to 0-90% of the progress bar.
                    # The last 10% will be reserved for the MP3 conversion in the UI.
                    percent = int((current_frame / max_frames) * 90)
                    
                    # If callback returns False, user clicked Cancel
                    if not progress_callback(percent):
                        out_file.close()
                        for f in files: f.close()
                        if os.path.exists(output_filepath):
                            os.remove(output_filepath)
                        return False, "Canceled"
                    
            # 6. Flush leftover delayed packets
            if stretcher and stretcher_unpitched:
                empty_block = np.zeros((blocksize, 2), dtype='float32')
                for _ in range(5): 
                    out_p = stretcher.process_and_retrieve(empty_block)
                    out_u = stretcher_unpitched.process_and_retrieve(empty_block)
                    min_len = min(len(out_p), len(out_u))
                    processed_output = out_p[:min_len] + out_u[:min_len]
                    if len(processed_output) > 0:
                        out_file.write(processed_output)

            # Cleanup
            out_file.close()
            for f in files: 
                f.close()
            
            return True, ""
            
        except Exception as e:
            return False, str(e)

    def cleanup(self):
        """Explicitly stops threads, flushes queues, and closes file handles safely."""
        # 1. Signal the producer thread to stop its loop
        self.engine_running = False
        self.is_playing = False 
        
        # 2. Flush queue to immediately unblock the producer if it's stuck on a queue.Full state
        self.flush_queue()
        
        # 3. Wait for the producer thread to exit gracefully
        if self.producer_thread and self.producer_thread.is_alive():
            self.producer_thread.join(timeout=2.0)
            self.producer_thread = None

        # 4. Stop and close the hardware audio stream
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                print(f"Error closing audio stream: {e}")
            self.stream = None

        # 5. Safely close all open audio file handles to release OS locks
        with self.lock:
            for f in self.stems_list:
                try:
                    f.close()
                except Exception:
                    pass
            self.stems_list.clear()
            self.stems_data.clear()
            
            # 6. Release C++ resources held by the stretchers
            self.stretcher = None
            self.stretcher_unpitched = None
