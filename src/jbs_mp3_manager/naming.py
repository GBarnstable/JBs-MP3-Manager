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
    stem = Path(filename).stem
    parts = stem.split("--", 1)
    if len(parts) != 2:
        parts = re.split(r"\s+-\s*-\s+|\s+[-\u2013\u2014]\s+", stem, maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", "")


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
    if old_artist and old_title and old.stem != old_artist + "--" + old_title:
        reasons.append("Normalize artist/title separator" if "--" not in old.stem else "Remove extra spaces")
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


def proposals(library, song_ids=None):
    songs = library.songs() if song_ids is None else [library.song(song_id) for song_id in song_ids]
    result = []
    for song in songs:
        if song["missing"]:
            continue
        fallback_artist, fallback_title = filename_parts(song["path"])
        artist = song["artist"].strip() or fallback_artist
        retained_order = bool(
            fallback_artist
            and not comma_name_needs_review(fallback_artist)
            and comma_name_needs_review(artist)
        )
        if retained_order:
            artist = fallback_artist
        artist = filename_artist(artist)
        title = song["title"].strip() or fallback_title
        name = suggested_name(artist, title)
        if name == Path(song["path"]).name:
            continue
        result.append(
            {
                **song,
                "rename_artist": artist,
                "rename_title": title,
                "proposed": name,
                "reason": change_reason(song["path"], name),
                "basis": (
                    "Metadata"
                    if song["artist"].strip() and song["title"].strip()
                    else "Filename fallback / review needed"
                )
                + (
                    "; artist retained from filename instead of comma-form metadata" if retained_order else ""
                ),
            }
        )
    return result


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
