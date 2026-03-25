from pathlib import Path
from typing import Tuple

import librosa
import numpy as np
from scipy.signal import butter, filtfilt

from music_id.config import AUDIO, MATCH


class AudioLoadError(RuntimeError):
    pass


def load_audio(path: Path, sample_rate: int = AUDIO.sample_rate) -> Tuple[np.ndarray, int]:
    """
    Load audio, resample to target sample rate, convert to mono and apply
    lightweight normalization / filtering / pre-emphasis for more stable
    fingerprinting under mobile-recorded playback conditions.
    """
    try:
        y, sr = librosa.load(path, sr=sample_rate, mono=AUDIO.mono)
    except Exception as exc:
        raise AudioLoadError(f"Failed to load audio file: {path}. {exc}") from exc

    if y is None or len(y) == 0:
        raise AudioLoadError(f"Audio file is empty or unreadable: {path}")

    y = np.asarray(y, dtype=np.float32)
    y = remove_dc(y)

    if AUDIO.highpass_cutoff_hz > 0:
        y = highpass_filter(y, sr, cutoff_hz=AUDIO.highpass_cutoff_hz)

    if AUDIO.normalize:
        y = peak_normalize(y)

    if AUDIO.pre_emphasis > 0:
        y = pre_emphasis(y, coeff=AUDIO.pre_emphasis)

    return y, sr


def remove_dc(y: np.ndarray) -> np.ndarray:
    if y.size == 0:
        return y
    return y - np.mean(y)


def highpass_filter(y: np.ndarray, sr: int, cutoff_hz: float, order: int = 3) -> np.ndarray:
    """
    Suppress low-frequency rumble / handling noise commonly present in
    mobile-recorded playback.
    """
    if y.size < max(16, order * 4):
        return y
    nyquist = sr / 2.0
    if cutoff_hz <= 0 or cutoff_hz >= nyquist * 0.95:
        return y

    normalized_cutoff = cutoff_hz / nyquist
    b, a = butter(order, normalized_cutoff, btype="highpass")
    try:
        filtered = filtfilt(b, a, y)
    except Exception:
        return y
    return np.asarray(filtered, dtype=np.float32)


def peak_normalize(y: np.ndarray, eps: float = 1e-8) -> np.ndarray:
    peak = np.max(np.abs(y))
    if peak < eps:
        return y
    return y / peak


def pre_emphasis(y: np.ndarray, coeff: float = 0.97) -> np.ndarray:
    if y.size < 2:
        return y
    emphasized = np.empty_like(y)
    emphasized[0] = y[0]
    emphasized[1:] = y[1:] - coeff * y[:-1]
    return emphasized


def get_duration_seconds(y: np.ndarray, sr: int) -> float:
    if sr <= 0:
        return 0.0
    return float(len(y)) / float(sr)


def validate_query_duration(y: np.ndarray, sr: int) -> None:
    duration = get_duration_seconds(y, sr)
    if duration < MATCH.min_query_duration_s:
        raise ValueError(
            f"Query audio is too short ({duration:.2f}s). "
            f"Please provide at least {MATCH.min_query_duration_s:.2f}s."
        )