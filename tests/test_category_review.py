import copy
import json
import shutil
import sqlite3

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

from jbs_mp3_manager.app import MainWindow
from jbs_mp3_manager.category_review import apply_review, export_review, preview, read_json
from jbs_mp3_manager.category_review_dialog import CategorySuggestionsDialog
from jbs_mp3_manager.library import Library
from jbs_mp3_manager.media import digest_range
from jbs_mp3_manager.naming import rename_files


@pytest.fixture
def exported(library):
    songs = library.songs()
    jazz = library.add_category("Jazz")
    library.assign([songs[0]["id"]], [jazz])
    library.edit_metadata([songs[0]["id"]], {"artist": "Björk & Friends", "title": "A Song", "genre": "Jazz"})
    path = library.root / "Exports" / "Category Review" / "review.json"
    result = export_review(library, path, "Suggest more songs for Jazz and a new evening category.")
    document = read_json(path)
    response = copy.deepcopy(document["suggestions_template"])
    response["new_categories"] = [
        {"name": "Evening", "description": "Gentle songs for a quiet evening."},
        {"name": "Unused category", "description": "Should not be created without approval."},
    ]
    response["suggestions"] = [
        {
            "song_id": songs[0]["id"],
            "category": "Jazz",
            "confidence": "high",
            "reason": "Already in this category.",
        },
        {
            "song_id": songs[1]["id"],
            "category": "Jazz",
            "confidence": "medium",
            "reason": "Fits the existing examples.",
        },
        {
            "song_id": songs[2]["id"],
            "category": "Evening",
            "confidence": "high",
            "reason": "An evening listening suggestion.",
        },
        {
            "song_id": songs[0]["id"],
            "category": "Unused category",
            "confidence": "low",
            "reason": "Leave this unapproved.",
        },
    ]
    return {"export": document, "response": response, "songs": songs, "jazz": jazz, "result": result}


def test_export_contains_portable_song_ids_categories_metadata_and_reply_template(library, exported):
    document = exported["export"]
    assert document["song_count"] == 3
    assert str(library.root) not in json.dumps(document, ensure_ascii=False)
    first = next(s for s in document["songs"] if s["song_id"] == exported["songs"][0]["id"])
    assert first["artist"] == "Björk & Friends"
    assert first["title"] == "A Song"
    assert first["categories"] == ["Jazz"]
    assert first["category_ids"] == [exported["jazz"]]
    assert not first["untagged"]
    assert sum(s["untagged"] for s in document["songs"]) == 2
    assert document["suggestions_template"]["source_export_id"] == document["export_id"]
    assert document["suggestions_template"]["library_id"] == document["library_id"]
    assert "suggest more" in document["goals"].casefold()
    assert not any(key in first for key in ("audio", "mp3_bytes", "absolute_path"))
    assert library.scan()["added"] == 0  # Review exports cannot become library songs.


def test_approved_additions_create_only_needed_categories_and_preserve_music(library, exported):
    original_hashes = {s["id"]: digest_range(library.path(s)) for s in library.songs()}
    review = preview(library, exported["response"])
    assert [r["status"] for r in review["rows"]] == ["Already assigned", "Ready", "Ready", "Ready"]
    result = apply_review(library, review, [1, 2])
    assert result["added"] == 2
    assert result["created_categories"] == ["Evening"]
    assert not result["errors"]
    assert {c["name"] for c in library.categories()} == {"Jazz", "Evening"}
    assert {s["id"] for s in library.songs(exported["jazz"])} == {s["id"] for s in exported["songs"][:2]}
    assert not library.songs("untagged")
    assert {s["id"]: digest_range(library.path(s)) for s in library.songs()} == original_hashes
    with sqlite3.connect(result["database_backup"]) as backup:
        assert backup.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 1
    report = read_json(result["report_path"])
    assert report["state"] == "applied" and report["added"] == 2
    assert len(report["approved_suggestions"]) == 2
    again = apply_review(library, review, [1, 2])
    assert again["added"] == 0 and not again["created_categories"]
    assert len(again["skipped"]) == 2
    second_path = library.root / "Exports" / "Category Review" / "review-again.json"
    export_review(library, second_path)
    evening = next(c for c in read_json(second_path)["categories"] if c["name"] == "Evening")
    assert evening["description"] == "Gentle songs for a quiet evening."


