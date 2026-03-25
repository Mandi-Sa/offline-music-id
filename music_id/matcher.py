from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Sequence

from music_id.config import DEBUG, MATCH
from music_id.fingerprint import Fingerprint
from music_id.index_db import FingerprintIndex
from music_id.utils import format_seconds, seconds_from_frames


@dataclass
class MatchCandidate:
    song_id: int
    path: str
    score: float
    matched_hashes: int
    unique_matched_hashes: int
    coverage_ratio: float
    offset_concentration: float
    best_offset_frames: int
    best_offset_seconds: float
    best_offset_votes: int
    confidence: str
    confident: bool


@dataclass
class MatchDebugInfo:
    query_peak_count: int
    query_fingerprint_count: int
    query_unique_hash_count: int
    candidate_count: int
    candidate_summaries: List[Dict[str, float | int | str]]


@dataclass
class MatchResult:
    best: MatchCandidate | None
    top_candidates: List[MatchCandidate]
    query_fingerprint_count: int
    debug: MatchDebugInfo


def _confidence_label(
    score: float,
    matched_hashes: int,
    coverage_ratio: float,
    offset_concentration: float,
) -> tuple[str, bool]:
    confident = (
        score >= MATCH.min_confident_score
        and matched_hashes >= MATCH.min_confident_matched_hashes
        and coverage_ratio >= MATCH.min_confident_coverage_ratio
        and offset_concentration >= MATCH.min_confident_offset_ratio
    )

    if confident and score >= MATCH.min_confident_score * 1.8:
        return "high", True
    if confident:
        return "medium", True
    if score > 0:
        return "low", False
    return "none", False


def _compute_candidate_score(
    best_offset_votes: int,
    matched_hashes: int,
    coverage_ratio: float,
    offset_concentration: float,
) -> float:
    return (
        MATCH.score_offset_weight * float(best_offset_votes)
        + MATCH.score_hash_weight * float(matched_hashes)
        + MATCH.score_coverage_weight * float(coverage_ratio * 100.0)
        + MATCH.score_concentration_weight * float(offset_concentration * 100.0)
    )


def match_query(
    query_fingerprints: Sequence[Fingerprint],
    index: FingerprintIndex,
    sample_rate: int,
    hop_length: int,
    query_peak_count: int = 0,
) -> MatchResult:
    if not query_fingerprints:
        return MatchResult(
            best=None,
            top_candidates=[],
            query_fingerprint_count=0,
            debug=MatchDebugInfo(
                query_peak_count=query_peak_count,
                query_fingerprint_count=0,
                query_unique_hash_count=0,
                candidate_count=0,
                candidate_summaries=[],
            ),
        )

    query_map: Dict[int, List[int]] = defaultdict(list)
    for fp in query_fingerprints:
        query_map[fp.hash_value].append(fp.anchor_time)

    song_offset_votes: Dict[int, Counter] = defaultdict(Counter)
    song_matched_hashes: Counter = Counter()
    song_unique_hashes: Dict[int, set[int]] = defaultdict(set)

    all_hits = index.query_hashes(query_map.keys())
    for hash_value, song_id, db_anchor_time in all_hits:
        query_times = query_map.get(hash_value)
        if not query_times:
            continue

        song_unique_hashes[song_id].add(hash_value)
        for query_anchor_time in query_times:
            offset = db_anchor_time - query_anchor_time
            binned_offset = offset // MATCH.offset_bin_size_frames
            song_offset_votes[song_id][binned_offset] += 1
            song_matched_hashes[song_id] += 1

    if not song_offset_votes:
        return MatchResult(
            best=None,
            top_candidates=[],
            query_fingerprint_count=len(query_fingerprints),
            debug=MatchDebugInfo(
                query_peak_count=query_peak_count,
                query_fingerprint_count=len(query_fingerprints),
                query_unique_hash_count=len(query_map),
                candidate_count=0,
                candidate_summaries=[],
            ),
        )

    song_info_map = index.get_song_info_map()
    candidates: List[MatchCandidate] = []

    for song_id, offset_counter in song_offset_votes.items():
        if not offset_counter:
            continue

        best_offset_bin, best_votes = offset_counter.most_common(1)[0]
        best_offset_frames = best_offset_bin * MATCH.offset_bin_size_frames
        best_offset_seconds = seconds_from_frames(best_offset_frames, sample_rate, hop_length)

        matched_hashes = int(song_matched_hashes[song_id])
        unique_matched_hashes = len(song_unique_hashes[song_id])
        coverage_ratio = (
            0.0 if len(query_map) == 0 else unique_matched_hashes / float(len(query_map))
        )
        offset_concentration = (
            0.0 if matched_hashes == 0 else int(best_votes) / float(matched_hashes)
        )
        score = _compute_candidate_score(
            best_offset_votes=int(best_votes),
            matched_hashes=matched_hashes,
            coverage_ratio=coverage_ratio,
            offset_concentration=offset_concentration,
        )
        path = song_info_map.get(song_id, {}).get("path", f"<song:{song_id}>")
        confidence, confident = _confidence_label(
            score=score,
            matched_hashes=matched_hashes,
            coverage_ratio=coverage_ratio,
            offset_concentration=offset_concentration,
        )

        candidates.append(
            MatchCandidate(
                song_id=song_id,
                path=path,
                score=score,
                matched_hashes=matched_hashes,
                unique_matched_hashes=unique_matched_hashes,
                coverage_ratio=coverage_ratio,
                offset_concentration=offset_concentration,
                best_offset_frames=best_offset_frames,
                best_offset_seconds=best_offset_seconds,
                best_offset_votes=int(best_votes),
                confidence=confidence,
                confident=confident,
            )
        )

    candidates.sort(
        key=lambda c: (
            -c.score,
            -c.best_offset_votes,
            -c.unique_matched_hashes,
            c.path,
        )
    )
    top_candidates = candidates[: MATCH.top_k]
    best = top_candidates[0] if top_candidates else None

    debug_candidate_count = min(DEBUG.top_candidate_details, len(candidates))
    debug = MatchDebugInfo(
        query_peak_count=query_peak_count,
        query_fingerprint_count=len(query_fingerprints),
        query_unique_hash_count=len(query_map),
        candidate_count=len(candidates),
        candidate_summaries=[
            {
                "path": c.path,
                "score": round(c.score, 2),
                "matched_hashes": c.matched_hashes,
                "unique_matched_hashes": c.unique_matched_hashes,
                "best_offset_votes": c.best_offset_votes,
                "coverage_ratio": round(c.coverage_ratio, 4),
                "offset_concentration": round(c.offset_concentration, 4),
            }
            for c in candidates[:debug_candidate_count]
        ],
    )

    return MatchResult(
        best=best,
        top_candidates=top_candidates,
        query_fingerprint_count=len(query_fingerprints),
        debug=debug,
    )


