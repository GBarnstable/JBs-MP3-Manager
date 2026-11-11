"""Metadata-first filename proposals. Renaming never changes Artist/Title tags."""

import json
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .fileops import _original_path, rename_independently
from .library import atomic_text
from .media import digest_range


def filename_parts(filename):
    """Recognize explicit separators without splitting hyphenated artist names."""
    parts = filename_components(filename)
    return (parts["artist"].strip(), parts["title"].strip()) if parts else ("", "")


def possible_first_name_first(artist):
    """Return a comparison form; callers verify it against the existing artist name."""
    if artist.count(",") != 1:
        return ""
    last, first = (part.strip() for part in artist.split(",", 1))
    if not last or not first or first.casefold() == "the":
        return ""
    return first + " " + last


def same_artist_name(first, second):
    return " ".join(first.split()).casefold() == " ".join(second.split()).casefold()


def natural_person_name(artist):
    """Convert a simple personal comma name; leave group/ambiguous names for review."""
    if re.search(r"\b(and|with|featuring|feat|ft|vs)\b|&", artist, re.IGNORECASE):
        return ""
    parts = [part.strip() for part in artist.split(",")]
    suffixes = {"jr", "sr", "ii", "iii", "iv", "v"}
    suffix = ""
    if len(parts) == 3 and parts[-1].rstrip(".").casefold() in suffixes:
        suffix = " " + parts.pop()
    if len(parts) != 2:
        return ""
    last, first = parts
    if first.casefold() == "the" or first.rstrip(".").casefold() in suffixes or not last or not first:
        return ""
    words = first.split()
    if len(words) == 2 and words[-1].rstrip(".").casefold() in suffixes:
        suffix = " " + words.pop() + suffix
    if len(words) != 1:
        return ""
    name_word = r"[^\W\d_]+(?:[-'.’][^\W\d_]+)*\.?"
    if not all(re.fullmatch(name_word, word) for word in [*last.split(), *words]):
        return ""
    return words[0] + " " + last + suffix


def comma_name_needs_review(artist):
    return "," in artist and artist.rsplit(",", 1)[1].strip().casefold() != "the"


def filename_artist(artist):
    artist = artist.strip()
    natural = natural_person_name(artist)
    if natural:
        return natural
    if artist.casefold() == "the the":
        return artist
    if re.match(r"(?i)^the\s+", artist):
        return re.sub(r"(?i)^the\s+", "", artist).strip() + ", The"
    return artist


def change_reason(current, proposed):
    if not proposed:
        return "Needs artist/title details"
    old, new = Path(current), Path(proposed)
    old_artist, old_title = filename_parts(current)
    new_artist, new_title = filename_parts(proposed)
    reasons = []
    if comma_name_needs_review(new_artist):
        reasons.append("Artist name order: manual review needed")
    if old_artist and new_artist and old_artist != new_artist:
        if same_artist_name(
            natural_person_name(old_artist) or possible_first_name_first(old_artist), new_artist
        ):
            reasons.append("Artist name order: first name first")
        elif re.match(r"(?i)^the\s+", old_artist) and filename_artist(old_artist) == new_artist:
            reasons.append("Move leading The to artist suffix")
        elif old_artist.casefold() == new_artist.casefold():
            reasons.append("Change artist letter case")
        else:
            reasons.append("Update artist spelling or filename characters")
    if old_title and new_title and old_title != new_title:
        reasons.append(
            "Change title letter case"
            if old_title.casefold() == new_title.casefold()
            else "Update song title"
        )
    old_parts, new_parts = filename_components(current), filename_components(proposed)
    if old_parts and new_parts:
        if old_parts["separator"] != new_parts["separator"]:
            reasons.append(
                "Normalize artist/title separator" if "--" not in old.stem else "Remove extra spaces"
            )
        elif any(
            (
                old_parts[key][: len(old_parts[key]) - len(old_parts[key].lstrip())],
                old_parts[key][len(old_parts[key].rstrip()) :],
            )
            != (
                new_parts[key][: len(new_parts[key]) - len(new_parts[key].lstrip())],
                new_parts[key][len(new_parts[key].rstrip()) :],
            )
            for key in ("artist", "title")
        ):
            reasons.append("Remove extra spaces")
    if old.stem != new.stem and not reasons:
        reasons.append("Update artist/title spelling or filename characters")
    if old.suffix != new.suffix:
        reasons.append(f"Extension {old.suffix} → {new.suffix}")
    return "; ".join(reasons) or "No filename change"


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


RENAME_RULES = {
    "artist_order": "Artist name order",
    "the": "Move leading The to artist suffix",
    "spacing": "Extra spaces",
    "separator": "Artist/title separator",
    "extension": "MP3 extension case",
    "metadata_artist": "Artist spelling from metadata",
    "metadata_title": "Song title from metadata",
    "metadata_name": "Build filename from metadata",
    "characters": "Filename characters / length",
}


