from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt

from jbs_mp3_manager.app import MainWindow
from jbs_mp3_manager.dialogs import ExportDialog
from jbs_mp3_manager.exports import ExportGroup, ExportRequest, execute_export
from jbs_mp3_manager.library import Library


def groups(library):
    songs = library.songs()
    return [
        ExportGroup("Dance", [song["id"] for song in songs[:2]]),
        ExportGroup("Favorites", [song["id"] for song in songs[1:]]),
    ]


def test_combined_export_deduplicates_and_creates_new_folder(library):
    result = execute_export(library, ExportRequest("files", "My mix", groups(library)))
    folder = library.root / "Exports" / "My mix"
    assert result["copied"] == 3
    assert result["errors"] == []
    assert len(list(folder.glob("*.mp3"))) + len(list(folder.glob("*.MP3"))) == 3
    assert result["destination"] == str(folder)
    again = execute_export(library, ExportRequest("files", "My mix", groups(library)))
    assert Path(again["destination"]).name == "My mix (2)"
    assert all(library.path(song).is_file() for song in library.songs())


def test_separate_export_uses_category_names_and_keeps_shared_song_in_each(library):
    result = execute_export(library, ExportRequest("files", "USB set", groups(library), separate=True))
    assert result["copied"] == 4
    folder = Path(result["destination"])
    assert sorted(p.name for p in folder.iterdir()) == ["Dance", "Favorites"]
    assert len(list((folder / "Dance").glob("*.m3u8"))) == 1
    assert len(list((folder / "Favorites").glob("*.m3u8"))) == 1


def test_recursive_scans_and_reopen_ignore_exports(library):
    library.set_setting("recursive", True)
    before = {song["id"] for song in library.songs()}
    execute_export(library, ExportRequest("files", "Copies", groups(library)))
    assert library.scan(verify=True)["added"] == 0
    with Library(library.root) as reopened:
        assert reopened.scan()["added"] == 0
        assert {song["id"] for song in reopened.songs()} == before


def test_playlist_export_defaults_under_exports_and_references_masters(library):
    result = execute_export(library, ExportRequest("playlist", "My mix", groups(library)))
    path = Path(result["playlists"][0])
    assert path == library.root / "Exports" / "Playlists" / "My mix.m3u8"
    references = [line for line in path.read_text("utf-8").splitlines() if not line.startswith("#")]
    assert len(references) == 3
    assert {(path.parent / reference).resolve() for reference in references} == {
        library.path(song) for song in library.songs()
    }
    again = execute_export(library, ExportRequest("playlist", "My mix", groups(library)))
    assert Path(again["playlists"][0]).name == "My mix (2).m3u8"


def test_multiple_playlists_and_filename_collisions(library):
    chosen = groups(library)
    chosen[0].name, chosen[1].name = "A/B", "A:B"
    result = execute_export(library, ExportRequest("playlist", "Both", chosen, separate=True))
    assert {Path(p).name for p in result["playlists"]} == {"A_B.m3u8", "A_B (2).m3u8"}


def test_empty_export_creates_nothing(library):
    with pytest.raises(ValueError, match="at least one"):
        execute_export(library, ExportRequest("files", "Empty", []))
    assert not (library.root / "Exports").exists()


def test_export_dialog_never_defaults_to_highlighted_song(qapp, library):
    selected = library.songs()[:1]
    dialog = ExportDialog(library, None, selected, "playlist")
    assert len(dialog.request().unique_ids()) == 3
    assert "Exports" in dialog.destination.text()
    assert "Playlists" in dialog.destination.text()
    dialog.source.setCurrentIndex(1)
    assert dialog.request().unique_ids() == [selected[0]["id"]]
    dialog.check_songs(True)
    assert len(dialog.request().unique_ids()) == 3
    dialog.check_songs(False)
    assert not dialog.request().unique_ids()
    dialog.close()


def test_export_dialog_categories_can_be_combined_or_separate(qapp, library):
    for group in groups(library):
        category = library.add_category(group.name)
        library.assign(group.song_ids, [category])
    dialog = ExportDialog(library, None, [], "files")
    dialog.source.setCurrentIndex(2)
    for i in range(1, dialog.category_list.count()):  # Skip Untagged.
        dialog.category_list.item(i).setCheckState(Qt.CheckState.Checked)
    request = dialog.request()
    assert len(request.groups) == 2
    assert len(request.unique_ids()) == 3
    assert not request.separate
    dialog.layout_choice.setCurrentIndex(1)
    assert dialog.request().separate
    assert "<category name>" in dialog.destination.text()
    dialog.close()


def test_play_only_queues_selected_songs_and_category_button_queues_category(qapp, library):
    window = MainWindow(restore=False, enable_audio=False)
    window.open_library(library.root, scan=False)
    captured = []
    window.playback = SimpleNamespace(
        set_queue=lambda tracks, first: captured.append(tracks), close=lambda: None
    )
    for row in (0, 2):
        window.table.selectionModel().select(
            window.proxy.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
    expected = {song["id"] for song in window.selected_songs()}
    window.play_selected()
    assert {song["id"] for song in captured[-1]} == expected
    assert len(captured[-1]) == 2
    window.play_category()
    assert len(captured[-1]) == 3
    window.close()
