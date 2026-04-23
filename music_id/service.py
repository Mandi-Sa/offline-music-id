from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import asdict
from pathlib import Path
from queue import Queue
import threading
from threading import Thread
from typing import Dict, List, Tuple, Optional, Callable

from tqdm import tqdm

from music_id.audio import AudioLoadError, get_duration_seconds, load_audio, validate_query_duration
from music_id.config import AUDIO, BUILD, DEBUG, FINGERPRINT, INDEX, MATCH, PEAKS, SPECTROGRAM
from music_id.fingerprint import extract_fingerprints
from music_id.index_db import FingerprintIndex
from music_id.matcher import MatchResult, match_query
from music_id.utils import ensure_dir, get_index_paths, save_json, scan_audio_files


class LibraryBuildError(RuntimeError):
    pass


class QueryError(RuntimeError):
    pass


class EmptyIndexError(RuntimeError):
    pass


def _process_audio_for_index(audio_path_str: str, stop_event: Optional[threading.Event] = None) -> Dict:
    """
    Worker-side full pipeline:
    read -> preprocess -> fingerprint extraction -> return serializable result.
    """
    audio_path = Path(audio_path_str)
    stat = audio_path.stat()
    file_size = int(stat.st_size)
    mtime = float(stat.st_mtime)

    y, sr = load_audio(audio_path, sample_rate=AUDIO.sample_rate)
    duration = get_duration_seconds(y, sr)
    _, fingerprints, _ = extract_fingerprints(y, sr, stop_event=stop_event)

    return {
        "path": str(audio_path),
        "file_size": file_size,
        "mtime": mtime,
        "duration": float(duration),
        "fingerprints": fingerprints,
    }


def _iter_files_to_update(
    files: List[Path],
    song_records: Dict[str, Dict],
    rebuild: bool,
) -> Tuple[List[Path], int]:
    to_process: List[Path] = []
    skipped = 0

    for audio_path in files:
        if rebuild:
            to_process.append(audio_path)
            continue

        stat = audio_path.stat()
        file_size = int(stat.st_size)
        mtime = float(stat.st_mtime)
        path_str = str(audio_path)

        existing = song_records.get(path_str)
        if (
            existing is not None
            and existing.get("file_size") == file_size
            and existing.get("mtime") == mtime
        ):
            skipped += 1
            continue

        to_process.append(audio_path)

    return to_process, skipped


import os

def _resolve_build_runtime(thread_count: int | None) -> Dict[str, int]:
    """
    Drive runtime build knobs from a single user-facing thread count.
    If thread_count is None, use the global BUILD.max_workers configuration.
    Capped at CPU cores * 2 to prevent resource exhaustion.
    """
    cpu_count = os.cpu_count() or 1
    limit = cpu_count * 2
    if thread_count is not None:
        effective_threads = max(1, min(thread_count, limit))
    else:
        effective_threads = max(1, min(BUILD.max_workers, limit))
        
    max_pending_futures = max(effective_threads * 4, 16)
    write_batch_size = max(effective_threads * 8, 16)
    write_queue_size = max(effective_threads * 4, 16)

    return {
        "effective_threads": effective_threads,
        "max_pending_futures": max_pending_futures,
        "write_batch_size": write_batch_size,
        "write_queue_size": write_queue_size,
    }


def _prepare_build_database(
    db_path: Path,
    existing_paths: List[str],
    rebuild: bool,
) -> int:
    """
    Do all pre-build DB maintenance in the main thread before the writer thread
    starts, so the writer owns the only active build-mode SQLite connection.
    """
    index = FingerprintIndex(db_path, mode="build")
    try:
        if rebuild:
            index.clear()
            return 0
        return index.remove_missing_songs(existing_paths)
    finally:
        index.close()


def _load_song_records(db_path: Path, rebuild: bool) -> Dict[str, Dict]:
    if rebuild:
        return {}

    index = FingerprintIndex(db_path, mode="query")
    try:
        return index.get_song_records_by_path()
    finally:
        index.close()


def _writer_loop(
    db_path: Path,
    write_queue: Queue,
    write_batch_size: int,
    state: Dict,
) -> None:
    """
    Dedicated writer thread:
    - owns SQLite connection
    - receives processed song results from queue
    - flushes buffered results in batches
    """
    index = FingerprintIndex(db_path, mode="build")
    buffer: List[Dict] = []
    written = 0

    try:
        while True:
            item = write_queue.get()
            if item is None:
                break

            buffer.append(item)
            if len(buffer) >= write_batch_size:
                written += index.add_song_result_batch(buffer)
                buffer.clear()

        if buffer:
            written += index.add_song_result_batch(buffer)
        state["written"] = written
    except Exception as exc:
        index.rollback()
        state["error"] = exc
    finally:
        index.close()


