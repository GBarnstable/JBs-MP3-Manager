"""Explicit export plans with a dedicated, scan-excluded library destination."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

EXPORT_DIR = "Exports"


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(". ")[:120].rstrip(". ")
    if not name or name in (".", ".."):
        raise ValueError("Enter a name for this export.")
    if name.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *[f"COM{i}" for i in range(1, 10)],
        *[f"LPT{i}" for i in range(1, 10)],
    }:
        name = "_" + name
    return name


@dataclass
class ExportGroup:
    name: str
    song_ids: list[str]


@dataclass
class ExportRequest:
    kind: str
    name: str
    groups: list[ExportGroup]
    separate: bool = False

    def unique_ids(self):
        return list(dict.fromkeys(song_id for group in self.groups for song_id in group.song_ids))


def _new_folder(parent: Path, name: str) -> Path:
    number = 1
    while True:
        path = parent / (name if number == 1 else f"{name} ({number})")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            number += 1


def _new_playlist(parent: Path, name: str) -> Path:
    number = 1
    while True:
        path = parent / ((name if number == 1 else f"{name} ({number})") + ".m3u8")
        try:
            with path.open("xb"):
                pass
            return path
        except FileExistsError:
            number += 1


def execute_export(library, request: ExportRequest, progress=lambda message: None) -> dict:
    if request.kind not in ("playlist", "files"):
        raise ValueError("Choose playlist or MP3 export.")
    if not request.groups or not request.unique_ids():
        raise ValueError("Choose at least one song to export.")
    name = safe_name(request.name)
    # Validate the whole plan before creating files or folders.
    for song_id in request.unique_ids():
        song = library.song(song_id)
        if song["missing"] or not library.path(song).is_file():
            raise ValueError(f"Missing file: {song['path']}. Rescan and choose the export again.")
    root = library.export_root()
    result = {"copied": 0, "playlists": [], "errors": [], "destination": str(root)}
    groups = request.groups if request.separate else [ExportGroup(name, request.unique_ids())]
    if request.kind == "playlist":
        parent = root / "Playlists"
        if parent.is_symlink():
            raise ValueError("The Playlists export folder cannot be a link to another folder.")
        parent.mkdir(exist_ok=True)
        if request.separate:
            parent = _new_folder(parent, name)
        result["destination"] = str(parent)
        for group in groups:
            destination = _new_playlist(parent, safe_name(group.name))
            try:
                library.export_playlist(list(dict.fromkeys(group.song_ids)), destination)
                result["playlists"].append(str(destination))
            except Exception:
                destination.unlink(missing_ok=True)  # Remove only the placeholder we just reserved.
                raise
    else:
        parent = _new_folder(root, name)
        result["destination"] = str(parent)
        for group in groups:
            destination = _new_folder(parent, safe_name(group.name)) if request.separate else parent
            report = library.export_files(group.song_ids, destination, progress)
            result["copied"] += len(report["copied"])
            result["errors"].extend(report["errors"])
    return result
