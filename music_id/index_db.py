import json
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from music_id.config import INDEX
from music_id.fingerprint import Fingerprint
from music_id.utils import chunked, ensure_dir


class FingerprintIndex:
    def __init__(self, db_path: Path, mode: str = "query"):
        self.db_path = Path(db_path)
        self.mode = mode
        self.in_transaction = False

        ensure_dir(self.db_path.parent)
        self.conn = sqlite3.connect(str(self.db_path))
        self._configure_connection(mode=mode)
        self._create_tables()

    def _configure_connection(self, mode: str) -> None:
        """
        Use different SQLite tuning for build vs query workloads.
        """
        self.conn.execute("PRAGMA temp_store=MEMORY;")
        self.conn.execute("PRAGMA foreign_keys=ON;")

        if mode == "build":
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self.conn.execute("PRAGMA cache_size=-262144;")
            self.conn.execute("PRAGMA mmap_size=268435456;")
            self.conn.execute("PRAGMA locking_mode=EXCLUSIVE;")
        else:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self.conn.execute("PRAGMA cache_size=-65536;")
            self.conn.execute("PRAGMA mmap_size=134217728;")

    def close(self) -> None:
        if self.in_transaction:
            self.commit()
        self.conn.close()

    def begin(self) -> None:
        if not self.in_transaction:
            self.conn.execute("BEGIN")
            self.in_transaction = True

    def commit(self) -> None:
        if self.in_transaction:
            self.conn.commit()
            self.in_transaction = False
        else:
            self.conn.commit()

    def rollback(self) -> None:
        if self.in_transaction:
            self.conn.rollback()
            self.in_transaction = False
        else:
            self.conn.rollback()

    def _auto_commit(self) -> None:
        if not self.in_transaction:
            self.conn.commit()

    def _create_tables(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                file_size INTEGER,
                mtime REAL,
                duration REAL,
                fingerprint_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS fingerprints (
                hash_value INTEGER NOT NULL,
                song_id INTEGER NOT NULL,
                anchor_time INTEGER NOT NULL,
                FOREIGN KEY(song_id) REFERENCES songs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_fingerprints_hash
            ON fingerprints(hash_value);

            CREATE INDEX IF NOT EXISTS idx_fingerprints_song
            ON fingerprints(song_id);

            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def clear(self) -> None:
        self.conn.executescript(
            """
            DELETE FROM fingerprints;
            DELETE FROM songs;
            DELETE FROM metadata;
            """
        )
        self._auto_commit()

    def set_metadata(self, key: str, value: Dict) -> None:
        self.conn.execute(
            """
            INSERT INTO metadata(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self._auto_commit()

    def get_metadata(self, key: str) -> Optional[Dict]:
        row = self.conn.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,),
        ).fetchone()
        if not row:
            return None
        return json.loads(row[0])

    def add_song(self, path: str, file_size: int, mtime: float, duration: float) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO songs(path, file_size, mtime, duration)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                file_size=excluded.file_size,
                mtime=excluded.mtime,
                duration=excluded.duration
            RETURNING id
            """,
            (path, file_size, mtime, duration),
        )
        row = cursor.fetchone()
        self._auto_commit()
        return int(row[0])

    def add_fingerprints(self, song_id: int, fingerprints: Sequence[Fingerprint]) -> None:
        self.conn.execute("DELETE FROM fingerprints WHERE song_id = ?", (song_id,))
        rows = [(fp.hash_value, song_id, fp.anchor_time) for fp in fingerprints]

        for batch in chunked(rows, INDEX.fingerprints_batch_size):
            self.conn.executemany(
                """
                INSERT INTO fingerprints(hash_value, song_id, anchor_time)
                VALUES (?, ?, ?)
                """,
                batch,
            )

        self.conn.execute(
            """
            UPDATE songs
            SET fingerprint_count = ?
            WHERE id = ?
            """,
            (len(fingerprints), song_id),
        )
        self._auto_commit()

    def add_song_result_batch(self, items: Sequence[Dict]) -> int:
        """
        Write multiple processed song results in one buffered DB pass.

        Each item must contain:
        - path
        - file_size
        - mtime
        - duration
        - fingerprints
        """
        if not items:
            return 0

        song_rows = [
            (
                item["path"],
                int(item["file_size"]),
                float(item["mtime"]),
                float(item["duration"]),
            )
            for item in items
        ]

        self.conn.executemany(
            """
            INSERT INTO songs(path, file_size, mtime, duration)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                file_size=excluded.file_size,
                mtime=excluded.mtime,
                duration=excluded.duration
            """,
            song_rows,
        )

        paths = [item["path"] for item in items]
        song_id_rows: List[Tuple[int, str]] = []
        for path_batch in chunked(paths, 800):
            placeholders = ",".join("?" for _ in path_batch)
            sql = f"""
                SELECT id, path
                FROM songs
                WHERE path IN ({placeholders})
            """
            rows = self.conn.execute(sql, path_batch).fetchall()
            song_id_rows.extend((int(row[0]), row[1]) for row in rows)

        song_id_map = {path: song_id for song_id, path in song_id_rows}
        song_ids = list(song_id_map.values())

        if song_ids:
            for id_batch in chunked(song_ids, 800):
                placeholders = ",".join("?" for _ in id_batch)
                sql = f"DELETE FROM fingerprints WHERE song_id IN ({placeholders})"
                self.conn.execute(sql, id_batch)

        fingerprint_rows: List[Tuple[int, int, int]] = []
        count_rows: List[Tuple[int, int]] = []

        for item in items:
            song_id = song_id_map[item["path"]]
            fps = item["fingerprints"]
            count_rows.append((len(fps), song_id))
            fingerprint_rows.extend((fp.hash_value, song_id, fp.anchor_time) for fp in fps)

        for batch in chunked(fingerprint_rows, INDEX.fingerprints_batch_size):
            self.conn.executemany(
                """
                INSERT INTO fingerprints(hash_value, song_id, anchor_time)
                VALUES (?, ?, ?)
                """,
                batch,
            )

        self.conn.executemany(
            """
            UPDATE songs
            SET fingerprint_count = ?
            WHERE id = ?
            """,
            count_rows,
        )

        self._auto_commit()
        return len(items)

    def remove_song_by_path(self, path: str) -> None:
        row = self.conn.execute(
            "SELECT id FROM songs WHERE path = ?",
            (path,),
        ).fetchone()
        if row:
            song_id = int(row[0])
            self.conn.execute("DELETE FROM fingerprints WHERE song_id = ?", (song_id,))
            self.conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))
            self._auto_commit()

    def remove_missing_songs(self, existing_paths: Iterable[str]) -> int:
        existing = set(existing_paths)
        rows = self.conn.execute("SELECT path FROM songs").fetchall()
        db_paths = {row[0] for row in rows}
        to_delete = sorted(db_paths - existing)

        for path in to_delete:
            self.remove_song_by_path(path)

        return len(to_delete)

    def get_song_path(self, song_id: int) -> Optional[str]:
        row = self.conn.execute(
            "SELECT path FROM songs WHERE id = ?",
            (song_id,),
        ).fetchone()
        return row[0] if row else None

    def get_song_info_map(self) -> Dict[int, Dict]:
        rows = self.conn.execute(
            """
            SELECT id, path, duration, fingerprint_count
            FROM songs
            ORDER BY id
            """
        ).fetchall()
        return {
            int(row[0]): {
                "path": row[1],
                "duration": row[2],
                "fingerprint_count": row[3],
            }
            for row in rows
        }

    def get_song_records_by_path(self) -> Dict[str, Dict]:
        rows = self.conn.execute(
            """
            SELECT id, path, file_size, mtime, duration, fingerprint_count
            FROM songs
            """
        ).fetchall()
        return {
            row[1]: {
                "id": int(row[0]),
                "path": row[1],
                "file_size": int(row[2]) if row[2] is not None else None,
                "mtime": float(row[3]) if row[3] is not None else None,
                "duration": float(row[4]) if row[4] is not None else None,
                "fingerprint_count": int(row[5]) if row[5] is not None else 0,
            }
            for row in rows
        }

    def get_song_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM songs").fetchone()
        return int(row[0]) if row else 0

    def get_fingerprint_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()
        return int(row[0]) if row else 0

    def query_hashes(self, hash_values: Iterable[int], chunk_size: int = 800) -> List[Tuple[int, int, int]]:
        values = list(hash_values)
        if not values:
            return []

        all_rows: List[Tuple[int, int, int]] = []
        for batch in chunked(values, chunk_size):
            placeholders = ",".join("?" for _ in batch)
            sql = f"""
                SELECT hash_value, song_id, anchor_time
                FROM fingerprints
                WHERE hash_value IN ({placeholders})
            """
            rows = self.conn.execute(sql, batch).fetchall()
            all_rows.extend((int(r[0]), int(r[1]), int(r[2])) for r in rows)

        return all_rows

    def optimize(self) -> None:
        self.conn.execute("ANALYZE;")
        self._auto_commit()