def test_invalid_suggestions_are_isolated_and_cannot_remove_memberships(library, exported):
    response = exported["response"]
    first = exported["songs"][0]["id"]
    response["suggestions"].extend(
        [
            {"song_id": "unknown-id", "category": "Jazz", "confidence": "high", "reason": "Unknown song."},
            {
                "song_id": first,
                "category": "Untagged",
                "confidence": "high",
                "reason": "Invalid virtual category.",
            },
            {"song_id": first, "category": "Undeclared", "confidence": "high", "reason": "Unknown category."},
            {
                "song_id": first,
                "category": "Jazz",
                "confidence": "high",
                "reason": "Remove it.",
                "action": "remove",
            },
            copy.deepcopy(response["suggestions"][1]),
            {
                "song_id": first,
                "category": "Evening",
                "confidence": "certain",
                "reason": "Invalid confidence.",
            },
        ]
    )
    review = preview(library, response)
    assert [r["status"] for r in review["rows"]][4:] == [
        "Invalid",
        "Invalid",
        "Invalid",
        "Invalid",
        "Duplicate suggestion",
        "Invalid",
    ]
    result = apply_review(library, review, [1, 4, 5, 6, 7, 8, 9])
    assert result["added"] == 1
    assert len(result["errors"]) == 5
    assert len(result["skipped"]) == 1
    assert first in {s["id"] for s in library.songs(exported["jazz"])}


def test_wrong_library_wrong_format_and_missing_export_are_rejected(library, exported, tmp_path):
    response = copy.deepcopy(exported["response"])
    response["library_id"] = "different-library"
    with pytest.raises(ValueError, match="different library"):
        preview(library, response)
    with pytest.raises(ValueError, match="not the library summary"):
        preview(library, exported["export"])
    response = copy.deepcopy(exported["response"])
    response["source_export_id"] = "../../outside"
    with pytest.raises(ValueError, match="export ID"):
        preview(library, response)
    response["source_export_id"] = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ValueError, match="original review export"):
        preview(library, response)
    path = tmp_path / "array.json"
    path.write_text("[]")
    with pytest.raises(ValueError, match="object"):
        read_json(path)


def test_missing_or_changed_recordings_are_blocked_but_metadata_edits_are_reviewable(
    library, exported, tones
):
    songs = exported["songs"]
    library.edit_metadata([songs[0]["id"]], {"title": "Corrected title"})
    shutil.copy2(tones[2], library.path(songs[1]))
    library.path(songs[2]).unlink()
    library.scan()
    review = preview(library, exported["response"])
    assert "changed since export" in review["rows"][0]["warning"]
    assert "Recording changed" in review["rows"][1]["error"]
    assert "Song is missing" in review["rows"][2]["error"]


def test_rename_and_move_library_keep_export_and_suggestion_identity(library, exported, tmp_path):
    song = library.song(exported["songs"][1]["id"])
    rename_files(
        library, [{"id": song["id"], "name": "New Artist--New Filename.mp3", "sha256": song["sha256"]}]
    )
    old_root = library.root
    library.close()
    new_root = tmp_path / "Moved music"
    shutil.move(old_root, new_root)
    with Library(new_root) as moved:
        review = preview(moved, exported["response"])
        assert review["rows"][1]["path"] == "New Artist--New Filename.mp3"
        result = apply_review(moved, review, [1])
        assert result["added"] == 1 and not result["errors"]
        assert song["id"] in {s["id"] for s in moved.songs(exported["jazz"])}


def test_changed_category_and_new_details_after_preview_require_review(library, exported):
    review = preview(library, exported["response"])
    library.rename_category(exported["jazz"], "Jazz renamed")
    result = apply_review(library, review, [1, 2])
    assert result["added"] == 1
    assert "renamed or removed" in result["errors"][0]["reason"]
    review = preview(library, exported["response"])
    library.edit_metadata([exported["songs"][0]["id"]], {"title": "Edited after preview"})
    result = apply_review(library, review, [3])
    assert result["added"] == 0
    assert "changed since preview" in result["errors"][0]["reason"]
    assert "Unused category" not in {c["name"] for c in library.categories()}


