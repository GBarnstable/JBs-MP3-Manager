"""Portable, reviewable category recommendations exchanged as JSON, never executable code."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .category_origins import REVIEW_CATEGORY, REVIEW_DESCRIPTION, manually_categorized
from .library import Library, atomic_text

REVIEW_FORMAT = "jbs-category-review"
SUGGESTIONS_FORMAT = "jbs-category-suggestions"
VERSION = 1
MAX_BYTES = 32 * 1024 * 1024


def read_json(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Choose a JSON file smaller than 32 MiB.")
    result = json.loads(path.read_text("utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError("The JSON file must contain an object.")
    return result


def _identifier(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid category-review export ID.") from None


def _name(value):
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError("Enter a category name of at most 1000 characters.")
    return Library.validate_category(value)


def export_review(library, destination, goals="", progress=lambda message: None):
    destination = Path(destination).expanduser().resolve().with_suffix(".json")
    if destination.is_relative_to(library.data):
        raise ValueError("Save the review in Exports or another folder outside the library database folder.")
    progress("Collecting songs and category examples…")
    library_id = library.setting("category_review_library_id")
    if not library_id:
        library_id = str(uuid.uuid4())
        library.set_setting("category_review_library_id", library_id)
    export_id = str(uuid.uuid4())
    memberships = {}
    manual = manually_categorized(library)
    ai = {}
    for row in library.db.execute("SELECT song_id,category_id FROM ai_memberships"):
        ai.setdefault(row["song_id"], []).append(row["category_id"])
    for row in library.db.execute("SELECT song_id,category_id FROM memberships ORDER BY category_id"):
        memberships.setdefault(row["song_id"], []).append(row["category_id"])
    descriptions = library.setting("category_review_descriptions", {})
    categories = [
        {
            "id": c["id"],
            "name": c["name"],
            "song_count": c["count"],
            "description": descriptions.get(str(c["id"]), ""),
        }
        for c in library.categories()
    ]
    names = {c["id"]: c["name"] for c in categories}
    songs = []
    for song in library.songs():
        category_ids = memberships.get(song["id"], [])
        songs.append(
            {
                "song_id": song["id"],
                "filename": song["path"],
                **{
                    field: song[field]
                    for field in (
                        "artist",
                        "title",
                        "album",
                        "genre",
                        "year",
                        "track",
                        "duration",
                        "bitrate",
                        "audio_sha256",
                    )
                },
                "category_ids": category_ids,
                "categories": [names[i] for i in category_ids],
                "untagged": not category_ids,
                "manually_categorized": song["id"] in manual,
                "ai_category_ids": ai.get(song["id"], []),
            }
        )
    template = {
        "format": SUGGESTIONS_FORMAT,
        "version": VERSION,
        "library_id": library_id,
        "source_export_id": export_id,
        "new_categories": [],
        "suggestions": [],
    }
    document = {
        "format": REVIEW_FORMAT,
        "version": VERSION,
        "library_id": library_id,
        "export_id": export_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "goals": str(goals),
        "song_count": len(songs),
        "categories": categories,
        "songs": songs,
        "review_instructions": (
            "Infer the user's category patterns from existing memberships, names and metadata. "
            "Recommend additional memberships and, when useful, new categories. No audio is included; "
            "give uncertain identities low confidence. Explain each suggestion. Do not invent song IDs. "
            "Return a JSON document using suggestions_template. Each suggestions entry needs song_id, "
            "category (exact existing name or declared new name), confidence (high, medium, low), "
            "and reason. Declare new_categories as objects with name and description. "
            "Only additions are supported. Do not remove memberships, rename categories, change files "
            "or assign the virtual Untagged, All songs or Missing files categories."
            " Review after AI tag is a follow-up queue, not a musical category; do not recommend it."
        ),
        "suggestions_template": template,
        "suggestion_entry_example": {
            "song_id": songs[0]["song_id"] if songs else "COPY_A_SONG_ID",
            "category": "COPY_AN_EXISTING_OR_DECLARE_A_NEW_CATEGORY_NAME",
            "confidence": "medium",
            "reason": "Explain the proposed fit.",
        },
    }
    snapshot = library.data / "category-review" / "exports" / f"{export_id}.json"
    text = json.dumps(document, ensure_ascii=False, indent=2)
    atomic_text(snapshot, text)
    atomic_text(destination, text)
    return {
        "path": str(destination),
        "songs": len(songs),
        "categories": len(categories),
        "export_id": export_id,
    }


def preview(library, document, *, scan_errors=()):
    if document.get("format") != SUGGESTIONS_FORMAT or document.get("version") != VERSION:
        raise ValueError("Choose a category-suggestions JSON file, not the library summary export.")
    if not library.setting("category_review_library_id") or document.get("library_id") != library.setting(
        "category_review_library_id"
    ):
        raise ValueError(
            "These suggestions belong to a different library. Export a review from this library first."
        )
    export_id = _identifier(document.get("source_export_id"))
    snapshot_path = library.data / "category-review" / "exports" / f"{export_id}.json"
    if not snapshot_path.is_file():
        raise ValueError(
            "The original review export is unavailable in this library. Export a new review first."
        )
    snapshot = read_json(snapshot_path)
    if snapshot.get("library_id") != document["library_id"] or snapshot.get("export_id") != export_id:
        raise ValueError("The saved review does not match these suggestions.")
    entries = document.get("suggestions")
    declared = document.get("new_categories", [])
    if (
        not isinstance(entries, list)
        or len(entries) > 50000
        or not isinstance(declared, list)
        or len(declared) > 5000
    ):
        raise ValueError("Use lists of up to 50,000 suggestions and 5,000 new categories.")
    warnings, new_categories = [], {}
    manual = manually_categorized(library)
    failed_paths = {error["path"] for error in scan_errors}
    if failed_paths:
        warnings.append(
            f"Rescan could not verify {len(failed_paths)} file(s); their suggestions are unavailable."
        )
    for index, category in enumerate(declared):
        try:
            name = _name(category["name"])
            description = category.get("description", "")
            if not isinstance(description, str) or len(description) > 4000:
                raise ValueError("Category descriptions must be text of at most 4000 characters.")
            if name.casefold() in new_categories:
                raise ValueError("Duplicate new-category name.")
            new_categories[name.casefold()] = {"name": name, "description": description}
        except (ValueError, TypeError, KeyError) as error:
            warnings.append(f"New category {index + 1}: {error}")
    baseline = {s["song_id"]: s for s in snapshot["songs"]}
    baseline_categories = {c["name"].casefold(): c["id"] for c in snapshot["categories"]}
    songs = {row["id"]: dict(row) for row in library.db.execute("SELECT * FROM songs")}
    categories = {}
    for category in library.categories():
        categories.setdefault(category["name"].casefold(), []).append(category)
    memberships = {(r[0], r[1]) for r in library.db.execute("SELECT song_id,category_id FROM memberships")}
    seen, rows = set(), []
    for index, entry in enumerate(entries):
        row = {
            "index": index,
            "song_id": "",
            "path": "",
            "artist": "",
            "title": "",
            "category": "",
            "category_id": None,
            "new": False,
            "confidence": "",
            "reason": "",
            "warning": "",
            "status": "Invalid",
            "error": "",
            "review_after_ai": False,
        }
        try:
            if not isinstance(entry, dict) or entry.get("action", "add") != "add":
                raise ValueError("Only song/category additions are supported.")
            song_id, category = entry.get("song_id"), _name(entry.get("category"))
            if category.casefold() == REVIEW_CATEGORY.casefold():
                raise ValueError(
                    "Review after AI tag is added automatically when needed, not as a recommendation."
                )
            row.update(song_id=song_id if isinstance(song_id, str) else "", category=category)
            confidence, reason = entry.get("confidence"), entry.get("reason")
            if not isinstance(confidence, str) or confidence.casefold() not in ("high", "medium", "low"):
                raise ValueError("Confidence must be high, medium or low.")
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
                raise ValueError("Include a reason of 1–4000 characters.")
            row.update(confidence=confidence.casefold(), reason=reason)
            if not isinstance(song_id, str) or song_id not in baseline or song_id not in songs:
                raise ValueError("Song ID is unknown or was not included in the original export.")
            song, original = songs[song_id], baseline[song_id]
            row.update(path=song["path"], artist=song["artist"], title=song["title"])
            row["review_after_ai"] = song_id not in manual
            if song["path"] in failed_paths:
                raise ValueError(
                    "File could not be verified during rescan; correct the file problem and rescan."
                )
            if song["missing"]:
                raise ValueError("Song is missing; restore it and rescan before importing suggestions.")
            if song["audio_sha256"] != original["audio_sha256"]:
                raise ValueError("Recording changed since export; export a new review for this song.")
            if any(song[f] != original[f] for f in ("artist", "title")):
                row["warning"] = "Artist/Title changed since export; review the recommendation carefully."
            matches = categories.get(category.casefold(), [])
            exact = [c for c in matches if c["name"] == category]
            if len(matches) > 1 and not exact:
                raise ValueError("Category name is ambiguous; use its exact spelling.")
            if matches:
                target = exact[0] if exact else matches[0]
                if (
                    category.casefold() in baseline_categories
                    and target["id"] != baseline_categories[category.casefold()]
                    and category.casefold() not in new_categories
                ):
                    raise ValueError("Category identity changed since export; export a new review.")
                row.update(category=target["name"], category_id=target["id"])
            elif category.casefold() in new_categories:
                row.update(category=new_categories[category.casefold()]["name"], new=True)
            elif category.casefold() in baseline_categories:
                raise ValueError("Category was renamed or removed since export; export a new review.")
            else:
                raise ValueError(
                    "Unknown category: declare it in new_categories or use an existing category name."
                )
            pair = (song_id, row["category"].casefold())
            if pair in seen:
                row.update(status="Duplicate suggestion")
            elif row["category_id"] is not None and (song_id, row["category_id"]) in memberships:
                row.update(status="Already assigned")
            else:
                row.update(status="Ready")
            seen.add(pair)
        except (ValueError, TypeError, KeyError) as error:
            row["error"] = str(error)
        rows.append(row)
    proposed = list(new_categories.values())
    if (
        REVIEW_CATEGORY.casefold() not in categories
        and any(r["status"] == "Ready" and r["review_after_ai"] for r in rows)
        and not any(c["name"].casefold() == REVIEW_CATEGORY.casefold() for c in proposed)
    ):
        proposed.append({"name": REVIEW_CATEGORY, "description": REVIEW_DESCRIPTION})
    return {
        "document": document,
        "rows": rows,
        "new_categories": proposed,
        "warnings": warnings,
    }


def apply_review(library, review, approved_indices, progress=lambda message: None):
    chosen = set(approved_indices)
    if any(type(i) is not int or i < 0 or i >= len(review["rows"]) for i in chosen):
        raise ValueError("Invalid approval selection. Reopen the suggestion file.")
    result = {
        "added": 0,
        "created_categories": [],
        "review_flagged": [],
        "skipped": [],
        "errors": [],
        "warnings": [],
    }
    if not chosen:
        return result
    # Refresh cached audio identities before making any category changes.
    scan = library.scan(progress=progress)
    result["scan_errors"] = scan["errors"]
    token = uuid.uuid4().hex
    backup = library.data / "category-review" / "backups" / f"{token}.sqlite3"
    backup.parent.mkdir(parents=True, exist_ok=True)
    result["database_backup"] = str(library.backup(backup))
    report_path = library.data / "reports" / f"category-suggestions-{token}.json"
    result.update(
        report_path=str(report_path),
        state="prepared",
        source_export_id=review["document"]["source_export_id"],
        approved_indices=sorted(chosen),
    )
    result["approved_suggestions"] = [review["rows"][i] for i in sorted(chosen)]
    atomic_text(report_path, json.dumps(result, ensure_ascii=False, indent=2))
    library.db.execute("BEGIN IMMEDIATE")
    try:
        fresh = preview(library, review["document"], scan_errors=scan["errors"])
        manual = manually_categorized(library)
        result["warnings"].extend(fresh["warnings"])
        descriptions = library.setting("category_review_descriptions", {})
        declarations = {c["name"].casefold(): c for c in fresh["new_categories"]}
        created = {}
        for index in sorted(chosen):
            row, original = fresh["rows"][index], review["rows"][index]
            progress(f"Applying category suggestion {index + 1}: {row['path']}")
            if row["status"] != "Ready":
                key = "errors" if row["status"] == "Invalid" else "skipped"
                result[key].append(
                    {
                        "index": index,
                        "path": row["path"],
                        "category": row["category"],
                        "reason": row["error"] or row["status"],
                    }
                )
                continue
            if original["status"] != "Ready" or any(
                row[f] != original[f]
                for f in ("song_id", "artist", "title", "category", "category_id", "new")
            ):
                result["errors"].append(
                    {"index": index, "reason": "Suggestion target changed since preview; reopen the file."}
                )
                continue
            category_id = row["category_id"]
            if category_id is None:
                name = row["category"]
                if name not in created:
                    category_id = library.db.execute(
                        "INSERT INTO categories(name) VALUES (?)", (name,)
                    ).lastrowid
                    created[name] = category_id
                    descriptions[str(category_id)] = declarations[name.casefold()]["description"]
                    result["created_categories"].append(name)
                category_id = created[name]
            added = library.db.execute(
                "INSERT OR IGNORE INTO memberships(song_id,category_id) VALUES (?, ?)",
                (row["song_id"], category_id),
            ).rowcount
            result["added"] += added
            if added:
                library.db.execute("INSERT INTO ai_memberships VALUES (?, ?)", (row["song_id"], category_id))
            if added and row["song_id"] not in manual:
                target = library.db.execute(
                    "SELECT id FROM categories WHERE name=? COLLATE NOCASE", (REVIEW_CATEGORY,)
                ).fetchone()
                if target is None:
                    review_id = library.db.execute(
                        "INSERT INTO categories(name) VALUES (?)", (REVIEW_CATEGORY,)
                    ).lastrowid
                    result["created_categories"].append(REVIEW_CATEGORY)
                    descriptions[str(review_id)] = REVIEW_DESCRIPTION
                else:
                    review_id = target[0]
                flagged = library.db.execute(
                    "INSERT OR IGNORE INTO memberships VALUES (?, ?)", (row["song_id"], review_id)
                ).rowcount
                if flagged:
                    library.db.execute(
                        "INSERT INTO ai_memberships VALUES (?, ?)", (row["song_id"], review_id)
                    )
                    result["review_flagged"].append(row["song_id"])
        library.db.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES (?, ?)",
            ("category_review_descriptions", json.dumps(descriptions, ensure_ascii=False)),
        )
        library.db.commit()
    except BaseException:
        library.db.rollback()
        raise
    result["state"] = "applied"
    try:
        atomic_text(report_path, json.dumps(result, ensure_ascii=False, indent=2))
    except OSError as error:
        result["warnings"].append(
            f"Category changes were saved, but the final report could not be saved: {error}"
        )
    return result