def filename_components(filename):
    """Keep every original character so a single rule can leave other details intact."""
    path = Path(filename)
    stem = path.stem
    index = stem.find("--")
    if index >= 0:
        start, end = index, index + 2
        while start and stem[start - 1].isspace():
            start -= 1
        while end < len(stem) and stem[end].isspace():
            end += 1
    else:
        match = re.search(r"\s+-\s*-\s+", stem) or re.search(r"\s+[-–—]\s+", stem)
        if not match:
            return None
        start, end = match.span()
    return {
        "artist": stem[:start],
        "separator": stem[start:end],
        "title": stem[end:],
        "extension": path.suffix,
    }


def _join_components(parts):
    return parts["artist"] + parts["separator"] + parts["title"] + parts["extension"]


def _replace_content(original, content):
    leading = original[: len(original) - len(original.lstrip())]
    trailing = original[len(original.rstrip()) :]
    return leading + content + trailing


def _clean_component(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)


def _metadata_artist(song, current):
    metadata = song.get("artist", "").strip()
    if not metadata:
        return current.strip()
    if current.strip():
        if not comma_name_needs_review(current) and comma_name_needs_review(metadata):
            return current.strip()
        # A tag's display form must not undo the filename's The/order convention.
        if filename_artist(metadata) == filename_artist(current.strip()):
            return current.strip()

        def without_article(value):
            value = re.sub(r"(?i)^the\s+", "", value.strip())
            return re.sub(r"(?i),\s*the$", "", value)

        if without_article(metadata) == without_article(current):
            return current.strip()
    return metadata


def rename_operations(song, *, use_metadata):
    """Return independent previews, each changing exactly one kind of detail."""
    original = Path(song["path"]).name
    parts = filename_components(original)
    operations = {}
    original_path = Path(original)
    if original_path.suffix != ".mp3":
        operations["extension"] = original_path.stem + ".mp3"
    cleaned = _clean_component(original_path.stem)[:220] + original_path.suffix
    if cleaned != original:
        operations["characters"] = cleaned
    if parts is None or not parts["artist"].strip() or not parts["title"].strip():
        if original_path.stem.strip() != original_path.stem:
            operations["spacing"] = original_path.stem.strip() + original_path.suffix
        if use_metadata:
            fallback_artist, fallback_title = filename_parts(original)
            name = suggested_name(
                _metadata_artist(song, fallback_artist), song.get("title", "").strip() or fallback_title
            )
            if name and name != original:
                operations["metadata_name"] = name
        return operations

    def propose(rule, field, value):
        updated = {**parts, field: value}
        name = _join_components(updated)
        if name != original:
            operations[rule] = name

    artist = parts["artist"].strip()
    natural = natural_person_name(artist)
    if natural:
        propose("artist_order", "artist", _replace_content(parts["artist"], natural))
    if artist.casefold() != "the the" and re.match(r"(?i)^the\s+", artist):
        moved = re.sub(r"(?i)^the\s+", "", artist).strip() + ", The"
        propose("the", "artist", _replace_content(parts["artist"], moved))
    compact_separator = "".join(parts["separator"].split())
    spacing = {**parts, "artist": parts["artist"].strip(), "title": parts["title"].strip()}
    if compact_separator == "--":
        spacing["separator"] = "--"
    if _join_components(spacing) != original:
        operations["spacing"] = _join_components(spacing)
    if parts["separator"] != "--":
        propose("separator", "separator", "--")
    propose("extension", "extension", ".mp3")
    if use_metadata:
        metadata_artist = _metadata_artist(song, parts["artist"])
        if metadata_artist and metadata_artist != artist:
            propose(
                "metadata_artist",
                "artist",
                _replace_content(parts["artist"], _clean_component(metadata_artist)),
            )
        metadata_title = song.get("title", "").strip()
        if metadata_title and metadata_title != parts["title"].strip():
            propose(
                "metadata_title", "title", _replace_content(parts["title"], _clean_component(metadata_title))
            )
    cleaned = _clean_component(Path(original).stem)[:220] + parts["extension"]
    if cleaned != original:
        operations["characters"] = cleaned
    return operations


def rename_marks(library):
    return {
        row["song_id"]: {"path": row["path"], "audio_hash": row["audio_hash"]}
        for row in library.db.execute("SELECT * FROM rename_acceptances")
    }


def mark_names_correct(library, songs, correct=True):
    with library.db:
        for reviewed in songs:
            current = library.song(reviewed["id"])
            if (current["path"], current["audio_sha256"]) != (reviewed["path"], reviewed["audio_sha256"]):
                raise ValueError("A reviewed song changed; reopen rename review before marking it correct.")
            if correct:
                library.db.execute(
                    "INSERT OR REPLACE INTO rename_acceptances VALUES (?, ?, ?)",
                    (current["id"], current["path"], current["audio_sha256"]),
                )
                library.db.execute("DELETE FROM new_arrivals WHERE song_id=?", (current["id"],))
            else:
                library.db.execute("DELETE FROM rename_acceptances WHERE song_id=?", (current["id"],))
    return rename_marks(library)


