import json
import shutil
from pathlib import Path

import pytest
from mutagen.id3 import APIC, ID3, TXXX
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from jbs_mp3_manager.app import MainWindow
from jbs_mp3_manager.discrepancies import apply, preview
from jbs_mp3_manager.discrepancy_dialog import DiscrepancyDialog
from jbs_mp3_manager.library import Library
from jbs_mp3_manager.media import inspect_mp3, write_tags


@pytest.fixture
def comparison_library(tmp_path, tones):
    root = tmp_path / "Music"
    root.mkdir()
    data = [
        ("ACDC--Back in Black.mp3", "A_C_D_C", "Back In Black"),
        ("Wrong artist--Wrong title.mp3", "The Who", "My Generation"),
        ("Same Artist--Same Title.mp3", "Same Artist", "Same Title"),
    ]
    for source, (name, artist, title) in zip(tones, data):
        shutil.copy2(source, root / name)
        write_tags(
            root / name, {"artist": artist, "title": title, "album": "Keep album"}, tmp_path / "initial-tags"
        )
    with Library(root, create=True) as lib:
        lib.scan()
        yield lib


def decide(rows, choices):
    return [{**row, "choice": choices[row["id"]]} for row in rows if row["id"] in choices]


def test_mixed_directions_preserve_audio_artwork_categories_and_identity(comparison_library):
    lib = comparison_library
    songs = {Path(s["path"]).name: s for s in lib.songs()}
    first = songs["ACDC--Back in Black.mp3"]
    second = songs["Wrong artist--Wrong title.mp3"]
    tags = ID3(lib.path(first))
    tags.add(TXXX(encoding=3, desc="custom", text=["preserve me"]))
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="cover", data=b"cover-test"))
    tags.save(lib.path(first))
    lib.scan()
    rows = preview(lib.songs())
    assert len(rows) == 2
    category = lib.add_category("Favorites")
    lib.assign([first["id"], second["id"]], [category])
    result = apply(lib, decide(rows, {first["id"]: "tags", second["id"]: "filename"}))
    assert result["updated"] == result["renamed"] == 1
    assert not result["errors"]
    assert lib.song(first["id"])["artist"] == "ACDC"
    assert lib.song(first["id"])["title"] == "Back in Black"
    renamed = lib.song(second["id"])
    assert renamed["path"] == "Who, The--My Generation.mp3"
    assert renamed["artist"] == "The Who"  # Filename direction does not overwrite tags.
    assert not lib.path(second).exists()
    assert lib.path(renamed).is_file()
    lib.scan()
    assert not preview(lib.songs())
    assert len(preview(lib.songs(), include_conventions=True)) == 1
    assert {s["id"] for s in lib.songs(category)} == {first["id"], second["id"]}
    for original in songs.values():
        current = lib.song(original["id"])
        assert inspect_mp3(lib.path(current))["audio_sha256"] == original["audio_sha256"]
        assert current["album"] == "Keep album"
    tags = ID3(lib.path(first))
    assert str(tags["TXXX:custom"]) == "preserve me"
    assert tags.getall("APIC")[0].data == b"cover-test"
    assert Path(result["rename_report"]).is_file()
    assert json.loads(Path(result["report_path"]).read_text())["updated"] == 1


def test_conflicting_rename_does_not_cancel_metadata_updates(comparison_library, tones):
    lib = comparison_library
    rows = preview(lib.songs())
    shutil.copy2(tones[2], lib.root / "Who, The--My Generation.mp3")
    before = (lib.root / "Who, The--My Generation.mp3").read_bytes()
    result = apply(
        lib, [{**r, "choice": "tags" if r["path"].startswith("ACDC") else "filename"} for r in rows]
    )
    assert result["updated"] == 1
    assert result["renamed"] == 0
    assert len(result["errors"]) == 1
    assert (lib.root / "Wrong artist--Wrong title.mp3").is_file()
    assert (lib.root / "Who, The--My Generation.mp3").read_bytes() == before


def test_stale_preview_cannot_overwrite_external_tags_and_other_choices_continue(comparison_library):
    lib = comparison_library
    rows = preview(lib.songs())
    first = next(r for r in rows if r["path"].startswith("ACDC"))
    write_tags(lib.path(first), {"artist": "Changed externally"}, lib.data / "tag-backups")
    result = apply(lib, [{**r, "choice": "tags" if r["id"] == first["id"] else "filename"} for r in rows])
    assert result["renamed"] == 1
    assert result["updated"] == 0
    assert "changed since review" in result["errors"][0]["error"]
    assert inspect_mp3(lib.path(first))["artist"] == "Changed externally"


def test_expected_conventions_can_be_hidden_or_reviewed_without_reversing_name_order():
    def song(path, artist, title):
        return {"id": path, "path": path, "artist": artist, "title": title}

    songs = [
        song("Who, The--My Generation.mp3", "The Who", "My Generation"),
        song("Eric Clapton--Wonderful Tonight.mp3", "Clapton, Eric", "Wonderful Tonight"),
        song("AC_DC--Track.mp3", "AC/DC", "Track"),
        song("ACDC--Track.mp3", "A_C_D_C", "Track"),
    ]
    assert [r["path"] for r in preview(songs)] == ["ACDC--Track.mp3"]
    all_rows = preview(songs, include_conventions=True)
    assert len(all_rows) == 4
    assert all(r["reasons"] == ["Convention only"] and "filename" in r["blocked"] for r in all_rows[:3])


