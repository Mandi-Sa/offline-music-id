from dataclasses import dataclass
from pathlib import Path


SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a"}


@dataclass
class AudioConfig:
    sample_rate: int = 11025
    mono: bool = True
    normalize: bool = True
    pre_emphasis: float = 0.97
    highpass_cutoff_hz: float = 80.0


@dataclass
class SpectrogramConfig:
    n_fft: int = 2048
    hop_length: int = 256
    window: str = "hann"
    top_db: float = 80.0


@dataclass
class PeakDetectionConfig:
    amp_min_db: float = 18.0
    neighborhood_freq_bins: int = 17
    neighborhood_time_bins: int = 17
    max_peaks_per_frame: int = 5
    max_peaks_per_second: int = 32
    min_freq_hz: float = 120.0
    max_freq_hz: float = 5000.0
    min_frame_peak_percentile: float = 75.0


@dataclass
class FingerprintConfig:
    fan_value: int = 10
    target_zone_start_s: float = 0.35
    target_zone_end_s: float = 2.8
    max_targets_per_anchor: int = 10
    anchor_step: int = 1
    delta_t_quantization: int = 2
    freq_quantization_hz: int = 30
    hash_mod: int = 2**63 - 1
    include_freq_delta: bool = True


@dataclass
class MatchConfig:
    top_k: int = 3
    min_query_duration_s: float = 3.0
    min_confident_score: float = 10.0
    min_confident_matched_hashes: int = 18
    min_confident_coverage_ratio: float = 0.08
    min_confident_offset_ratio: float = 0.18
    offset_bin_size_frames: int = 2
    score_offset_weight: float = 0.55
    score_hash_weight: float = 0.25
    score_coverage_weight: float = 0.12
    score_concentration_weight: float = 0.08


import os
import json
from pathlib import Path
from dataclasses import asdict

CONFIG_FILE = Path("config.json")

def save_config():
    """Saves all global configuration objects to a JSON file."""
    config_data = {
        "AUDIO": asdict(AUDIO),
        "SPECTROGRAM": asdict(SPECTROGRAM),
        "PEAKS": asdict(PEAKS),
        "FINGERPRINT": asdict(FINGERPRINT),
        "MATCH": asdict(MATCH),
        "BUILD": asdict(BUILD),
        "DEBUG": asdict(DEBUG),
        "INDEX": asdict(INDEX),
    }
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
    except Exception as e:
        print(f"[ERROR] Failed to save config: {e}")

def load_config():
    """Loads configuration from a JSON file and updates global objects."""
    if not CONFIG_FILE.exists():
        return
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Map of object names to the actual global objects
            objs = {
                "AUDIO": AUDIO,
                "SPECTROGRAM": SPECTROGRAM,
                "PEAKS": PEAKS,
                "FINGERPRINT": FINGERPRINT,
                "MATCH": MATCH,
                "BUILD": BUILD,
                "DEBUG": DEBUG,
                "INDEX": INDEX,
            }
            for obj_name, values in data.items():
                obj = objs.get(obj_name)
                if obj:
                    for k, v in values.items():
                        setattr(obj, k, v)
    except Exception as e:
        print(f"[ERROR] Failed to load config: {e}")

@dataclass
class BuildConfig:
    sequential_scan: bool = True
    max_workers: int = os.cpu_count() or 2
    prefetch_window: int = 2
    commit_every_n_files: int = 32
    prefer_locality_order: bool = True


@dataclass
class DebugConfig:
    enabled: bool = True
    top_candidate_details: int = 3


@dataclass
class IndexConfig:
    index_dir_name: str = ".fingerprint_index"
    db_name: str = "index.db"
    metadata_name: str = "metadata.json"
    fingerprints_batch_size: int = 10000

    def index_dir(self, library_dir: Path) -> Path:
        return library_dir / self.index_dir_name

    def db_path(self, library_dir: Path) -> Path:
        return self.index_dir(library_dir) / self.db_name

    def metadata_path(self, library_dir: Path) -> Path:
        return self.index_dir(library_dir) / self.metadata_name


AUDIO = AudioConfig()
SPECTROGRAM = SpectrogramConfig()
PEAKS = PeakDetectionConfig()
FINGERPRINT = FingerprintConfig()
MATCH = MatchConfig()
BUILD = BuildConfig()
DEBUG = DebugConfig()
INDEX = IndexConfig()