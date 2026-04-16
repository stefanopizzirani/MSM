Music Studio Manager 2 (MSM2) is a comprehensive, Python-powered control hub designed for live musicians, bands, and studio engineers. Built with PyQt6, it serves as a central nervous system for performances, orchestrating everything from multi-stem audio playback to complex hardware automation.



Key Features:

Multi-Stem Audio Engine: 

High-performance playback with independent stem mixing, real-time pitch shifting (+/- 12 semitones), and time stretching using the RubberBand library. 

Includes parallel processing for pitched instruments and drums to maintain transient clarity.



Hardware Orchestration:

OSC: Deep integration with Behringer X-Air/XR18 mixers for fader control, mutes, and effects sync.

DMX: Integrated Art-Net controller for beat-synced lighting pulses.

MIDI: Precise MTC (MIDI Time Code) generation and timed MIDI CC/PC actions for automating external pedals and synths.

AI-Powered Setlist Optimizer: Analyze your library using local DSP or Spotify's API to calculate BPM, Key, and Energy. Use simulated annealing to automatically sort setlists for the best "harmonic flow" and "energy build."

Web Remote Control: A mobile-responsive dashboard with per-user roles (Tech, Musician, Singer, Drummer). It provides frame-accurate lyrics/chord mirroring, remote mixing, and a high-visibility visual metronome.

Integrated Content Creation: Built-in tools for AI stem separation (Demucs) and automated ChordPro lead sheet generation using Google Gemini AI.

Universal Input Mapping: Extensive support for Bluetooth page-turners, MIDI controllers, and global keyboard hotkeys to manage navigation and playback hands-free.



Tech Stack

Core: Python 3.10+, PyQt6

Audio: SoundDevice, SoundFile, RubberBand, Librosa, Numba (JIT DSP)

Networking: Flask-SocketIO, Zeroconf, Art-Net

AI: Google GenAI (Gemini), Demucs
