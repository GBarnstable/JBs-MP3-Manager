"""Metadata-first filename proposals. Renaming never changes Artist/Title tags."""

import os
import re
from pathlib import Path

from .fileops import _batch, _execute, _original_path


def filename_artist(artist):
    artist = artist.strip()
    if artist.casefold() == "the the":
        return artist
    if re.match(r"(?i)^the\s+", artist):
        return re.sub(r"(?i)^the\s+", "", artist).strip() + ", The"
    return artist


def suggested_name(artist, title):
    if not artist.strip() or not title.strip():
        return ""
    name = filename_artist(artist) + "--" + title.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ")
    return name[:220].rstrip(". ") + ".mp3"


def validate_name(name):
    if (
        not name
        or len(name) > 240
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
        or name.endswith((" ", "."))
        or not name.lower().endswith(".mp3")
        or name.split(".")[0].upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *[f"COM{i}" for i in range(1, 10)],
            *[f"LPT{i}" for i in range(1, 10)],
        }
    ):
        raise ValueError(
            "Use a valid Windows MP3 filename without folder separators (maximum 240 characters)."
        )
    return name


def proposals(library, song_ids=None):
    songs = library.songs() if song_ids is None else [library.song(song_id) for song_id in song_ids]
    result = []
    for song in songs:
        if song["missing"]:
            continue
        stem = Path(song["path"]).stem
        parts = stem.split("--", 1)
        artist = song["artist"].strip() or (parts[0].strip() if len(parts) == 2 else "")
        title = song["title"].strip() or (parts[1].strip() if len(parts) == 2 else "")
        name = suggested_name(artist, title)
        if name == Path(song["path"]).name:
            continue
        result.append(
            {
                **song,
                "rename_artist": artist,
                "rename_title": title,
                "proposed": name,
                "basis": "Metadata"
                if song["artist"].strip() and song["title"].strip()
                else "Filename fallback / review needed",
            }
        )
    return result


def rename_files(library, approved, progress=lambda message: None):
    """approved contains only explicitly reviewed {id,name,sha256} rows."""
    if not approved:
        raise ValueError("Approve at least one filename.")
    sources, plans, seen = [], [], set()
    for item in approved:
        song = library.song(item["id"])
        name = validate_name(item["name"].strip())
        target = str(Path(song["path"]).with_name(name)).replace("\\", "/")
        key = target.casefold()
        if key in seen:
            raise ValueError("Two approved songs have the same proposed filename. Edit one of them.")
        seen.add(key)
        if target == song["path"]:
            continue
        absolute = _original_path(library, target)
        old = _original_path(library, song["path"])
        if absolute.exists() and os.path.normcase(str(absolute)) != os.path.normcase(str(old)):
            raise ValueError(f"A file already exists: {target}. Edit the proposed name.")
        if library.db.execute("SELECT 1 FROM songs WHERE path=? AND id<>?", (target, song["id"])).fetchone():
            raise ValueError("That filename belongs to an existing library record.")
        sources.append((song, item["sha256"]))
        plans.append((song, target))
    if not plans:
        with library.db:
            library.db.executemany("DELETE FROM new_arrivals WHERE song_id=?", [(i["id"],) for i in approved])
        return {"renamed": 0, "deleted": 0, "errors": [], "backup_folder": None}
    batch, manifest = _batch(library, "rename", sources)
    for move, (_, target) in zip(manifest["moves"], plans):
        move["target"] = target

    def mutate():
        for song, target in plans:
            library.db.execute("UPDATE songs SET path=? WHERE id=?", (target, song["id"]))
            library.db.execute(
                "UPDATE import_resolutions SET keeper_path=? WHERE song_id=?", (target, song["id"])
            )
        library.db.executemany("DELETE FROM new_arrivals WHERE song_id=?", [(i["id"],) for i in approved])

    return {**_execute(library, batch, manifest, mutate, progress), "renamed": len(plans)}