def build_proposals(songs, *, use_metadata=True, reason=None, accepted=None, include_accepted=False):
    accepted = accepted or {}
    result = []
    for song in songs:
        if song.get("missing"):
            continue
        mark = accepted.get(song["id"])
        is_accepted = bool(
            mark and mark["path"] == song["path"] and mark["audio_hash"] == song.get("audio_sha256", "")
        )
        if is_accepted and not include_accepted:
            continue
        operations = rename_operations(song, use_metadata=use_metadata)
        fallback_artist, fallback_title = filename_parts(song["path"])
        artist = _metadata_artist(song, fallback_artist) if use_metadata else fallback_artist
        title = (song.get("title", "").strip() or fallback_title) if use_metadata else fallback_title
        full_name = suggested_name(artist, title)
        if reason is not None:
            if reason not in operations:
                continue
            else:
                name = operations[reason]
        else:
            name = full_name
        if is_accepted and not name:
            name = Path(song["path"]).name
        if name == Path(song["path"]).name and not is_accepted:
            continue
        if not operations and name and not is_accepted:
            continue
        proposed_artist, proposed_title = filename_parts(name) if name else (artist, title)
        explanation = change_reason(song["path"], name) if reason is None else RENAME_RULES[reason] + " only"
        if is_accepted:
            explanation = "Marked correct; " + explanation
        result.append(
            {
                **song,
                "rename_artist": proposed_artist or artist,
                "rename_title": proposed_title or title,
                "proposed": name,
                "full_proposed": full_name,
                "reason": explanation,
                "reason_ids": list(operations),
                "other_reasons": [RENAME_RULES[key] for key in operations if key != reason],
                "only_reason": reason,
                "accepted": is_accepted,
                "basis": "Filename only"
                if not use_metadata
                else "Metadata where available; filename fallback",
            }
        )
    return result


def proposals(library, song_ids=None, *, use_metadata=True, reason=None, include_accepted=False):
    songs = library.songs() if song_ids is None else [library.song(song_id) for song_id in song_ids]
    return build_proposals(
        songs,
        use_metadata=use_metadata,
        reason=reason,
        accepted=rename_marks(library),
        include_accepted=include_accepted,
    )


def rename_files(library, approved, progress=lambda message: None):
    """Apply approved filenames independently and report each failed file."""
    if not approved:
        raise ValueError("Approve at least one filename.")
    result = {"renamed": 0, "unchanged": 0, "deleted": 0, "errors": [], "warnings": [], "backup_folder": None}
    candidates, seen_ids = [], set()
    for index, item in enumerate(approved):
        path = item.get("id", "Unknown song")
        try:
            song = library.song(item["id"])
            path = song["path"]
            progress(f"Checking rename {index + 1}/{len(approved)}: {path}")
            if song["id"] in seen_ids:
                raise ValueError("The same song was approved more than once.")
            seen_ids.add(song["id"])
            if song["missing"]:
                raise ValueError("The original song is missing.")
            name = validate_name(item["name"].strip())
            target = str(Path(path).with_name(name)).replace("\\", "/")
            old, absolute = _original_path(library, path), _original_path(library, target)
            if not old.is_file():
                raise ValueError("The original song is no longer available.")
            if absolute.exists() and os.path.normcase(str(absolute)) != os.path.normcase(str(old)):
                raise ValueError(f"A file already exists: {target}. Edit the proposed name.")
            if library.db.execute(
                "SELECT 1 FROM songs WHERE path=? COLLATE NOCASE AND id<>?", (target, song["id"])
            ).fetchone():
                raise ValueError("That filename belongs to an existing library record.")
            candidates.append((song, target, item["sha256"]))
        except Exception as error:
            result["errors"].append({"path": path, "target": item.get("name", ""), "error": str(error)})
    counts = Counter(target.casefold() for _, target, _ in candidates)
    plans = []
    for song, target, expected in candidates:
        if counts[target.casefold()] > 1:
            result["errors"].append(
                {
                    "path": song["path"],
                    "target": target,
                    "error": "Multiple approved songs propose the same filename. Edit these proposals.",
                }
            )
        elif target == song["path"]:
            try:
                if digest_range(_original_path(library, target)) != expected:
                    raise ValueError("File changed since review; rescan and review again.")
                with library.db:
                    library.db.execute("DELETE FROM new_arrivals WHERE song_id=?", (song["id"],))
                result["unchanged"] += 1
            except Exception as error:
                result["errors"].append({"path": song["path"], "target": target, "error": str(error)})
        else:
            plans.append((song, target, expected))
    if plans:
        details = rename_independently(library, plans, progress)
        result["renamed"] = details["renamed"]
        result["backup_folder"] = details["backup_folder"]
        result["errors"].extend(details["errors"])
        result["warnings"].extend(details["warnings"])
    token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    report = library.data / "reports" / f"rename-{token}.json"
    result["report_path"] = str(report)
    try:
        atomic_text(report, json.dumps(result, ensure_ascii=False, indent=2))
    except OSError as error:
        result["report_path"] = None
        result["warnings"].append(f"Could not save the rename report: {error}")
    return result