def _build_sequential_with_buffer(
    db_path: Path,
    files_to_process: List[Path],
    failed: List[Dict],
    write_batch_size: int,
    progress_callback: Optional[Callable] = None,
    log_callback: Optional[Callable[[str, str], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> int:
    write_queue: Queue = Queue(maxsize=max(write_batch_size, 1))
    state: Dict = {"written": 0, "error": None}
    writer = Thread(
        target=_writer_loop,
        args=(db_path, write_queue, write_batch_size, state),
        daemon=True,
    )
    writer.start()

    try:
        for i, audio_path in enumerate(files_to_process):
            if stop_event and stop_event.is_set():
                break
            if progress_callback:
                progress_callback(i + 1, len(files_to_process), f"Processing: {audio_path.name}")
            if log_callback:
                log_callback("DEBUG", f"处理文件 [{i+1}/{len(files_to_process)}]: {audio_path.name}")
            
            # To keep it simple and compatible with existing CLI,
            # I'll use a custom progress wrapper or just check for callback.
            try:
                item = _process_audio_for_index(str(audio_path), stop_event=stop_event)
                write_queue.put(item)
            except Exception as exc:
                if log_callback:
                    log_callback("WARN", f"文件处理失败 {audio_path.name}: {str(exc)}")
                failed.append(
                    {
                        "file": str(audio_path),
                        "error": str(exc),
                    }
                )
    finally:
        write_queue.put(None)
        writer.join()

    if state["error"] is not None:
        raise state["error"]

    return int(state["written"])


def _build_async_writer_pipeline(
    db_path: Path,
    files_to_process: List[Path],
    failed: List[Dict],
    thread_count: int,
    max_pending_futures: int,
    write_batch_size: int,
    write_queue_size: int,
    progress_callback: Optional[Callable] = None,
    log_callback: Optional[Callable[[str, str], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> int:
    """
    Async pipeline:
    - worker processes do read + compute
    - writer thread flushes results to SQLite in the background
    - queue provides backpressure but avoids compute/write lockstep
    """
    write_queue: Queue = Queue(maxsize=write_queue_size)
    writer_state: Dict = {"written": 0, "error": None}
    writer = Thread(
        target=_writer_loop,
        args=(db_path, write_queue, write_batch_size, writer_state),
        daemon=True,
    )
    writer.start()

    next_submit = 0
    in_flight: Dict[Future, str] = {}

    with ProcessPoolExecutor(max_workers=thread_count) as executor:
        progress = tqdm(total=len(files_to_process), desc="Building fingerprints", unit="file")
        processed_count = 0
        try:
            while next_submit < len(files_to_process) and len(in_flight) < max_pending_futures:
                if stop_event and stop_event.is_set():
                    break
                audio_path = str(files_to_process[next_submit])
                # Note: stop_event is a threading.Event and cannot be passed to ProcessPoolExecutor workers.
                # We only check it in the main loop.
                future = executor.submit(_process_audio_for_index, audio_path)
                in_flight[future] = audio_path
                if log_callback:
                    log_callback("DEBUG", f"提交任务: {audio_path}")
                next_submit += 1

            while in_flight:
                if stop_event and stop_event.is_set():
                    # We can't easily cancel futures already running in ProcessPoolExecutor,
                    # but we can stop processing their results and exit.
                    break
                done, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)

                for future in done:
                    audio_path = in_flight.pop(future)

                    try:
                        item = future.result()
                        if writer_state["error"] is not None:
                            raise writer_state["error"]
                        write_queue.put(item)
                        if log_callback:
                            log_callback("DEBUG", f"完成处理: {audio_path}")
                    except Exception as exc:
                        if log_callback:
                            log_callback("WARN", f"任务失败 {audio_path}: {str(exc)}")
                        failed.append(
                            {
                                "file": audio_path,
                                "error": str(exc),
                            }
                        )

                    progress.update(1)
                    processed_count += 1
                    if progress_callback:
                        progress_callback(processed_count, len(files_to_process), f"Processing... {processed_count}/{len(files_to_process)}")

                    while next_submit < len(files_to_process) and len(in_flight) < max_pending_futures:
                        if stop_event and stop_event.is_set():
                            break
                        next_audio_path = str(files_to_process[next_submit])
                        # Note: stop_event is a threading.Event and cannot be passed to ProcessPoolExecutor workers.
                        next_future = executor.submit(_process_audio_for_index, next_audio_path)
                        in_flight[next_future] = next_audio_path
                        next_submit += 1
        finally:
            progress.close()

    write_queue.put(None)
    writer.join()

    if writer_state["error"] is not None:
        raise writer_state["error"]

    return int(writer_state["written"])


def build_library(
    library_dir: Path,
    rebuild: bool = False,
    thread_count: int | None = None,
    progress_callback: Optional[Callable] = None,
    log_callback: Optional[Callable[[str, str], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> Dict:
    library_dir = Path(library_dir).resolve()
    files = scan_audio_files(library_dir)
    if not files:
        raise LibraryBuildError(f"No supported audio files found in library: {library_dir}")

    index_paths = get_index_paths(library_dir)
    db_path = index_paths["db_path"]
    ensure_dir(index_paths["index_dir"])

    existing_paths = [str(p) for p in files]
    song_records = _load_song_records(db_path, rebuild=rebuild)
    removed_files = _prepare_build_database(
        db_path=db_path,
        existing_paths=existing_paths,
        rebuild=rebuild,
    )

    files_to_process, skipped = _iter_files_to_update(files, song_records, rebuild=rebuild)
    if log_callback:
        log_callback("INFO", f"扫描完成: 发现 {len(files)} 个文件, 需处理 {len(files_to_process)} 个, 跳过 {skipped} 个")

    failed: List[Dict] = []
    runtime = _resolve_build_runtime(thread_count)
    effective_threads = runtime["effective_threads"]
    max_pending_futures = runtime["max_pending_futures"]
    write_batch_size = runtime["write_batch_size"]
    write_queue_size = runtime["write_queue_size"]

    if effective_threads <= 1:
        updated = _build_sequential_with_buffer(
            db_path=db_path,
            files_to_process=files_to_process,
            failed=failed,
            write_batch_size=write_batch_size,
            progress_callback=progress_callback,
            log_callback=log_callback,
            stop_event=stop_event,
        )
    else:
        updated = _build_async_writer_pipeline(
            db_path=db_path,
            files_to_process=files_to_process,
            failed=failed,
            thread_count=effective_threads,
            max_pending_futures=max_pending_futures,
            write_batch_size=write_batch_size,
            write_queue_size=write_queue_size,
            progress_callback=progress_callback,
            log_callback=log_callback,
            stop_event=stop_event,
        )

    index = FingerprintIndex(db_path, mode="build")
    try:
        index.optimize()
        metadata = build_metadata(
            library_dir=library_dir,
            song_count=index.get_song_count(),
            fingerprint_count=index.get_fingerprint_count(),
            processed_files=updated,
            failed_files=failed,
            skipped_files=skipped,
            updated_files=updated,
            removed_files=removed_files,
            rebuild=rebuild,
            thread_count=effective_threads,
            max_pending_futures=max_pending_futures,
            write_batch_size=write_batch_size,
            write_queue_size=write_queue_size,
        )
        index.set_metadata("library_info", metadata)
        save_json(index_paths["metadata_path"], metadata)
        return metadata
    finally:
        index.close()


def ensure_index_exists(
    library_dir: Path,
    auto_build: bool = True,
    thread_count: int | None = None,
) -> Path:
    index_paths = get_index_paths(library_dir)
    db_path = index_paths["db_path"]

    if db_path.exists():
        # Check if the database is actually populated
        index = FingerprintIndex(db_path, mode="query")
        try:
            if index.get_song_count() > 0:
                return db_path
            raise EmptyIndexError(f"Index file exists but is empty: {db_path}")
        finally:
            index.close()

    if not auto_build:
        raise FileNotFoundError(
            f"Fingerprint index not found: {db_path}. Run build command first."
        )

    build_library(library_dir, rebuild=False, thread_count=thread_count)
    return db_path


def get_library_info(library_dir: Path) -> Dict:
    """
    Retrieve detailed information about the library and its index status.
    """
    library_dir = Path(library_dir).resolve()
    files = scan_audio_files(library_dir)
    file_count = len(files)
    
    index_paths = get_index_paths(library_dir)
    db_path = index_paths["db_path"]
    
    info = {
        "library_dir": str(library_dir),
        "file_count": file_count,
        "index_exists": False,
        "db_mtime": None,
        "song_count": 0,
        "fingerprint_count": 0,
        "needs_update": True,
    }
    
    if db_path.exists():
        info["index_exists"] = True
        info["db_mtime"] = float(db_path.stat().st_mtime)
        
        try:
            index = FingerprintIndex(db_path, mode="query")
            song_count = index.get_song_count()
            info["song_count"] = song_count
            info["fingerprint_count"] = index.get_fingerprint_count()
            info["needs_update"] = (file_count != song_count)
            index.close()
        except Exception as e:
            print(f"[DEBUG] Error reading index for info: {e}")
            info["index_exists"] = False # Treat as not existing if corrupted
            
    return info

def query_library(
    library_dir: Path,
    query_file: Path,
    auto_build: bool = True,
    thread_count: int | None = None,
    progress_callback: Optional[Callable[[str], None]] = None,
    log_callback: Optional[Callable[[str, str], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> MatchResult:
    library_dir = Path(library_dir).resolve()
    query_file = Path(query_file).resolve()
    print(f"[DEBUG] query_library received library_dir={library_dir}, query_file={query_file}")

    if not query_file.exists():
        raise QueryError(f"Query file does not exist: {query_file}")

    db_path = ensure_index_exists(
        library_dir,
        auto_build=auto_build,
        thread_count=thread_count,
    )
    index = FingerprintIndex(db_path, mode="query")

    try:
        if stop_event and stop_event.is_set():
            raise QueryError("任务已停止")
        if progress_callback:
            progress_callback("加载音频...")
        if log_callback:
            log_callback("INFO", f"加载查询文件: {query_file.name}")
        try:
            y, sr = load_audio(query_file, sample_rate=AUDIO.sample_rate)
        except AudioLoadError as exc:
            raise QueryError(str(exc)) from exc

        if stop_event and stop_event.is_set():
            raise QueryError("任务已停止")
        if progress_callback:
            progress_callback("验证音频时长...")
        if log_callback:
            log_callback("DEBUG", "验证音频时长...")
        try:
            validate_query_duration(y, sr)
        except ValueError as exc:
            raise QueryError(str(exc)) from exc

        if stop_event and stop_event.is_set():
            raise QueryError("任务已停止")
        if progress_callback:
            progress_callback("提取指纹...")
        if log_callback:
            log_callback("INFO", "提取指纹中...")
        peaks, query_fingerprints, stats = extract_fingerprints(y, sr)
        if log_callback:
            log_callback("DEBUG", f"提取完成: {len(query_fingerprints)} 个指纹, {len(peaks)} 个峰值")
        if not query_fingerprints:
            raise QueryError("No fingerprints extracted from query audio.")

        if stop_event and stop_event.is_set():
            raise QueryError("任务已停止")
        if progress_callback:
            progress_callback("检索曲库...")
        if log_callback:
            log_callback("INFO", "开始检索曲库...")
        return match_query(
            query_fingerprints=query_fingerprints,
            index=index,
            sample_rate=sr,
            hop_length=SPECTROGRAM.hop_length,
            query_peak_count=stats.peak_count if stats.peak_count > 0 else len(peaks),
        )
    finally:
        index.close()


def build_metadata(
    library_dir: Path,
    song_count: int,
    fingerprint_count: int,
    processed_files: int,
    failed_files: List[Dict],
    skipped_files: int,
    updated_files: int,
    removed_files: int,
    rebuild: bool,
    thread_count: int,
    max_pending_futures: int,
    write_batch_size: int,
    write_queue_size: int,
) -> Dict:
    return {
        "library_dir": str(library_dir),
        "song_count": int(song_count),
        "fingerprint_count": int(fingerprint_count),
        "processed_files": int(processed_files),
        "skipped_files": int(skipped_files),
        "updated_files": int(updated_files),
        "removed_files": int(removed_files),
        "rebuild": bool(rebuild),
        "thread_count": int(thread_count),
        "max_pending_futures": int(max_pending_futures),
        "write_batch_size": int(write_batch_size),
        "write_queue_size": int(write_queue_size),
        "failed_files": failed_files,
        "config": {
            "audio": asdict(AUDIO),
            "spectrogram": asdict(SPECTROGRAM),
            "peaks": asdict(PEAKS),
            "fingerprint": asdict(FINGERPRINT),
            "match": asdict(MATCH),
            "build": asdict(BUILD),
            "debug": asdict(DEBUG),
            "index": asdict(INDEX),
        },
    }


def summarize_build_metadata(metadata: Dict, library_dir: Path) -> str:
    failed_files = metadata.get("failed_files", [])
    lines = [
        "Build finished:",
        f"  library: {library_dir}",
        f"  rebuild: {metadata.get('rebuild', False)}",
        f"  thread_count: {metadata.get('thread_count', 1)}",
        f"  max_pending_futures: {metadata.get('max_pending_futures', 0)}",
        f"  write_batch_size: {metadata.get('write_batch_size', 0)}",
        f"  write_queue_size: {metadata.get('write_queue_size', 0)}",
        f"  songs_indexed: {metadata.get('song_count', 0)}",
        f"  fingerprints: {metadata.get('fingerprint_count', 0)}",
        f"  processed_files: {metadata.get('processed_files', 0)}",
        f"  updated_files: {metadata.get('updated_files', 0)}",
        f"  skipped_files: {metadata.get('skipped_files', 0)}",
        f"  removed_files: {metadata.get('removed_files', 0)}",
        f"  failed_files: {len(failed_files)}",
    ]

    if failed_files:
        lines.append("  failed_examples:")
        for item in failed_files[:5]:
            lines.append(f"    - {item['file']}: {item['error']}")

    return "\n".join(lines) + "\n"