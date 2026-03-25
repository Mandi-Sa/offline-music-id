import subprocess
import tempfile
import warnings
from pathlib import Path
from typing import Tuple

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import butter, filtfilt

from music_id.config import AUDIO, MATCH


class AudioLoadError(RuntimeError):
    pass


def load_audio(path: Path, sample_rate: int = AUDIO.sample_rate) -> Tuple[np.ndarray, int]:
    """
    Load audio with a resilient multi-backend fallback strategy:

    1. Try SoundFile first (usually stable for WAV / FLAC).
    2. Fallback to librosa for broader codec support.
    3. If both fail, fallback to system ffmpeg by transcoding to a temporary WAV.

    This improves compatibility with files that are technically playable by
    media players but not fully supported by Python audio backends.
    """
    path = Path(path)

    y = None
    sr = None
    errors = []

    try:
        y, sr = _load_with_soundfile(path)
    except Exception as exc:
        errors.append(f"soundfile: {exc}")

    if y is None or sr is None:
        try:
            y, sr = _load_with_librosa(path, sample_rate=sample_rate)
        except Exception as exc:
            errors.append(f"librosa: {exc}")

    if y is None or sr is None:
        try:
            y, sr = _load_with_ffmpeg(path, sample_rate=sample_rate)
        except Exception as exc:
            errors.append(f"ffmpeg: {exc}")

    if y is None or sr is None or len(y) == 0:
        hint = (
            "Please verify the file is readable. If needed, install ffmpeg or "
            "convert the file to a standard WAV / FLAC file first."
        )
        detail = " | ".join(errors) if errors else "unknown backend error"
        raise AudioLoadError(f"Failed to load audio file: {path}. {detail}. {hint}")

    if sr != sample_rate or not AUDIO.mono:
        if AUDIO.mono and y.ndim > 1:
            y = np.mean(y, axis=1)
        if sr != sample_rate:
            y = librosa.resample(np.asarray(y, dtype=np.float32), orig_sr=sr, target_sr=sample_rate)
            sr = sample_rate
    else:
        if AUDIO.mono and y.ndim > 1:
            y = np.mean(y, axis=1)

    y = np.asarray(y, dtype=np.float32)
    y = remove_dc(y)

    if AUDIO.highpass_cutoff_hz > 0:
        y = highpass_filter(y, sr, cutoff_hz=AUDIO.highpass_cutoff_hz)

    if AUDIO.normalize:
        y = peak_normalize(y)

    if AUDIO.pre_emphasis > 0:
        y = pre_emphasis(y, coeff=AUDIO.pre_emphasis)

    return y, sr


def _load_with_soundfile(path: Path) -> Tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), always_2d=False)
    if data is None or len(data) == 0:
        raise AudioLoadError(f"Empty audio returned by soundfile for: {path}")
    return np.asarray(data, dtype=np.float32), int(sr)


def _load_with_librosa(path: Path, sample_rate: int) -> Tuple[np.ndarray, int]:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="PySoundFile failed. Trying audioread instead.",
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message="librosa.core.audio.__audioread_load",
            category=FutureWarning,
        )
        y, sr = librosa.load(path, sr=sample_rate, mono=AUDIO.mono)

    if y is None or len(y) == 0:
        raise AudioLoadError(f"Empty audio returned by librosa for: {path}")
    return np.asarray(y, dtype=np.float32), int(sr)


def _load_with_ffmpeg(path: Path, sample_rate: int) -> Tuple[np.ndarray, int]:
    """
    Fallback path using system ffmpeg:
    transcode input audio to a temporary WAV file, then read with SoundFile.

    ffmpeg must be available in PATH.
    """
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            "1" if AUDIO.mono else "2",
            tmp.name,
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip() or "unknown ffmpeg error"
            raise AudioLoadError(stderr)

        data, sr = sf.read(tmp.name, always_2d=False)
        if data is None or len(data) == 0:
            raise AudioLoadError(f"Empty audio returned after ffmpeg transcoding for: {path}")

        return np.asarray(data, dtype=np.float32), int(sr)


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