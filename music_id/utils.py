import json
from pathlib import Path
from typing import Dict, Iterable, List

from music_id.config import INDEX, SUPPORTED_EXTENSIONS


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def is_audio_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def scan_audio_files(library_dir: Path) -> List[Path]:
    if not library_dir.exists():
        raise FileNotFoundError(f"Library directory does not exist: {library_dir}")
    if not library_dir.is_dir():
        raise NotADirectoryError(f"Library path is not a directory: {library_dir}")

    files = [p for p in library_dir.rglob("*") if is_audio_file(p)]
    files.sort()
    return files


def get_index_paths(library_dir: Path) -> Dict[str, Path]:
    return {
        "index_dir": INDEX.index_dir(library_dir),
        "db_path": INDEX.db_path(library_dir),
        "metadata_path": INDEX.metadata_path(library_dir),
    }


def save_json(path: Path, data: Dict) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def relative_to_or_self(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def chunked(items: Iterable, batch_size: int):
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def seconds_from_frames(frame_index: int, sample_rate: int, hop_length: int) -> float:
    return frame_index * hop_length / float(sample_rate)


def format_seconds(value: float) -> str:
    return f"{value:.2f}s"