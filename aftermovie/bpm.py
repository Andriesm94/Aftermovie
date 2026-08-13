"""BPM / beat-length detection for a song."""
from __future__ import annotations

import librosa


def detect_bpm(audio_path: str) -> float:
    """Estimate the tempo (BPM) of an audio file."""
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    return float(tempo)


def beat_duration_ms(bpm: float, beats_per_clip: int = 1) -> int:
    """Convert a BPM value into a clip duration in milliseconds."""
    return round(60_000 / bpm * beats_per_clip)
