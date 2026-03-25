from dataclasses import dataclass
from typing import List, Sequence, Tuple

import librosa
import numpy as np
from scipy.ndimage import maximum_filter

from music_id.config import FINGERPRINT, PEAKS, SPECTROGRAM


@dataclass(frozen=True)
class Peak:
    freq_bin: int
    time_bin: int
    magnitude_db: float


@dataclass(frozen=True)
class Fingerprint:
    hash_value: int
    anchor_time: int
    freq1_bin: int
    freq2_bin: int
    delta_t_bin: int


@dataclass(frozen=True)
class FingerprintExtractionStats:
    peak_count: int
    fingerprint_count: int
    spectrogram_frames: int
    spectrogram_freq_bins: int


def compute_spectrogram_db(
    y: np.ndarray,
    sr: int,
    n_fft: int = SPECTROGRAM.n_fft,
    hop_length: int = SPECTROGRAM.hop_length,
) -> np.ndarray:
    """
    Compute magnitude spectrogram in dB scale.
    """
    stft = librosa.stft(
        y,
        n_fft=n_fft,
        hop_length=hop_length,
        window=SPECTROGRAM.window,
    )
    magnitude = np.abs(stft)
    spec_db = librosa.amplitude_to_db(magnitude, ref=np.max, top_db=SPECTROGRAM.top_db)
    return spec_db


def find_spectral_peaks(spec_db: np.ndarray, sr: int) -> List[Peak]:
    """
    Detect stable local maxima with basic noise suppression and peak-density control.

    Robustness strategies:
    - frequency band limiting to suppress low-frequency hum / handling noise
    - local-maximum filtering
    - absolute dB threshold
    - frame-adaptive percentile threshold
    - per-frame and global peak density limits
    """
    if spec_db.size == 0:
        return []

    n_fft = (spec_db.shape[0] - 1) * 2
    freqs_hz = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

    freq_mask = (
        (freqs_hz >= PEAKS.min_freq_hz)
        & (freqs_hz <= PEAKS.max_freq_hz)
    )
    if not np.any(freq_mask):
        return []

    working_spec = spec_db.copy()
    working_spec[~freq_mask, :] = -np.inf

    neighborhood = (
        PEAKS.neighborhood_freq_bins,
        PEAKS.neighborhood_time_bins,
    )
    local_max = maximum_filter(working_spec, size=neighborhood, mode="nearest")
    peak_mask = (working_spec == local_max) & np.isfinite(working_spec)

    num_frames = working_spec.shape[1]
    max_total_peaks = max(
        PEAKS.max_peaks_per_frame,
        int(
            round(
                (num_frames * SPECTROGRAM.hop_length / float(sr))
                * PEAKS.max_peaks_per_second
            )
        ),
    )

    peaks: List[Peak] = []
    for t in range(num_frames):
        frame_values = working_spec[:, t]
        finite_values = frame_values[np.isfinite(frame_values)]
        if finite_values.size == 0:
            continue

        frame_dynamic_threshold = np.percentile(
            finite_values,
            PEAKS.min_frame_peak_percentile,
        )
        threshold = max(frame_dynamic_threshold, -PEAKS.amp_min_db)

        freq_indices = np.flatnonzero(peak_mask[:, t] & (working_spec[:, t] >= threshold))
        if freq_indices.size == 0:
            continue

        magnitudes = working_spec[freq_indices, t]
        order = np.argsort(magnitudes)[::-1]
        selected = freq_indices[order[: PEAKS.max_peaks_per_frame]]

        for f in selected:
            peaks.append(
                Peak(
                    freq_bin=int(f),
                    time_bin=int(t),
                    magnitude_db=float(working_spec[f, t]),
                )
            )

    peaks.sort(key=lambda p: (-p.magnitude_db, p.time_bin, p.freq_bin))
    if len(peaks) > max_total_peaks:
        peaks = peaks[:max_total_peaks]

    peaks.sort(key=lambda p: (p.time_bin, -p.magnitude_db, p.freq_bin))
    return peaks


def build_fingerprints(
    peaks: Sequence[Peak],
    sr: int,
    hop_length: int = SPECTROGRAM.hop_length,
    n_fft: int = SPECTROGRAM.n_fft,
) -> List[Fingerprint]:
    """
    Build anchor-target fingerprints using a forward target zone.

    Improvements over the baseline:
    - configurable target zone start / end
    - configurable max targets per anchor
    - optional anchor decimation via anchor_step
    - richer hash with optional frequency delta component
    """
    if not peaks:
        return []

    start_dt_frames = max(1, int(round(FINGERPRINT.target_zone_start_s * sr / hop_length)))
    end_dt_frames = max(start_dt_frames + 1, int(round(FINGERPRINT.target_zone_end_s * sr / hop_length)))
    freq_resolution_hz = sr / float(n_fft)
    freq_quant = max(1, int(round(FINGERPRINT.freq_quantization_hz / max(freq_resolution_hz, 1e-9))))

    fingerprints: List[Fingerprint] = []

    for i in range(0, len(peaks), max(1, FINGERPRINT.anchor_step)):
        anchor = peaks[i]
        pair_count = 0

        for j in range(i + 1, len(peaks)):
            target = peaks[j]
            dt = target.time_bin - anchor.time_bin

            if dt < start_dt_frames:
                continue
            if dt > end_dt_frames:
                break

            f1_bin = anchor.freq_bin // freq_quant
            f2_bin = target.freq_bin // freq_quant
            dt_bin = max(1, dt // max(1, FINGERPRINT.delta_t_quantization))

            hash_value = hash_triplet(f1_bin, f2_bin, dt_bin)
            fingerprints.append(
                Fingerprint(
                    hash_value=hash_value,
                    anchor_time=anchor.time_bin,
                    freq1_bin=int(f1_bin),
                    freq2_bin=int(f2_bin),
                    delta_t_bin=int(dt_bin),
                )
            )

            pair_count += 1
            if pair_count >= min(FINGERPRINT.fan_value, FINGERPRINT.max_targets_per_anchor):
                break

    return fingerprints


def hash_triplet(freq1_bin: int, freq2_bin: int, delta_t_bin: int) -> int:
    """
    Compact deterministic integer hash for inverted index key.

    To improve hash discreteness for noisy mobile recordings, include
    frequency-delta information when enabled.
    """
    freq_delta = max(0, freq2_bin - freq1_bin)

    if FINGERPRINT.include_freq_delta:
        value = (
            (int(freq1_bin) & 0xFFFF) << 40
            | (int(freq2_bin) & 0xFFFF) << 24
            | (int(freq_delta) & 0xFF) << 16
            | (int(delta_t_bin) & 0xFFFF)
        )
    else:
        value = (
            (int(freq1_bin) & 0xFFFF) << 32
            | (int(freq2_bin) & 0xFFFF) << 16
            | (int(delta_t_bin) & 0xFFFF)
        )

    return int(value % FINGERPRINT.hash_mod)


def extract_fingerprints(
    y: np.ndarray,
    sr: int,
) -> Tuple[List[Peak], List[Fingerprint], FingerprintExtractionStats]:
    spec_db = compute_spectrogram_db(y, sr)
    peaks = find_spectral_peaks(spec_db, sr=sr)
    fingerprints = build_fingerprints(peaks, sr)
    stats = FingerprintExtractionStats(
        peak_count=len(peaks),
        fingerprint_count=len(fingerprints),
        spectrogram_frames=int(spec_db.shape[1]),
        spectrogram_freq_bins=int(spec_db.shape[0]),
    )
    return peaks, fingerprints, stats