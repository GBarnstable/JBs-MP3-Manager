"""Offline recording comparisons, with explicit limits and encoding-quality indicators."""

import json
import os
import re
import shutil
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

ALGORITHM = "chromaprint-raw-90-v1"


def tool_path():
    name = "fpcalc.exe" if sys.platform == "win32" else "fpcalc"
    paths = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "fingerprint" / name,
        Path(__file__).resolve().parents[2] / "build" / "fingerprint" / name,
    ]
    found = next((str(path) for path in paths if path.is_file()), None)
    return found or shutil.which("fpcalc")


def fingerprint(library, song):
    row = library.db.execute(
        "SELECT data FROM fingerprints WHERE audio_hash=? AND algorithm=?", (song["audio_sha256"], ALGORITHM)
    ).fetchone()
    if row:
        return json.loads(row[0])
    binary = tool_path()
    if not binary:
        raise ValueError("The local audio fingerprint tool is unavailable. Metadata comparisons still work.")
    path = library.path(song)
    before = path.stat()
    if (before.st_size, before.st_mtime_ns) != (song["size"], song["mtime_ns"]):
        raise ValueError("File changed; rescan before audio comparison.")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [binary, "-raw", "-json", "-length", "90", str(path)], capture_output=True, timeout=45, **options
    )
    try:
        raw = json.loads(result.stdout.decode("utf-8"))["fingerprint"]
        values = [int(value) & 0xFFFFFFFF for value in (raw.split(",") if isinstance(raw, str) else raw)]
    except (ValueError, KeyError, UnicodeError) as error:
        raise ValueError(
            "The file could not be fingerprinted (it may be too short or unreadable)."
        ) from error
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("File changed during audio comparison; rescan first.")
    with library.db:
        library.db.execute(
            "INSERT OR REPLACE INTO fingerprints VALUES (?, ?, ?)",
            (song["audio_sha256"], ALGORITHM, json.dumps(values)),
        )
    return values


