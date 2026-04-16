import ctypes
import numpy as np
import os

class RubberBandStretcher:
    # Options updated from rubberband-c.h file
    OptionProcessOffline      = 0x00000000
    OptionProcessRealTime     = 0x00000001
    OptionPitchHighConsistency= 0x00000200
    OptionTransposerHighQuality= 0x00001000 # <--- Added for HQ Pitch
    OptionStretchPrecise      = 0x00010000 # <--- Added for precision
    OptionWindowStandard      = 0x00000000
    OptionWindowShort         = 0x00010000
    OptionWindowLong          = 0x00020000

    def __init__(self, samplerate, channels, pitch_scale=1.0, time_ratio=1.0, options=None):
        self.lib = ctypes.CDLL("librubberband.so.2")
        self.channels = channels
        self.samplerate = samplerate

        # Prototipi
        self.lib.rubberband_new.argtypes = [ctypes.c_size_t, ctypes.c_size_t, ctypes.c_int, ctypes.c_double, ctypes.c_double]
        self.lib.rubberband_new.restype = ctypes.c_void_p
        
        # ... (restante codice dei prototipi invariato) ...
        self.lib.rubberband_delete.argtypes = [ctypes.c_void_p]
        self.lib.rubberband_process.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_float)), ctypes.c_size_t, ctypes.c_int]
        self.lib.rubberband_available.argtypes = [ctypes.c_void_p]
        self.lib.rubberband_available.restype = ctypes.c_size_t
        self.lib.rubberband_retrieve.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_float)), ctypes.c_size_t]
        self.lib.rubberband_retrieve.restype = ctypes.c_size_t
        self.lib.rubberband_set_pitch_scale.argtypes = [ctypes.c_void_p, ctypes.c_double]
        self.lib.rubberband_get_latency.argtypes = [ctypes.c_void_p]
        self.lib.rubberband_get_latency.restype = ctypes.c_size_t
        self.lib.rubberband_set_time_ratio.argtypes = [ctypes.c_void_p, ctypes.c_double]

        # If no options are passed, use a balanced default
        if options is None:
            options = self.OptionProcessRealTime | self.OptionPitchHighConsistency

        # Create instance with provided options
        self.handle = self.lib.rubberband_new(samplerate, channels, options, time_ratio, pitch_scale)

    def set_time_ratio(self, ratio):
        self.lib.rubberband_set_time_ratio(self.handle, ratio)

    # ... (restante classe invariata) ...
    def __del__(self):
        if hasattr(self, 'handle') and self.handle:
            self.lib.rubberband_delete(self.handle)

    def set_pitch_scale(self, scale):
        self.lib.rubberband_set_pitch_scale(self.handle, scale)

    def process_and_retrieve(self, input_chunk):
        samples = input_chunk.shape[0]
        in_ptrs = (ctypes.POINTER(ctypes.c_float) * self.channels)()
        for c in range(self.channels):
            channel_data = np.ascontiguousarray(input_chunk[:, c].astype(np.float32))
            in_ptrs[c] = channel_data.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
            setattr(self, f'_in_ref_{c}', channel_data)

        self.lib.rubberband_process(self.handle, in_ptrs, samples, 0)
        avail = self.lib.rubberband_available(self.handle)
        if avail == 0:
            return np.zeros((0, self.channels), dtype=np.float32)

        out_ptrs = (ctypes.POINTER(ctypes.c_float) * self.channels)()
        out_buffers = []
        for c in range(self.channels):
            buf = np.zeros(avail, dtype=np.float32)
            out_ptrs[c] = buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
            out_buffers.append(buf)

        self.lib.rubberband_retrieve(self.handle, out_ptrs, avail)
        return np.stack(out_buffers, axis=1)