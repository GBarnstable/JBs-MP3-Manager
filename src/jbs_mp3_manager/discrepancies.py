"""Review filename/tag differences and apply an explicitly chosen direction."""

import json
import uuid
from pathlib import Path

from .library import atomic_text
from .media import digest_range
from .naming import (
    comma_name_needs_review,
    filename_artist,
    filename_components,
    filename_parts,
    natural_person_name,
    rename_files,
    suggested_name,
)

CHOICES = {
    "skip": "Leave unchanged",
    "tags": "Update metadata from filename",
    "filename": "Update filename from metadata",
}


def preview(songs, *, include_conventions=False):
    rows = []
    for song in songs:
        if song.get("missing"):
            continue
        artist, title = filename_parts(song["path"])
        parts = filename_components(song["path"])
        valid_filename = bool(artist and title)
        artist_diff = artist != song["artist"]
        title_diff = title != song["title"]
        format_diff = bool(parts and parts["separator"] != "--")
        if valid_filename and not (artist_diff or title_diff or format_diff):
            continue
        name = suggested_name(song["artist"], song["title"])
        convention_only = bool(
            valid_filename
            and name
            and not format_diff
            and (
                (
                    filename_artist(artist) == filename_artist(song["artist"])
                    and title == song["title"].strip()
                )
                or Path(song["path"]).stem == Path(name).stem
            )
        )
        if convention_only and not include_conventions:
            continue
        reasons = []
        if not valid_filename:
            reasons.append("Filename needs Artist / Title")
        else:
            if artist_diff:
                reasons.append("Artist")
            if title_diff:
                reasons.append("Title")
        if format_diff:
            reasons.append("Filename format")
        if not song["artist"].strip() or not song["title"].strip():
            reasons.append("Metadata missing Artist / Title")
        if convention_only:
            reasons = ["Convention only"]
        tags = (
            {field: value for field, value in (("artist", artist), ("title", title)) if value != song[field]}
            if valid_filename
            else {}
        )
        blocked = {}
        if not tags:
            blocked["tags"] = (
                "Filename needs both Artist and Title." if not valid_filename else "Tags already match."
            )
        if not name:
            blocked["filename"] = "Metadata needs both Artist and Title; edit the tags first."
        elif (
            comma_name_needs_review(song["artist"])
            and not natural_person_name(song["artist"])
            and song["artist"].strip() != artist
        ):
            blocked["filename"] = "Metadata artist name order is ambiguous; edit the tags first."
        elif name == Path(song["path"]).name:
            blocked["filename"] = "Filename already follows the metadata and filename conventions."
        rows.append(
            {
                **song,
                "file_artist": artist,
                "file_title": title,
                "tag_values": tags,
                "proposed_filename": name,
                "reasons": reasons,
                "blocked": blocked,
            }
        )
    return rows


def apply(library, decisions, progress=lambda message: None):
    result = {"updated": 0, "renamed": 0, "errors": [], "warnings": []}
    renames, seen = [], set()
    for entry in decisions:
        if entry.get("choice", "skip") == "skip":
            continue
        try:
            if entry["id"] in seen:
                raise ValueError("The same song has more than one decision.")
            seen.add(entry["id"])
            song = library.song(entry["id"])
            progress(f"Checking filename / metadata choice: {song['path']}")
            if song["path"] != entry["path"] or digest_range(library.path(song)) != entry["sha256"]:
                raise ValueError("File changed since review. Rescan and review it again.")
            current = preview([song], include_conventions=True)
            if not current:
                raise ValueError("The file no longer has a discrepancy. Rescan before continuing.")
            current = current[0]
            choice = entry["choice"]
            if choice not in ("tags", "filename") or choice in current["blocked"]:
                raise ValueError(current["blocked"].get(choice, "Choose a valid update direction."))
            if choice == "tags":
                if entry["tag_values"] != current["tag_values"]:
                    raise ValueError("The metadata preview changed. Rescan and review it again.")
                report = library.edit_metadata([song["id"]], current["tag_values"], progress)
                result["updated"] += report["updated"]
                result["errors"].extend(report["errors"])
            else:
                if entry["proposed_filename"] != current["proposed_filename"]:
                    raise ValueError("The filename preview changed. Rescan and review it again.")
                renames.append(
                    {"id": song["id"], "name": current["proposed_filename"], "sha256": entry["sha256"]}
                )
        except Exception as error:
            result["errors"].append({"path": entry["path"], "error": str(error)})
    if renames:
        report = rename_files(library, renames, progress)
        result["renamed"] = report["renamed"]
        result["errors"].extend(report["errors"])
        result["warnings"].extend(report["warnings"])
        result["rename_report"] = report["report_path"]
    report_path = library.data / "reports" / f"filename-metadata-{uuid.uuid4().hex}.json"
    result["report_path"] = str(report_path)
    result["decisions"] = [
        {"id": e["id"], "path": e["path"], "choice": e.get("choice", "skip")} for e in decisions
    ]
    try:
        atomic_text(report_path, json.dumps(result, ensure_ascii=False, indent=2))
    except OSError as error:
        result["report_path"] = None
        result["warnings"].append(f"Could not save the comparison report: {error}")
    return result
