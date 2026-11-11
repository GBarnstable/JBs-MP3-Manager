"""Preview and apply literal Artist/Title tags from selected filenames."""

from pathlib import Path

from .media import digest_range

TAG_FIELDS = ("artist", "title")


def preview(songs, fields=TAG_FIELDS):
    fields = set(fields) & set(TAG_FIELDS)
    entries = []
    for song in songs:
        artist, separator, title = Path(song["path"]).stem.partition("--")
        derived = {"artist": artist.strip(), "title": title.strip()}
        values = {}
        if song.get("missing"):
            status = "Missing file — rescan first"
        elif not separator or not all(derived.values()):
            status = "Needs an artist--title filename with both parts"
        else:
            values = {field: derived[field] for field in fields if derived[field] != song[field]}
            status = "Update " + " and ".join(field.title() for field in TAG_FIELDS if field in values)
            if not values:
                status = "Already matches" if fields else "Choose Artist and/or Title"
        entries.append({**song, "derived": derived if separator else {}, "values": values, "status": status})
    return entries


def apply(library, updates, progress=lambda message: None):
    """Reuse ordinary tag backups/writes; a failed file does not cancel its peers."""
    result = {"updated": 0, "errors": []}
    for entry in updates:
        try:
            song = library.song(entry["id"])
            if song["path"] != entry["path"] or digest_range(library.path(song)) != entry["sha256"]:
                raise ValueError("File changed since preview. Rescan and review it again.")
            values = entry["values"]
            expected = preview([song], values)[0]["values"]
            if not values or expected != values:
                raise ValueError("Preview no longer matches the filename. Review it again.")
            report = library.edit_metadata([song["id"]], values, progress)
            result["updated"] += report["updated"]
            result["errors"].extend(report["errors"])
        except Exception as error:
            result["errors"].append({"path": entry["path"], "error": str(error)})
    return result
