import shutil

import pytest
from mutagen.id3 import APIC, ID3, TXXX
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QDialog

from jbs_mp3_manager import library as library_module
from jbs_mp3_manager.app import MainWindow
from jbs_mp3_manager.filename_tag_dialog import FilenameTagDialog
from jbs_mp3_manager.filename_tags import apply, preview
from jbs_mp3_manager.library import Library
from jbs_mp3_manager.media import inspect_mp3, write_tags


@pytest.fixture
def named_library(tmp_path, tones):
    root = tmp_path / "Music"
    root.mkdir()
    names = ["ACDC--Back in Black.mp3", "Björk--Jóga.MP3", "No separator.mp3"]
    for source, name in zip(tones, names):
        shutil.copy2(source, root / name)
        write_tags(
            root / name,
            {"artist": "Old artist", "title": "Old title", "album": "Keep album"},
            tmp_path / "initial-tags",
        )
    with Library(root, create=True) as library:
        library.scan()
        yield library


def test_filename_tags_write_distinct_values_preserve_audio_artwork_and_categories(named_library):
    lib = named_library
    song = lib.songs()[0]
    lib.edit_metadata([song["id"]], {"artist": "A_C_D_C"})
    path = lib.path(song)
    tags = ID3(path)
    tags.add(TXXX(encoding=3, desc="custom", text=["Keep custom"]))
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="cover", data=b"keep cover"))
    tags.save(path)
    lib.scan()
    songs = lib.songs()
    audio_hashes = {s["id"]: s["audio_sha256"] for s in songs}
    category = lib.add_category("Favorites")
    lib.assign([s["id"] for s in songs], [category])
    entries = preview(songs)
    assert entries[0]["values"] == {"artist": "ACDC", "title": "Back in Black"}
    report = apply(lib, [e for e in entries if e["values"]])
    assert report == {"updated": 2, "errors": []}
    assert [(s["artist"], s["title"]) for s in lib.songs()][:2] == [
        ("ACDC", "Back in Black"),
        ("Björk", "Jóga"),
    ]
    lib.scan()
    assert {s["id"] for s in lib.songs(category)} == set(audio_hashes)
    for current in lib.songs():
        assert inspect_mp3(lib.path(current))["audio_sha256"] == audio_hashes[current["id"]]
        assert current["album"] == "Keep album"
    tags = ID3(path)
    assert str(tags["TXXX:custom"]) == "Keep custom"
    assert tags.getall("APIC")[0].data == b"keep cover"
    assert list((lib.data / "tag-backups").glob("*.id3"))


@pytest.mark.parametrize("field", ["artist", "title"])
def test_filename_tags_only_write_chosen_field_and_selected_file(named_library, field):
    lib = named_library
    song = lib.songs()[0]
    report = apply(lib, preview([song], [field]))
    assert report == {"updated": 1, "errors": []}
    current = lib.song(song["id"])
    other = "title" if field == "artist" else "artist"
    assert current[field] == {"artist": "ACDC", "title": "Back in Black"}[field]
    assert current[other] == song[other]
    assert lib.songs()[1]["artist"] == "Old artist"


def test_preview_keeps_literal_spelling_and_skips_unparseable_missing_and_unchanged():
    def song(path, **values):
        return {"id": path, "path": path, "artist": "", "title": "", **values}

    entries = preview(
        [
            song("Beatles, The--A Hard-Day's Night.mp3"),
            song("AC-DC -- Back--in Black.MP3"),
            song("ACDC--Back in Black.mp3", artist="ACDC", title="Back in Black"),
            song("No delimiter.mp3"),
            song("--Title.mp3"),
            song("Artist--.mp3"),
            song("Artist--Title.mp3", missing=1),
        ]
    )
    assert entries[0]["values"] == {"artist": "Beatles, The", "title": "A Hard-Day's Night"}
    assert entries[1]["values"] == {"artist": "AC-DC", "title": "Back--in Black"}
    assert all(not entry["values"] for entry in entries[2:])
    assert preview(entries[:1], [])[0]["values"] == {}


def test_failed_or_stale_file_does_not_cancel_other_metadata_updates(named_library, monkeypatch):
    lib = named_library
    entries = preview(lib.songs()[:2])
    write_tags(lib.path(entries[0]), {"artist": "Changed externally"}, lib.data / "tag-backups")
    result = apply(lib, entries)
    assert result["updated"] == 1
    assert "changed since preview" in result["errors"][0]["error"]
    assert inspect_mp3(lib.path(entries[0]))["artist"] == "Changed externally"
    lib.scan()
    original_write = library_module.write_tags

    def failing_write(path, values, backup):
        if path.name.startswith("ACDC"):
            raise PermissionError("Locked file")
        return original_write(path, values, backup)

    second = next(s for s in lib.songs() if s["path"].startswith("Björk"))
    lib.edit_metadata([second["id"]], {"artist": "Needs another update"})
    entries = preview([s for s in lib.songs() if "--" in s["path"]], ["artist"])
    monkeypatch.setattr("jbs_mp3_manager.library.write_tags", failing_write)
    result = apply(lib, entries)
    assert result["updated"] == 1
    assert result["errors"][0]["error"] == "Locked file"


def test_dialog_field_choices_approvals_and_sorting(qapp, named_library):
    dialog = FilenameTagDialog(named_library.songs())
    assert len(dialog.updates()) == 2
    dialog.set_approvals(False)
    assert not dialog.save.isEnabled()
    dialog.table.sortItems(1, Qt.SortOrder.DescendingOrder)
    for row in range(dialog.table.rowCount()):
        if dialog.table.item(row, 1).text().startswith("ACDC"):
            dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    dialog.fields["title"].setChecked(False)
    assert [e["values"] for e in dialog.updates()] == [{"artist": "ACDC"}]
    dialog.fields["artist"].setChecked(False)
    assert not dialog.updates()
    assert not dialog.save.isEnabled()
    dialog.fields["title"].setChecked(True)
    dialog.set_approvals(True)
    assert len(dialog.updates()) == 2
    assert all(set(e["values"]) == {"title"} for e in dialog.updates())


def test_main_window_updates_only_selection_after_preview(qapp, named_library, monkeypatch):
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(named_library.root, scan=False)
    window.table.selectionModel().select(
        window.proxy.index(0, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    selected = window.selected_songs()[0]
    assert selected["path"].startswith("ACDC")
    monkeypatch.setattr(FilenameTagDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        window, "run_job", lambda operation, completed: completed(operation(window.library, lambda _: None))
    )
    reports = []
    monkeypatch.setattr(window, "show_report", lambda title, report: reports.append(report))
    window.metadata_from_filenames()
    assert reports == [{"updated": 1, "errors": []}]
    assert named_library.song(selected["id"])["artist"] == "ACDC"
    assert named_library.songs()[1]["artist"] == "Old artist"
    window.close()