def test_incomplete_and_legacy_names_offer_only_valid_directions(comparison_library):
    def song(path, artist, title):
        return {"id": path, "path": path, "artist": artist, "title": title}

    rows = preview(
        [
            song("No delimiter.mp3", "The Who", "My Generation"),
            song("ACDC--Track.mp3", "", ""),
            song("ACDC - Track.mp3", "Old artist", "Old title"),
            song("Sheryl Crow and Shelby Lynne--Track.mp3", "Crow, Sheryl and Shelby Lynne", "Track"),
            {**song("Missing--Track.mp3", "Other", "Other"), "missing": 1},
        ]
    )
    assert len(rows) == 4
    assert "tags" in rows[0]["blocked"] and "filename" not in rows[0]["blocked"]
    assert "filename" in rows[1]["blocked"] and "tags" not in rows[1]["blocked"]
    assert rows[2]["tag_values"] == {"artist": "ACDC", "title": "Track"}
    assert "ambiguous" in rows[3]["blocked"]["filename"]
    lib = comparison_library
    song = next(s for s in lib.songs() if s["path"].startswith("ACDC"))
    legacy = lib.root / "ACDC - Track.mp3"
    lib.path(song).rename(legacy)
    lib.scan()
    legacy_song = next(s for s in lib.songs() if s["path"] == legacy.name)
    result = apply(lib, [{**preview([legacy_song])[0], "choice": "tags"}])
    assert result["updated"] == 1
    assert lib.song(legacy_song["id"])["title"] == "Track"


def dialog_choices(dialog, mapping):
    for row in range(dialog.table.rowCount()):
        entry = dialog.entry(row)
        if entry["id"] in mapping:
            combo = dialog.table.cellWidget(row, 4)
            combo.setCurrentIndex(combo.findData(mapping[entry["id"]]))


def test_dialog_defaults_to_skip_and_choices_follow_songs_through_sort_filter(qapp, comparison_library):
    lib = comparison_library
    dialog = DiscrepancyDialog(lib.songs(), on_apply=lambda _: None, on_refresh=lambda: None)
    assert dialog.table.rowCount() == 2
    assert not dialog.decisions()
    assert not dialog.apply_button.isEnabled()
    first = next(s for s in lib.songs() if s["path"].startswith("ACDC"))
    dialog_choices(dialog, {first["id"]: "tags"})
    dialog.table.sortItems(0, Qt.SortOrder.DescendingOrder)
    dialog.search.setText("Wrong artist")
    assert "1 hidden" in dialog.summary.text()
    assert dialog.decisions()[0]["id"] == first["id"]
    assert dialog.decisions()[0]["choice"] == "tags"
    dialog.select_visible()
    dialog.set_choices("filename")
    assert {r["choice"] for r in dialog.decisions()} == {"tags", "filename"}
    dialog.search.clear()
    dialog.select_visible()
    dialog.set_choices("skip")
    assert not dialog.decisions()
    dialog.include_conventions.setChecked(True)
    assert dialog.table.rowCount() == 2  # Truly matching rows never appear.


def test_dialog_stays_open_rescans_and_blocks_duplicate_apply_and_close(qapp, comparison_library):
    lib = comparison_library
    batches = []
    dialog = DiscrepancyDialog(lib.songs(), on_apply=batches.append, on_refresh=lambda: None)
    dialog.show()
    qapp.processEvents()
    choices = {r["id"]: "tags" if r["path"].startswith("ACDC") else "filename" for r in preview(lib.songs())}
    dialog_choices(dialog, choices)
    dialog.apply()
    assert dialog.busy
    dialog.apply()
    dialog.reject()
    dialog.close()
    assert dialog.isVisible()
    assert len(batches) == 1
    result = apply(lib, batches[0])
    lib.scan()
    dialog.complete_action({"songs": lib.songs(), "result": result, "scan": {}})
    assert dialog.isVisible() and not dialog.busy
    assert dialog.table.rowCount() == 0
    assert dialog.reason.count() == 1
    assert not dialog.decisions()
    assert "Metadata updated: 1" in dialog.status.text()
    dialog.include_conventions.setChecked(True)
    assert dialog.table.rowCount() == 1
    dialog.fail_action("Reload failed")
    assert dialog.table.rowCount() == 0
    assert not dialog.apply_button.isEnabled()
    assert dialog.refresh_button.isEnabled()
    dialog.close()


def test_main_window_scans_scope_and_applies_chosen_direction(qapp, comparison_library, monkeypatch):
    lib = comparison_library
    first = next(s for s in lib.songs() if s["path"].startswith("ACDC"))
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(lib.root, scan=False)
    monkeypatch.setattr(
        window,
        "run_job",
        lambda operation, completed, **kwargs: completed(operation(window.library, lambda _: None)),
    )
    scans = []
    original_scan = Library.scan

    def scan(self, *args, **kwargs):
        scans.append(str(self.root))
        return original_scan(self, *args, **kwargs)

    monkeypatch.setattr(Library, "scan", scan)

    def review(dialog):
        assert dialog.table.rowCount() == 1
        dialog_choices(dialog, {first["id"]: "tags"})
        dialog.apply()
        assert dialog.table.rowCount() == 0
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(DiscrepancyDialog, "exec", review)
    window.review_discrepancies(song_ids=[first["id"]])
    assert len(scans) == 2
    assert lib.song(first["id"])["artist"] == "ACDC"
    assert (lib.root / "Wrong artist--Wrong title.mp3").exists()
    window.close()