def format_match_output(result: MatchResult) -> str:
    if not result.best:
        lines = ["No confident match found."]
        if DEBUG.enabled:
            lines.extend(
                [
                    "",
                    "Debug:",
                    f"  query_peaks: {result.debug.query_peak_count}",
                    f"  query_fingerprints: {result.debug.query_fingerprint_count}",
                    f"  query_unique_hashes: {result.debug.query_unique_hash_count}",
                    f"  candidate_count: {result.debug.candidate_count}",
                ]
            )
        return "\n".join(lines) + "\n"

    best = result.best
    lines = [
        "Best match:",
        f"  file: {best.path}",
        f"  score: {best.score:.2f}",
        f"  matched_hashes: {best.matched_hashes}",
        f"  unique_matched_hashes: {best.unique_matched_hashes}",
        f"  best_offset_votes: {best.best_offset_votes}",
        f"  best_offset: {format_seconds(best.best_offset_seconds)}",
        f"  coverage_ratio: {best.coverage_ratio:.3f}",
        f"  offset_concentration: {best.offset_concentration:.3f}",
        f"  confidence: {best.confidence}",
        f"  query_fingerprints: {result.query_fingerprint_count}",
        f"  confident: {'yes' if best.confident else 'no'}",
        "",
        "Top candidates:",
    ]

    for idx, candidate in enumerate(result.top_candidates, start=1):
        lines.append(
            f"  {idx}. {candidate.path}    "
            f"score={candidate.score:.2f} matched_hashes={candidate.matched_hashes} "
            f"best_offset_votes={candidate.best_offset_votes} "
            f"coverage={candidate.coverage_ratio:.3f} "
            f"concentration={candidate.offset_concentration:.3f} "
            f"offset={format_seconds(candidate.best_offset_seconds)} "
            f"confidence={candidate.confidence}"
        )

    if DEBUG.enabled:
        lines.extend(
            [
                "",
                "Debug:",
                f"  query_peaks: {result.debug.query_peak_count}",
                f"  query_fingerprints: {result.debug.query_fingerprint_count}",
                f"  query_unique_hashes: {result.debug.query_unique_hash_count}",
                f"  candidate_count: {result.debug.candidate_count}",
            ]
        )
        for idx, item in enumerate(result.debug.candidate_summaries, start=1):
            lines.append(
                f"  candidate_{idx}: path={item['path']} "
                f"score={item['score']} matched_hashes={item['matched_hashes']} "
                f"unique_matched_hashes={item['unique_matched_hashes']} "
                f"best_offset_votes={item['best_offset_votes']} "
                f"coverage={item['coverage_ratio']} concentration={item['offset_concentration']}"
            )

    if not best.confident:
        lines.append("")
        lines.append("No confident match found.")

    return "\n".join(lines) + "\n"