def test_sql_failure_rolls_back_categories_and_all_additions(library, exported):
    review = preview(library, exported["response"])
    library.db.execute(
        "CREATE TRIGGER block_add BEFORE INSERT ON memberships BEGIN SELECT RAISE(ABORT, 'Test failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="Test failure"):
        apply_review(library, review, [2])
    assert {c["name"] for c in library.categories()} == {"Jazz"}
    assert len(library.songs(exported["jazz"])) == 1


def test_scan_errors_and_invalid_new_categories_are_visible(library, exported, monkeypatch):
    response = exported["response"]
    response["new_categories"].append({"name": "All songs", "description": "Forbidden virtual category"})
    review = preview(
        library, response, scan_errors=[{"path": exported["songs"][1]["path"], "error": "Unreadable"}]
    )
    assert len(review["warnings"]) == 2
    assert "could not be verified" in review["rows"][1]["error"]
    valid_review = preview(library, response)
    original_scan = library.scan

    def scan(*args, **kwargs):
        result = original_scan(*args, **kwargs)
        result["errors"] = [{"path": exported["songs"][1]["path"], "error": "Unreadable"}]
        return result

    monkeypatch.setattr(library, "scan", scan)
    result = apply_review(library, valid_review, [1])
    assert result["added"] == 0
    assert "could not be verified" in result["errors"][0]["reason"]


def test_preview_approvals_filters_and_reordered_columns_keep_original_indices(qapp, library, exported):
    dialog = CategorySuggestionsDialog(preview(library, exported["response"]))
    assert not dialog.approved_indices() and not dialog.apply_button.isEnabled()
    dialog.table.horizontalHeader().moveSection(0, 6)
    dialog.confidence.setCurrentIndex(dialog.confidence.findData("high"))
    dialog.set_approvals(True)
    assert dialog.approved_indices() == [2]
    assert "1 new categories" in dialog.summary.text()
    dialog.search.setText("Nothing matches")
    assert "1 hidden" in dialog.summary.text()
    dialog.set_approvals(False, all_rows=True)
    assert not dialog.approved_indices()
    dialog.search.clear()
    dialog.confidence.setCurrentIndex(0)
    dialog.set_approvals(True)
    assert set(dialog.approved_indices()) == {1, 2, 3}
    assert dialog.table.horizontalHeader().sectionsMovable()
    assert dialog.new_table.horizontalHeader().sectionsMovable()


def test_export_and_import_menu_round_trip_and_fifteen_minute_rescan(qapp, library, tmp_path, monkeypatch):
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    assert window.rescan_timer.isActive() and window.rescan_timer.interval() == 15 * 60 * 1000
    assert window.debounce.interval() == 2500
    path = tmp_path / "library-review.json"
    monkeypatch.setattr(
        QInputDialog, "getMultiLineText", lambda *args, **kwargs: ("Suggest evening songs", True)
    )
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(
        window, "run_job", lambda operation, completed: completed(operation(window.library, lambda _: None))
    )
    menu = window.menuBar().actions()[0].menu()
    next(a for a in menu.actions() if a.text().startswith("Export library for category")).trigger()
    document = read_json(path)
    response = document["suggestions_template"]
    response["new_categories"] = [{"name": "Evening", "description": "Quiet evening listening"}]
    response["suggestions"] = [
        {
            "song_id": document["songs"][0]["song_id"],
            "category": "Evening",
            "confidence": "high",
            "reason": "A review example.",
        }
    ]
    response_path = tmp_path / "suggestions.json"
    response_path.write_text(json.dumps(response))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(response_path), ""))

    def approve(dialog):
        assert not dialog.approved_indices()
        dialog.set_approvals(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CategorySuggestionsDialog, "exec", approve)
    results = []
    monkeypatch.setattr(window, "category_suggestions_complete", results.append)
    next(a for a in menu.actions() if a.text() == "Import category suggestions…").trigger()
    assert results[0]["added"] == 1
    assert results[0]["created_categories"] == ["Evening"]
    assert len(library.songs("untagged")) == 2
    window.close()
