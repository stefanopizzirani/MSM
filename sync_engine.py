import time
import threading
import queue
import mido
import socket
import logging
import asyncio
import numpy as np
import sounddevice as sd
import aalink
from pythonosc.udp_client import SimpleUDPClient
from PyQt6.QtCore import pyqtSignal, QObject

# --- LOGGING CONFIGURATION ---
sync_logger = logging.getLogger("SyncEngine")
sync_logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%H:%M:%S')
handler.setFormatter(formatter)
sync_logger.addHandler(handler)

class SyncEngine(QObject):
    tick_signal = pyqtSignal(bool)
    jitter_signal = pyqtSignal(float)  # Timing error in ms
    meter_signal = pyqtSignal(list)
    name_signal = pyqtSignal(int, str, str) # ch_id (1-18), type ("CH"/"BUS"/"FX"), name
    fader_signal = pyqtSignal(int, str, float) # ch_id, type, level
    mute_signal = pyqtSignal(int, str, bool)   # ch_id, type, is_muted
    pan_signal = pyqtSignal(int, str, float)   # ch_id, type, pan
    mtc_started_signal = pyqtSignal()
    # Link State: (bpm, server_ref_time_ms, ref_beat)
    link_sync_signal = pyqtSignal(float, float, float)

    # --- 1. INITIALIZATION ---

    def __init__(self, osc_ip="192.168.1.1", osc_port=10024):
        super().__init__()
        self.osc_ip = osc_ip
        self.osc_port = osc_port
        self.osc_client = SimpleUDPClient(osc_ip, osc_port)
        
        # --- OSC Receiver Setup ---
        from pythonosc.dispatcher import Dispatcher
        from pythonosc.osc_server import ThreadingOSCUDPServer
        
        self.osc_dispatcher = Dispatcher()
        self.osc_dispatcher.map("/ch/*/config/name", self._handle_mixer_name, "CH")
        self.osc_dispatcher.map("/bus/*/config/name", self._handle_mixer_name, "BUS")
        self.osc_dispatcher.map("/rtn/aux/config/name", self._handle_mixer_name, "CH", 17)
        
        # Faders
        self.osc_dispatcher.map("/ch/*/mix/fader", self._handle_mixer_fader, "CH")
        self.osc_dispatcher.map("/bus/*/mix/fader", self._handle_mixer_fader, "BUS")
        self.osc_dispatcher.map("/rtn/aux/mix/fader", self._handle_mixer_fader, "CH", 17)
        self.osc_dispatcher.map("/lr/mix/fader", self._handle_mixer_fader, "LR", 0)
        
        # Mutes
        self.osc_dispatcher.map("/ch/*/mix/on", self._handle_mixer_mute, "CH")
        self.osc_dispatcher.map("/bus/*/mix/on", self._handle_mixer_mute, "BUS")
        self.osc_dispatcher.map("/rtn/aux/mix/on", self._handle_mixer_mute, "CH", 17)
        self.osc_dispatcher.map("/lr/mix/on", self._handle_mixer_mute, "LR", 0)
        
        # Pan
        self.osc_dispatcher.map("/ch/*/mix/pan", self._handle_mixer_pan, "CH")
        self.osc_dispatcher.map("/rtn/aux/mix/pan", self._handle_mixer_pan, "CH", 17)

        self.osc_dispatcher.set_default_handler(lambda addr, *args: None) # Ignore others
        
        try:
            self.osc_server = ThreadingOSCUDPServer(("0.0.0.0", 0), self.osc_dispatcher)
            self.osc_server_thread = threading.Thread(target=self.osc_server.serve_forever, daemon=True)
            self.osc_server_thread.start()
            sync_logger.info(f"OSC: Receiver started on port {self.osc_server.server_address[1]}")
        except Exception as e:
            sync_logger.error(f"OSC: Failed to start receiver: {e}")
        
        # --- DMX Networking Optimizations ---
        self.dmx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.dmx_ip = "127.0.0.1"
        self.dmx_port = 6454
        self.dmx_settings = {}
        self.dmx_universe = self.dmx_settings.get('universe', 0)

        # Pre-allocate the complete Art-Net packet
        self.artnet_header = bytearray(b'Art-Net\x00')
        self.artnet_header.extend([0x00, 0x50, 0x00, 14, 0x00, 0x00])
        self.artnet_packet = bytearray(self.artnet_header)
        self.artnet_packet.extend([self.dmx_universe & 0xFF, (self.dmx_universe >> 8) & 0xFF])
        self.artnet_packet.extend([(512 >> 8) & 0xFF, 512 & 0xFF])
        self.artnet_packet.extend([0] * 512)
        self.dmx_payload_offset = len(self.artnet_header) + 4 
        
        self.is_playing = False # RESTORED TO BOOLEAN FOR UI
        self.mtc_active = False
        self.bpm = 120
        self.beats_per_bar = 4
        self.pre_roll_bars = 2
        self.beat_thread = None
        self.mtc_thread = None
        self.io_thread = None
        
        # Hardware Flags
        self.enable_midi = True
        self.enable_dmx = True
        self.enable_osc = True
        
        # --- Ableton Link Integration ---
        # Note: aalink requires a running asyncio event loop.
        # We start a dedicated thread to run this loop.
        self.link = None
        self._link_ready = threading.Event()
        threading.Thread(target=self._run_link_loop, daemon=True).start()
        
        if not self._link_ready.wait(timeout=3.0):
            sync_logger.error("SyncEngine: Ableton Link initialization FAILED (timeout)")
        else:
            sync_logger.info("SyncEngine: Ableton Link initialized successfully with background loop")
        # Fire-and-forget queue for beat-sync'd but non-critical I/O (OSC, DMX).
        # The beat thread puts callables here; _io_worker drains it without
        # ever blocking the precision spin-wait.
        self._io_queue = queue.SimpleQueue()
        
        # Audio Click State
        self.click_volume = 0.5
        self.click_mute = False
        self.click_pan = 0.0
        self.click_freq_accent = 1000
        self.click_freq_normal = 800
        self.click_type = "Sine"
        self.click_stream = None
        
        # Thread-safe click playback state.
        # The beat thread only ever writes to `_pending_tick` (a single atomic
        # object reference swap). The audio callback safely picks it up once.
        self._pending_tick = None   # set by beat thread, consumed by callback
        self._click_buf = None      # buffer currently being played
        self._click_pos = 0         # read-head within _click_buf
        
        # MIDI Config
        self.midi_out = None
        self._init_midi()
        
        # Sequencer Configuration
        self.sequencer_enabled = False
        self.current_sequence = None
        self.current_step = 0
        
        self.bpm_sync_targets = [{"fx": 2, "param": 2}, {"fx": 4, "param": 2}]

        # --- MTC & MIDI Actions ---
        self.mtc_enabled = True
        self.mtc_fps = 30
        self.mtc_start_time = 0
        self.mtc_frame_count = 0
        self.mtc_last_piece_time = 0
        self.mtc_piece_idx = 0
        self.mtc_latched_time = (0, 0, 0, 0)
        self.mtc_full_frame_sent = False
        
        self.midi_actions = [] 
        self.next_action_idx = 0
        self.midi_start_sent = False

        self._generate_click_sounds()
        self._prebuild_dmx_buffers()

    def _init_midi(self):
        try:
            out_ports = mido.get_output_names()
            if out_ports:
                self.midi_out = mido.open_output(out_ports[0])
                sync_logger.info(f"MIDI: Connected to {self.midi_out.name}")
        except Exception as e:
            sync_logger.error(f"MIDI: Initialization failed: {e}")

    # --- 2. CORE ENGINE CONTROL ---

    def start(self, with_delay=False):
        if self.is_playing: return
        
        if self.link:
            # This version of aalink only accepts the beat number.
            # Force the beat to 0.0 immediately.
            self.link.force_beat(0.0)
        
        self.is_playing = True
        self.mtc_active = False 
        self.current_step = 0
        
        if self.click_stream is None:
            self.click_stream = sd.OutputStream(
                samplerate=44100, channels=2, callback=self._audio_callback, latency='low'
            )
            self.click_stream.start()
        
        if self.enable_midi and self.midi_out:
            self.midi_out.send(mido.Message('songpos', pos=0))
            self.midi_start_sent = False
        
        now = time.perf_counter()
        delay_sec = 0
        if with_delay:
            beats_to_wait = self.pre_roll_bars * self.beats_per_bar
            delay_sec = (beats_to_wait / self.bpm) * 60.0
            sync_logger.info(f"MTC: Delayed start enabled ({delay_sec:.2f}s for {beats_to_wait} beats)")

        self.mtc_start_time = now + delay_sec
        self.mtc_last_piece_time = self.mtc_start_time
        self.mtc_piece_idx = 0
        self.mtc_full_frame_sent = False
        
        # O(1) Action prep (Strict float mapping)
        self.midi_actions.sort(key=lambda x: float(x.get("time_ms", 0))) # Sort actions by time_ms
        self.next_action_idx = 0 # Reset action index
        
        if self.enable_midi and self.midi_out:
             self.mtc_active = True
        sync_logger.info(f"Metronome/Sync: Engine ACTIVATED (BPM: {self.bpm}, Signature: {self.beats_per_bar}/4)")
        
        self.beat_thread = threading.Thread(target=self._beat_loop, daemon=True)
        self.beat_thread.start()
        
        self.mtc_thread = threading.Thread(target=self._mtc_loop, daemon=True)
        self.mtc_thread.start()
        
        self.io_thread = threading.Thread(target=self._io_worker, daemon=True)
        self.io_thread.start()
        
        # Initial Link Sync broadcast
        self.link_sync_signal.emit(self.bpm, time.perf_counter() * 1000.0, self.link.beat)
        
        self.mtc_started_signal.emit()

    def stop(self):
        if not self.is_playing: return
        self.is_playing = False
        self.mtc_active = False
        sync_logger.info("Metronome/Sync: Engine STOPPED") # Tracks if music is actually moving (post-countdown)

        # Drain any queued I/O so stale events don't fire on the next start()
        while not self._io_queue.empty():
            try: self._io_queue.get_nowait()
            except: break
        
        if self.click_stream is not None:
            self.click_stream.stop()
            self.click_stream.close()
            self.click_stream = None
            
        if self.enable_midi and self.midi_out:
            self.midi_out.send(mido.Message('stop'))
            
        self.send_idle_dmx()

    def set_bpm(self, bpm):
        self.bpm = bpm
        self.link.tempo = bpm
        self.link_sync_signal.emit(bpm, time.perf_counter() * 1000.0, self.link.beat)
        if self.enable_osc:
            self.sync_mixer_effects(bpm)

    def set_time_signature(self, time_sig_str):
        try:
            numerator = time_sig_str.split('/')[0]
            self.beats_per_bar = int(numerator)
            if self.link:
                self.link.quantum = float(self.beats_per_bar)
        except Exception:
            self.beats_per_bar = 4

    def _run_link_loop(self):
        """Standard background thread to run an asyncio loop for Link."""
        self.link_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.link_loop)
        
        async def _init_link():
            self.link = aalink.Link(self.bpm)
            self.link.quantum = float(self.beats_per_bar)
            self.link.enabled = True
            self._link_ready.set()
        
        self.link_loop.create_task(_init_link())
        self.link_loop.run_forever()

    # --- 3. INTERNAL ENGINE PROCESSING ---

    def _beat_loop(self):
        import sys, os as _os
        # Shrink the GIL check interval to cap any preemption window at 0.1ms.
        sys.setswitchinterval(0.0001)
        # Ask the OS scheduler to favour this thread over normal-priority work.
        # Silently ignored if the process lacks privileges.
        try: _os.nice(-10)
        except (PermissionError, AttributeError): pass

        perf_counter = time.perf_counter
        sleep = time.sleep
        beat_count = 0
        next_pulse_time = perf_counter()
        SPIN_THRESHOLD = 0.0005

        # Pre-allocate hot-path objects: avoids GC allocation inside the spin.
        _midi_clock_msg = mido.Message('clock')
        _io_put = self._io_queue.put_nowait
        
        # Link Synchronization State
        # Initialize based on current Link beat to avoid catching up past pulses
        last_pulse_idx = -1
        
        while self.is_playing:
            # 1. ATOMIC STATE CAPTURE
            # Capture tempo and beat in one pass
            curr_tempo = self.link.tempo
            curr_beat = self.link.beat
            
            # 2. Update local BPM if needed
            if self.link.num_peers > 0:
                if abs(self.bpm - curr_tempo) > 0.001:
                    self.bpm = curr_tempo
                    self.link_sync_signal.emit(self.bpm, perf_counter() * 1000.0, curr_beat)

            # 3. Pulse detection (24 pulses per quarter note)
            curr_pulse_idx = int(curr_beat * 24.0)
            
            if curr_pulse_idx > last_pulse_idx:
                # Catch-up protection
                if (curr_pulse_idx - last_pulse_idx) > 24:
                    last_pulse_idx = curr_pulse_idx - 1
                    
                for p_idx in range(last_pulse_idx + 1, curr_pulse_idx + 1):
                    pulse_in_beat = p_idx % 24
                    beat_idx = p_idx // 24
                    
                    if pulse_in_beat == 0:
                        # --- BEAT BOUNDARY (Pulse 0) ---
                        # Calculate Jitter based on the CAPTURED beat
                        # Ideal beat for pulse 0 is exactly beat_idx
                        # Jitter is the discrepancy at the moment we processed it
                        jitter_beats = curr_beat - float(beat_idx)
                        # Normalize jitter to [-0.5, 0.5] range
                        if jitter_beats > 0.5: jitter_beats -= 1.0
                        
                        jitter_ms = jitter_beats * (60.0 / self.bpm) * 1000.0
                        self.jitter_signal.emit(jitter_ms)

                        is_downbeat = (beat_idx % self.beats_per_bar == 0)
                        self.tick_signal.emit(is_downbeat)
                        
                        if not self.click_mute and self.click_volume > 0:
                            self._pending_tick = (
                                self.stereo_tick_down if is_downbeat else self.stereo_tick_up
                            )
                        
                        if self.enable_midi and self.midi_out:
                            self.midi_out.send(_midi_clock_msg)
                        if self.enable_osc:
                            _io_put((self.osc_client.send_message, ("/bpm/tap", 1.0)))
                        if self.enable_dmx:
                            _io_put((self._trigger_beat_dmx, is_downbeat))
                            
                    else:
                        # --- SUB-PULSE ---
                        if self.enable_midi and self.midi_out:
                            self.midi_out.send(_midi_clock_msg)
                        if pulse_in_beat == 5 and self.enable_dmx:
                            _io_put((self._send_dmx_off_pulse, None))
                        
                    if pulse_in_beat % 6 == 0:
                        self.process_midi_step()
                        
                last_pulse_idx = curr_pulse_idx

            # 4. HIGH-PRECISION ADAPTIVE SLEEP
            # Target is the EXACT start of the next pulse
            target_pulse_beat = (curr_pulse_idx + 1) / 24.0
            beats_to_wait = target_pulse_beat - self.link.beat
            
            if beats_to_wait > 0:
                wait_sec = beats_to_wait * (60.0 / self.bpm)
                # Cap the wait to 100ms
                if wait_sec > 0.1: wait_sec = 0.1
                
                # Higher threshold for spin-wait on Link to improve phase-lock
                LINK_SPIN_THRESHOLD = 0.0008 # 0.8ms (800 microseconds)
                
                if wait_sec > LINK_SPIN_THRESHOLD:
                    sleep(wait_sec - LINK_SPIN_THRESHOLD)
                
                # Final wait with cooperative yielding.
                # Keep precision near the boundary while avoiding a hot busy-spin.
                LINK_FINAL_WINDOW_BEATS = 0.00005 # 50 microseconds
                MIN_YIELD_SEC = 0.00005 # 50 microseconds
                while self.is_playing:
                    remaining_beats = target_pulse_beat - self.link.beat
                    if remaining_beats <= 0:
                        break

                    # Outside the final window, sleep proportionally to remaining
                    # time (bounded) so we don't burn CPU polling continuously.
                    if remaining_beats > LINK_FINAL_WINDOW_BEATS:
                        sec_per_beat = 60.0 / max(self.bpm, 1e-6)
                        sleep_time = (remaining_beats - LINK_FINAL_WINDOW_BEATS) * sec_per_beat
                        sleep(min(0.001, max(MIN_YIELD_SEC, sleep_time)))
                    else:
                        # In the final window, just yield to avoid hard spinning.
                        sleep(MIN_YIELD_SEC)

    def set_mtc_fps(self, fps):
        self.mtc_fps = fps

    def _mtc_loop(self):
        perf_counter = time.perf_counter
        sleep = time.sleep
        SPIN_THRESHOLD = 0.002
        piece_interval = 1.0 / (self.mtc_fps * 4)

        while self.is_playing:
            self._update_mtc_and_actions()
            now = perf_counter()
            
            if now < self.mtc_start_time:
                next_target = self.mtc_start_time
            else:
                next_target = self.mtc_last_piece_time + piece_interval
            
            # Evaluate actions ensuring synchronization with mtc_start_time
            if self.next_action_idx < len(self.midi_actions):
                next_action = self.midi_actions[self.next_action_idx]
                action_time_ms = float(next_action.get("time_ms", 0))
                next_action_time = self.mtc_start_time + (action_time_ms / 1000.0)
                next_target = min(next_target, max(now, next_action_time))

            sleep_time = next_target - perf_counter()
            if sleep_time > 0.0001:
                sleep(sleep_time)

    def _update_mtc_and_actions(self):
        now = time.perf_counter()
        elapsed = now - self.mtc_start_time
        
        # If we are in pre-roll / delay, stop and do not start actions
        if elapsed < 0: return 
        
        elapsed_ms = elapsed * 1000

        # 1. MIDI Start
        if not self.midi_start_sent:
            if self.enable_midi and self.midi_out:
                self.midi_out.send(mido.Message('start'))
            self.midi_start_sent = True
            self.mtc_active = True
            self.mtc_started_signal.emit()

        # 2. MTC
        if self.mtc_enabled and self.enable_midi and self.midi_out:
            if not self.mtc_full_frame_sent:
                self._send_mtc_full_frame(elapsed)
                self.mtc_full_frame_sent = True

            piece_interval = 1.0 / (self.mtc_fps * 4)
            while now - self.mtc_last_piece_time >= piece_interval:
                intended_elapsed = self.mtc_last_piece_time + piece_interval - self.mtc_start_time
                self._send_mtc_quarter_frame(intended_elapsed)
                self.mtc_last_piece_time += piece_interval

        # 3. Scheduled MIDI Actions (Rigidamente legate al MTC time)
        while self.next_action_idx < len(self.midi_actions):
            next_action = self.midi_actions[self.next_action_idx]
            action_time_ms = float(next_action.get("time_ms", 0))
            if elapsed_ms >= action_time_ms:
                self._trigger_midi_action(next_action)
                self.next_action_idx += 1
            else:
                break 

    def _send_mtc_quarter_frame(self, elapsed):
        if self.mtc_piece_idx == 0:
            frames_total = int(elapsed * self.mtc_fps)
            frames = frames_total % self.mtc_fps
            seconds = int(elapsed) % 60
            minutes = int(elapsed / 60) % 60
            hours = int(elapsed / 3600) % 24
            self.mtc_latched_time = (hours, minutes, seconds, frames)
        
        hours, minutes, seconds, frames = self.mtc_latched_time
        mtc_type = 3 
        
        val = 0
        if self.mtc_piece_idx == 0: val = frames & 0x0F
        elif self.mtc_piece_idx == 1: val = (frames >> 4) & 0x01
        elif self.mtc_piece_idx == 2: val = seconds & 0x0F
        elif self.mtc_piece_idx == 3: val = (seconds >> 4) & 0x03
        elif self.mtc_piece_idx == 4: val = minutes & 0x0F
        elif self.mtc_piece_idx == 5: val = (minutes >> 4) & 0x03
        elif self.mtc_piece_idx == 6: val = hours & 0x0F
        elif self.mtc_piece_idx == 7: val = ((hours >> 4) & 0x01) | (mtc_type << 1)
        
        msg = mido.Message('quarter_frame', frame_type=self.mtc_piece_idx, frame_value=val)
        self.midi_out.send(msg)
        
        self.mtc_piece_idx = (self.mtc_piece_idx + 1) % 8

    def _send_mtc_full_frame(self, elapsed):
        if not self.enable_midi or not self.midi_out: return
        t = max(0, elapsed)
        frames_total = int(t * self.mtc_fps)
        frames = frames_total % self.mtc_fps
        seconds = int(t) % 60
        minutes = int(t / 60) % 60
        hours = int(t / 3600) % 24
        
        mtc_type = 3
        hr = (mtc_type << 5) | (hours & 0x1F)
        try:
            msg = mido.Message('sysex', data=[0x7F, 0x7F, 0x01, 0x01, hr, minutes, seconds, frames])
            self.midi_out.send(msg)
            sync_logger.info(f"MTC: Full Frame Sent ({hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d})")
        except Exception as e:
            sync_logger.error(f"MTC: Error sending Full Frame: {e}")

    def _trigger_midi_action(self, action):
        if not self.enable_midi or not self.midi_out: return
        
        msg_type = action.get("type", "cc").lower()
        channel = max(0, min(15, action.get("channel", 1) - 1))
        
        if msg_type == "cc":
            cc = action.get("control", 0)
            val = action.get("value", 0)
            self.midi_out.send(mido.Message('control_change', channel=channel, control=cc, value=val))
            sync_logger.info(f"MIDI Action: CC Sent - Ch {channel+1}, CC {cc}, Val {val}")
        elif msg_type == "pc":
            prog = action.get("program", 0)
            self.midi_out.send(mido.Message('program_change', channel=channel, program=prog))
            sync_logger.info(f"MIDI Action: PC Sent - Ch {channel+1}, Prog {prog}")

    def reset_mtc(self, with_delay=False):
        now = time.perf_counter()
        delay_sec = 0
        if with_delay:
            beats_to_wait = self.pre_roll_bars * self.beats_per_bar
            delay_sec = (beats_to_wait / self.bpm) * 60.0
            sync_logger.info(f"MTC: Delayed start enabled ({delay_sec:.2f}s for {beats_to_wait} beats)")

        self.mtc_start_time = now + delay_sec
        self.mtc_last_piece_time = self.mtc_start_time
        self.mtc_piece_idx = 0
        self.mtc_full_frame_sent = False
        self.midi_start_sent = False
        self.midi_actions.sort(key=lambda x: float(x.get("time_ms", 0)))
        self.next_action_idx = 0
        sync_logger.info("MTC: Clock reset")

    def get_mtc_string(self):
        if not self.is_playing:
            return "00:00:00:00"
        
        elapsed = time.perf_counter() - self.mtc_start_time
        if elapsed < 0:
            return "00:00:00:00"
        
        frames_total = int(elapsed * self.mtc_fps)
        frames = frames_total % self.mtc_fps
        seconds = int(elapsed) % 60
        minutes = int(elapsed / 60) % 60
        hours = int(elapsed / 3600) % 24
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"

    # --- 4. MIDI FUNCTIONS ---

    def set_midi_enabled(self, val):
        self.enable_midi = val

    def set_mtc_enabled(self, val):
        self.mtc_enabled = val
        if not val:
            sync_logger.info("MTC: Transmission disabled")
        else:
            sync_logger.info("MTC: Transmission enabled")

    def set_midi_port(self, port_name):
        if self.midi_out:
            try: self.midi_out.close()
            except: pass
        
        self.enable_midi = False
        if port_name:
            try:
                self.midi_out = mido.open_output(port_name)
                self.enable_midi = True
            except Exception as e:
                sync_logger.error(f"MIDI Error: {e}")

    def execute_patch(self, actions):
        """Executes a list of patch actions (MIDI, OSC, etc.) immediately."""
        if not actions: return
        
        for action in actions:
            msg_type = action.get("type", "pc").lower()
            patch_name = action.get("_patch_name")
            patch_prefix = f"Patch '{patch_name}': " if patch_name else "Patch: "
            
            if msg_type in ["pc", "cc"] and self.enable_midi and self.midi_out:
                channel = max(0, min(15, action.get("channel", 1) - 1))
                if msg_type == "pc":
                    prog = action.get("program", 0)
                    self.midi_out.send(mido.Message('program_change', channel=channel, program=prog))
                    sync_logger.info(f"{patch_prefix}PC Sent - Ch {channel+1}, Prog {prog}")
                elif msg_type == "cc":
                    cc = action.get("control", 0)
                    val = action.get("value", 0)
                    self.midi_out.send(mido.Message('control_change', channel=channel, control=cc, value=val))
                    sync_logger.info(f"{patch_prefix}CC Sent - Ch {channel+1}, CC {cc}, Val {val}")
            
            # Future: add OSC / DMX action types here

    def process_midi_step(self):
        if self.sequencer_enabled and self.current_sequence and self.midi_out:
            steps = self.current_sequence.get("steps", [])
            if steps:
                idx = self.current_step % self.current_sequence.get("num_steps", 16)
                chan = self.current_sequence.get("midi_channel", 1) - 1
                cc = self.current_sequence.get("midi_cc", 11)
                self.midi_out.send(mido.Message('control_change', channel=chan, control=cc, value=steps[idx]))
                self.current_step += 1

    # --- 5. DMX FUNCTIONS ---

    def set_dmx_enabled(self, val):
        if not val and getattr(self, 'enable_dmx', False):
            self.send_zero_dmx()
        self.enable_dmx = val

    def send_dmx(self, dmx_data):
        """General DMX send for arbitrary payloads (idle scenes, etc.).
        Uses a single C-level bytes() pass instead of a 512-iteration Python loop."""
        if not self.enable_dmx: return
        n = min(512, len(dmx_data))
        payload = bytes(max(0, min(255, int(v))) for v in dmx_data[:n])
        if n < 512:
            payload += bytes(512 - n)
        self._send_raw_dmx(payload)

    def _trigger_beat_dmx(self, is_downbeat):
        """Sends a pre-built DMX beat pulse. Zero allocation — single memcpy."""
        self._send_raw_dmx(
            self._dmx_payload_down if is_downbeat else self._dmx_payload_up
        )

    def _send_dmx_off_pulse(self, _=None):
        """Sends the pre-built DMX off pulse. Zero allocation — single memcpy."""
        self._send_raw_dmx(self._dmx_payload_off)

    def send_idle_dmx(self):
        if self.enable_dmx:
            data = [0] * 512
            data[0], data[7] = 255, 10
            self.send_dmx(data)

    def send_zero_dmx(self):
        if self.enable_dmx:
            self.send_dmx([0] * 512)

    def _send_raw_dmx(self, payload_512):
        """Writes a 512-byte payload into the pre-allocated Art-Net packet and sends it.
        Uses a single slice assignment (C memcpy) — no Python-level iteration."""
        if not self.enable_dmx: return
        offset = self.dmx_payload_offset
        self.artnet_packet[offset:offset + 512] = payload_512
        try:
            self.dmx_socket.sendto(self.artnet_packet, (self.dmx_ip, self.dmx_port))
        except Exception as e:
            sync_logger.error(f"Art-Net Error: {e}")

    def _prebuild_dmx_buffers(self):
        """Pre-builds the raw 512-byte DMX payloads for the periodic beat patterns.
        Call once at init and again any time dmx_settings changes."""
        s = self.dmx_settings

        buf = bytearray(512)
        buf[0] = s.get('accent_dim', 255)
        buf[4] = s.get('accent_r', 255)
        buf[5] = s.get('accent_g', 0)
        buf[6] = s.get('accent_b', 0)
        self._dmx_payload_down = bytes(buf)

        buf = bytearray(512)
        buf[0] = s.get('std_dim', 255)
        buf[4] = s.get('std_r', 0)
        buf[5] = s.get('std_g', 255)
        buf[6] = s.get('std_b', 0)
        self._dmx_payload_up = bytes(buf)

        buf = bytearray(512)
        buf[0] = s.get('std_dim', 255)
        self._dmx_payload_off = bytes(buf)

    def _io_worker(self):
        """Dedicated thread for beat-synchronised but non-timing-critical I/O.
        Drains OSC and DMX socket calls so the beat thread never blocks on them.
        The beat thread communicates via a single put_nowait(), which costs only
        one lock acquisition and is effectively instantaneous."""
        q = self._io_queue
        while self.is_playing:
            try:
                item = q.get(timeout=0.005)
                # Items are (callable, arg) — arg may be None for zero-arg calls
                fn, arg = item
                if arg is None:
                    fn()
                elif isinstance(arg, tuple):
                    fn(*arg)
                else:
                    fn(arg)
            except queue.Empty:
                continue
            except Exception:
                pass

    # --- 6. OSC / MIXER FUNCTIONS ---

    def set_osc_enabled(self, val):
        self.enable_osc = val

    def update_settings(self, new_ip):
        self.osc_ip = new_ip
        self.osc_client = SimpleUDPClient(self.osc_ip, self.osc_port)
        sync_logger.info(f"OSC: Target IP updated to {self.osc_ip}:{self.osc_port}")

    def set_bpm_sync_targets(self, targets):
        self.bpm_sync_targets = targets
        sync_logger.info(f"OSC: BPM Sync targets updated: {targets}")

    def sync_mixer_effects(self, bpm):
        try:
            ms_val = 60000.0 / bpm
            normalized_val = max(0.0, min(1.0, ms_val / 3000.0))
            
            for target in self.bpm_sync_targets:
                fx = target.get("fx", 2)
                par = target.get("param", 2)
                addr = f"/fx/{fx}/par/{par:02d}"
                self.osc_client.send_message(addr, float(normalized_val))
                sync_logger.info(f"OSC: Syncing FX {fx} (Par {par}) to {bpm} BPM ({ms_val:.2f}ms)")
        except Exception as e:
            sync_logger.warning(f"OSC: Failed to sync mixer: {e}")

    def toggle_mute_group(self, group_id, is_muted):
        if self.enable_osc:
            state = "ON (Muted)" if is_muted else "OFF (Unmuted)"
            val = 1 if is_muted else 0
            try:
                self.osc_client.send_message(f"/config/mute/{group_id}", val)
                sync_logger.info(f"OSC: Mute Group {group_id} set to {state}")
            except Exception as e:
                sync_logger.warning(f"OSC: Mute Group command failed: {e}")

    def toggle_fx_mute(self, fx_group, is_muted):
        if self.enable_osc and 1 <= fx_group <= 4:
            state = "MUTED" if is_muted else "ACTIVE"
            val = 0 if is_muted else 1
            try:
                self.osc_client.send_message(f"/rtn/{fx_group}/mix/on", val)
                sync_logger.info(f"OSC: FX Return {fx_group} is now {state}")
            except Exception as e:
                sync_logger.warning(f"OSC: FX Mute command failed: {e}")

    # --- XR18 SPECIFIC MIXER CONTROLS ---

    def set_mixer_channel_fader(self, channel_int, value_0_1):
        if not self.enable_osc: return
        try:
            if channel_int <= 16: addr = f"/ch/{channel_int:02d}/mix/fader"
            else: addr = "/rtn/aux/mix/fader"
            self.osc_client.send_message(addr, float(value_0_1))
        except Exception as e:
            sync_logger.warning(f"OSC: Failed to set fader for channel {channel_int}: {e}")

    def set_mixer_channel_send_level(self, channel_int, bus_id, value_0_1):
        if not self.enable_osc: return
        try:
            if channel_int <= 16: addr = f"/ch/{channel_int:02d}/mix/{bus_id:02d}/level"
            else: addr = f"/rtn/aux/mix/{bus_id:02d}/level"
            self.osc_client.send_message(addr, float(value_0_1))
        except Exception as e:
            sync_logger.warning(f"OSC: Failed to set send {bus_id} for channel {channel_int}: {e}")

    def set_mixer_channel_mute(self, channel_int, is_muted):
        if not self.enable_osc: return
        try:
            if channel_int <= 16: addr = f"/ch/{channel_int:02d}/mix/on"
            else: addr = "/rtn/aux/mix/on"
            self.osc_client.send_message(addr, 0 if is_muted else 1)
        except Exception as e:
            sync_logger.warning(f"OSC: Failed to set mute for channel {channel_int}: {e}")

    def set_mixer_channel_pan(self, channel_int, pan_value_0_1):
        if not self.enable_osc: return
        try:
            if channel_int <= 16: addr = f"/ch/{channel_int:02d}/mix/pan"
            else: addr = "/rtn/aux/mix/pan"
            self.osc_client.send_message(addr, float(pan_value_0_1))
        except Exception as e:
            sync_logger.warning(f"OSC: Failed to set pan for channel {channel_int}: {e}")

    def load_mixer_snapshot(self, index):
        if not self.enable_osc: return
        try:
            self.osc_client.send_message("/-snap/load", int(index))
            sync_logger.info(f"OSC: Loading Mixer Snapshot {index}")
        except Exception as e:
            sync_logger.warning(f"OSC: Failed to load snapshot {index}: {e}")

    def request_mixer_names(self):
        if not self.enable_osc: return
        try:
            for i in range(1, 17):
                self.osc_client.send_message(f"/ch/{i:02d}/config/name", None)
            self.osc_client.send_message("/rtn/aux/config/name", None)
            for i in range(1, 7):
                self.osc_client.send_message(f"/bus/{i:02d}/config/name", None)
        except Exception as e:
            sync_logger.warning(f"OSC: Failed to request names: {e}")

    def _handle_mixer_name(self, addr, fixed_args, *args):
        try:
            type_tag = fixed_args[0]
            name = args[0] if args else ""
            if len(fixed_args) > 1: ch_id = fixed_args[1]
            else: ch_id = int(addr.split('/')[2])
            self.name_signal.emit(ch_id, type_tag, name)
        except Exception as e:
            sync_logger.error(f"OSC: Error handling name response: {e}")

    def _handle_mixer_fader(self, addr, fixed_args, *args):
        try:
            type_tag = fixed_args[0]
            level = args[0] if args else 0.0
            if len(fixed_args) > 1: ch_id = fixed_args[1]
            else: ch_id = int(addr.split('/')[2])
            self.fader_signal.emit(ch_id, type_tag, level)
        except Exception: pass

    def _handle_mixer_mute(self, addr, fixed_args, *args):
        try:
            type_tag = fixed_args[0]
            is_on = args[0] if args else 1
            is_muted = (is_on == 0)
            if len(fixed_args) > 1: ch_id = fixed_args[1]
            else: ch_id = int(addr.split('/')[2])
            self.mute_signal.emit(ch_id, type_tag, is_muted)
        except Exception: pass

    def _handle_mixer_pan(self, addr, fixed_args, *args):
        try:
            type_tag = fixed_args[0]
            pan = args[0] if args else 0.5
            if len(fixed_args) > 1: ch_id = fixed_args[1]
            else: ch_id = int(addr.split('/')[2])
            self.pan_signal.emit(ch_id, type_tag, pan)
        except Exception: pass

    def load_scene_for_test(self, filepath):
        sync_logger.info(f"OSC: Loading test scene from {filepath}")
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    if "/config" in line and '"' in line:
                        parts = line.split()
                        addr = parts[0]
                        try: name = line.split('"')[1]
                        except IndexError: continue
                            
                        if addr.startswith("/ch/"):
                            ch_id = int(addr.split("/")[2])
                            self.name_signal.emit(ch_id, "CH", name)
                        elif addr.startswith("/bus/"):
                            bus_id = int(addr.split("/")[2])
                            self.name_signal.emit(bus_id, "BUS", name)
                        elif addr.startswith("/rtn/aux"):
                            self.name_signal.emit(17, "CH", name)
        except Exception as e:
            sync_logger.error(f"OSC: Failed to load scene: {e}")

    def set_mixer_master_fader(self, value_0_1):
        if not self.enable_osc: return
        try: self.osc_client.send_message("/lr/mix/fader", float(value_0_1))
        except Exception as e: sync_logger.warning(f"OSC: Failed to set master fader: {e}")

    def request_mixer_state(self):
        if not self.enable_osc: return
        try:
            for i in range(1, 17):
                self.osc_client.send_message(f"/ch/{i:02d}/mix/fader", None)
                self.osc_client.send_message(f"/ch/{i:02d}/mix/on", None)
            
            self.osc_client.send_message("/rtn/aux/mix/fader", None)
            self.osc_client.send_message("/rtn/aux/mix/on", None)
            self.osc_client.send_message("/lr/mix/fader", None)
        except Exception as e:
            sync_logger.warning(f"OSC: Failed to request mixer state: {e}")

    # --- 7. AUDIO / METRONOME FUNCTIONS ---

    def _audio_callback(self, outdata, frames, time_info, status):
        """
        Lock-free audio callback. The beat thread signals a new click by writing
        a single object reference to `_pending_tick` (atomic under Python GIL).
        We consume it here at the start of each buffer, avoiding mid-buffer tears.
        """
        if status:
            sync_logger.debug(f"Audio status: {status}")

        outdata.fill(0)

        # Atomically pick up any new click triggered by the beat thread.
        # This happens at buffer boundaries, so there is never a partial-buffer
        # switch that previously caused the crackling artefact.
        pending = self._pending_tick
        if pending is not None:
            self._pending_tick = None   # consume
            self._click_buf = pending
            self._click_pos = 0

        buf = self._click_buf
        if buf is not None:
            pos = self._click_pos
            remaining = len(buf) - pos
            if remaining > 0:
                chunk_size = min(frames, remaining)
                outdata[:chunk_size] = buf[pos : pos + chunk_size]
                self._click_pos += chunk_size
            else:
                self._click_buf = None

    def update_click_params(self, freq_accent, freq_normal, sound_type):
        """Regenerates click buffers with new frequency and timbre."""
        self.click_freq_accent = freq_accent
        self.click_freq_normal = freq_normal
        self.click_type = sound_type
        self._generate_click_sounds()

    def _generate_click_sounds(self):
        t = np.linspace(0, 0.1, int(44100 * 0.1), False)
        fa = self.click_freq_accent
        fn = self.click_freq_normal
        
        if self.click_type == "Wood":
            # Wood block style: fundamental + higher harmonic with fast decay
            tic = (np.sin(2 * np.pi * fa * t) + 0.5 * np.sin(2 * np.pi * fa * 2.3 * t)) * np.exp(-t * 250)
            toc = (np.sin(2 * np.pi * fn * t) + 0.5 * np.sin(2 * np.pi * fn * 2.1 * t)) * np.exp(-t * 200)
        elif self.click_type == "Metal":
            # Metallic: Bright, high harmonics, slower decay
            tic = (np.sin(2 * np.pi * fa * t) + 0.7 * np.sin(2 * np.pi * fa * 1.5 * t) + 0.4 * np.sin(2 * np.pi * fa * 2.8 * t)) * np.exp(-t * 120)
            toc = (np.sin(2 * np.pi * fn * t) + 0.5 * np.sin(2 * np.pi * fn * 1.4 * t)) * np.exp(-t * 100)
        elif self.click_type == "Electronic":
            # Sharp digital pulse
            tic = np.sin(2 * np.pi * fa * t) * np.exp(-t * 450)
            toc = np.sin(2 * np.pi * fn * t) * np.exp(-t * 400)
        else: # Sine (Default)
            # Pure sine with a nice smooth decay
            tic = np.sin(2 * np.pi * fa * t) * np.exp(-t * 180)
            toc = np.sin(2 * np.pi * fn * t) * np.exp(-t * 130)
            
        self._base_tick_down = (tic + 0.15 * toc) * 0.9
        self._base_tick_up = toc * 0.7
        self._update_audio_buffers()

    def _update_audio_buffers(self):
        l_gain = min(1.0, 1.0 - self.click_pan)
        r_gain = min(1.0, 1.0 + self.click_pan)
        
        # Pre-mix with volume and pan
        new_down = np.zeros((len(self._base_tick_down), 2), dtype=np.float32)
        new_down[:, 0] = self._base_tick_down * l_gain * self.click_volume
        new_down[:, 1] = self._base_tick_down * r_gain * self.click_volume
        self.stereo_tick_down = new_down

        new_up = np.zeros((len(self._base_tick_up), 2), dtype=np.float32)
        new_up[:, 0] = self._base_tick_up * l_gain * self.click_volume
        new_up[:, 1] = self._base_tick_up * r_gain * self.click_volume
        self.stereo_tick_up = new_up

    def set_click_volume(self, val):
        self.click_volume = val
        self._update_audio_buffers()

    def set_click_pan(self, pan_value):
        self.click_pan = pan_value
        self._update_audio_buffers()

    def set_click_mute(self, val):
        self.click_mute = val
        
    def cleanup(self):
        self.stop() 
        if hasattr(self, 'dmx_socket'):
            self.dmx_socket.close()
        if hasattr(self, 'click_stream') and self.click_stream is not None:
            self.click_stream.stop()
            self.click_stream.close()
            self.click_stream = None