def fingerprint_similarity(first, second):
    if min(len(first), len(second)) < 80 or min(len(set(first)), len(set(second))) < 8:
        return 0.0  # Short clips, silence and constant tones are insufficient evidence.
    best = 0.0
    for offset in range(-80, 81, 2):
        a_start, b_start = max(0, offset), max(0, -offset)
        overlap = min(len(first) - a_start, len(second) - b_start)
        if overlap < 80 or overlap < 0.75 * min(len(first), len(second)):
            continue
        step = max(1, overlap // 96)
        differences = [
            (first[a_start + i] ^ second[b_start + i]).bit_count() for i in range(0, overlap, step)
        ]
        best = max(best, 1 - sum(differences) / (32 * len(differences)))
    return best


def normalize(value):
    value = value.casefold().strip()
    value = re.sub(r",\s*the$", "", value)
    value = re.sub(r"^the\s+", "", value)
    return " ".join(re.findall(r"\w+", value))


def identity(song):
    parts = Path(song["path"]).stem.split("--", 1)
    return (
        normalize(song["artist"] or (parts[0] if len(parts) == 2 else "")),
        normalize(song["title"] or (parts[1] if len(parts) == 2 else "")),
    )


def quality_comparison(current, incoming):
    if current["audio_sha256"] == incoming["audio_sha256"]:
        return {"possible_upgrade": False, "text": "Identical encoded audio — same sound quality."}
    old, new = current.get("bitrate", 0), incoming.get("bitrate", 0)
    improved = bool(
        old
        and new > old * 1.12
        and incoming.get("sample_rate", 0) >= current.get("sample_rate", 0)
        and incoming.get("channels", 0) >= current.get("channels", 0)
    )
    if improved:
        text = f"Possible encoding-quality upgrade: {new / 1000:.0f} vs {old / 1000:.0f} kbps."
    elif old and new and old > new * 1.12:
        text = f"Current recording has the higher bitrate: {old / 1000:.0f} vs {new / 1000:.0f} kbps."
    else:
        text = "Encoding indicators are similar or mixed; there is no clear quality upgrade."
    return {
        "possible_upgrade": improved,
        "text": text + " Higher bitrate alone cannot prove better source quality.",
    }


def find_candidates(library, incoming_ids, progress=lambda message: None):
    songs = library.songs()
    by_id = {song["id"]: song for song in songs}
    order = {song_id: i for i, song_id in enumerate(incoming_ids)}
    results, errors = [], []
    for incoming_id in incoming_ids:
        if incoming_id not in by_id:
            continue
        incoming = by_id[incoming_id]
        artist, title = identity(incoming)
        pool = []
        for current in songs:
            if current["id"] == incoming_id or (
                current["id"] in order and order[current["id"]] > order[incoming_id]
            ):
                continue
            current_artist, current_title = identity(current)
            title_match = bool(title and current_title and title == current_title)
            artist_match = bool(
                artist
                and current_artist
                and (
                    artist in current_artist
                    or current_artist in artist
                    or SequenceMatcher(None, artist, current_artist).ratio() >= 0.8
                )
            )
            exact = incoming["audio_sha256"] == current["audio_sha256"]
            near_duration = abs(incoming["duration"] - current["duration"]) <= max(
                4, incoming["duration"] * 0.02
            )
            cached = (
                library.db.execute(
                    "SELECT 1 FROM fingerprints WHERE audio_hash=? AND algorithm=?",
                    (current["audio_sha256"], ALGORITHM),
                ).fetchone()
                is not None
            )
            if exact or title_match or near_duration:
                pool.append(
                    (
                        exact,
                        title_match,
                        artist_match,
                        cached,
                        -abs(incoming["duration"] - current["duration"]),
                        current,
                    )
                )
        pool.sort(key=lambda row: row[:5], reverse=True)
        incoming_fp = None
        decoded = 0
        for exact, title_match, artist_match, cached, _, current in pool:
            progress(f"Comparing {incoming['path']} with {current['path']}")
            score, match = (1.0, "Identical encoded audio") if exact else (0.0, "")
            if not exact and tool_path() and (cached or decoded < 8):
                try:
                    if incoming_fp is None:
                        incoming_fp = fingerprint(library, incoming)
                    score = fingerprint_similarity(incoming_fp, fingerprint(library, current))
                    decoded += int(not cached)
                    if score >= 0.90:
                        match = f"Likely same recording — audio fingerprint {score:.0%}"
                except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                    errors.append({"path": current["path"], "error": str(error)})
                    decoded += int(not cached)
            if not match and title_match and artist_match:
                match = "Artist/title metadata match — recording/version is unverified"
            if match:
                results.append(
                    {
                        "incoming": incoming,
                        "current": current,
                        "match": match,
                        "verified_audio": exact or score >= 0.90,
                        "score": score,
                        **quality_comparison(current, incoming),
                    }
                )
    results.sort(key=lambda row: (row["possible_upgrade"], row["verified_audio"], row["score"]), reverse=True)
    return {
        "pairs": results,
        "errors": errors,
        "note": "Audio checks use cached fingerprints and up to eight uncached duration/metadata candidates per new song. "
        "Build the audio index to improve matches across inconsistent names. Covers, edits, live versions and short clips need review.",
    }


def index_library(library, progress=lambda message: None):
    if not tool_path():
        raise ValueError("The local fingerprint tool is unavailable.")
    indexed, errors = 0, []
    songs = library.songs()
    for index, song in enumerate(songs):
        progress(f"Indexing audio {index + 1}/{len(songs)}: {song['path']}")
        try:
            fingerprint(library, song)
            indexed += 1
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            errors.append({"path": song["path"], "error": str(error)})
    return {"indexed": indexed, "errors": errors}


def apply_decisions(library, decisions, progress=lambda message: None):
    from .fileops import delete_incoming, replace_recording

    involved = [
        song["id"]
        for decision in decisions
        for song in (decision["pair"]["incoming"], decision["pair"]["current"])
    ]
    if len(involved) != len(set(involved)):
        raise ValueError("Handle shared alternatives in separate reviews; a recording was selected twice.")
    result = {"replaced": 0, "discarded": 0, "keeper_ids": [], "errors": []}
    for decision in decisions:
        pair = decision["pair"]
        incoming, current = pair["incoming"], pair["current"]
        try:
            if decision["action"] == 1:
                detail = replace_recording(
                    library, current["id"], incoming["id"], current["sha256"], incoming["sha256"], progress
                )
                result["replaced"] += 1
                result["keeper_ids"].append(current["id"])
            elif decision["action"] == 2:
                detail = delete_incoming(
                    library, incoming["id"], current["id"], incoming["sha256"], current["sha256"], progress
                )
                result["discarded"] += 1
            else:
                raise ValueError("Unknown comparison action")
            result["errors"].extend(detail["errors"])
        except Exception as error:
            result["errors"].append({"path": incoming["path"], "error": str(error)})
    return result
