from PySide6.QtCore import QItemSelectionModel, Qt

from jbs_mp3_manager.arrival_dialogs import RenameDialog, RenameOneDialog
from jbs_mp3_manager.library import Library
from jbs_mp3_manager.naming import build_proposals, mark_names_correct, proposals, rename_files, rename_marks


def named_song(library, name):
    song = library.songs()[0]
    library.path(song).rename(library.root / name)
    library.scan()
    return library.song(song["id"])


def test_artist_order_spacing_and_extension_are_three_independent_file_operations(library):
    song = named_song(library, "Performer, Alex -- A Song.MP3")
    library.edit_metadata([song["id"]], {"artist": "Unrelated Artist", "title": "Unrelated Title"})
    category = library.add_category("Favorites")
    library.assign([song["id"]], [category])
    before = library.path(song).read_bytes()
    steps = [
        ("artist_order", "Alex Performer -- A Song.MP3"),
        ("spacing", "Alex Performer--A Song.MP3"),
        ("extension", "Alex Performer--A Song.mp3"),
    ]
    for reason, expected in steps:
        item = proposals(library, [song["id"]], use_metadata=False, reason=reason)[0]
        assert item["proposed"] == expected
        result = rename_files(
            library, [{"id": song["id"], "name": item["proposed"], "sha256": item["sha256"]}]
        )
        assert result["renamed"] == 1 and not result["errors"]
        assert library.song(song["id"])["path"] == expected
        assert library.path(library.song(song["id"])).read_bytes() == before
        assert proposals(library, [song["id"]], use_metadata=False, reason=reason) == []
    assert library.songs(category)[0]["id"] == song["id"]


def test_the_rule_uses_filename_even_when_optional_metadata_disagrees(library):
    song = named_song(library, "The Example Band -- A Song.MP3")
    library.edit_metadata([song["id"]], {"artist": "Different Artist", "title": "Different Title"})
    item = proposals(library, [song["id"]], use_metadata=True, reason="the")[0]
    assert item["proposed"] == "Example Band, The -- A Song.MP3"
    assert "spacing" in item["reason_ids"] and "extension" in item["reason_ids"]
    assert "metadata_artist" in item["reason_ids"]
    off = proposals(library, [song["id"]], use_metadata=False)[0]
    assert off["proposed"] == "Example Band, The--A Song.mp3"


def test_metadata_without_the_does_not_remove_the_filename_article(library):
    song = named_song(library, "The Example Band--A Song.mp3")
    library.edit_metadata([song["id"]], {"artist": "Example Band", "title": "A Song"})
    item = proposals(library, [song["id"]], use_metadata=True)[0]
    assert item["proposed"] == "Example Band, The--A Song.mp3"
    assert "leading The" in item["reason"]


def test_metadata_artist_and_title_rules_do_not_change_other_parts(library):
    song = named_song(library, "Old Artist -- Old Title.MP3")
    library.edit_metadata([song["id"]], {"artist": "New Artist", "title": "New Title"})
    artist = proposals(library, [song["id"]], reason="metadata_artist")[0]
    title = proposals(library, [song["id"]], reason="metadata_title")[0]
    assert artist["proposed"] == "New Artist -- Old Title.MP3"
    assert title["proposed"] == "Old Artist -- New Title.MP3"
    assert proposals(library, [song["id"]], use_metadata=False, reason="metadata_artist") == []


def test_separator_rule_leaves_spacing_for_a_later_pass(library):
    song = named_song(library, "Artist - A Song.MP3")
    item = proposals(library, [song["id"]], use_metadata=False, reason="separator")[0]
    assert item["proposed"] == "Artist -- A Song.MP3"
    rename_files(library, [{"id": song["id"], "name": item["proposed"], "sha256": item["sha256"]}])
    item = proposals(library, [song["id"]], use_metadata=False, reason="spacing")[0]
    assert item["proposed"] == "Artist--A Song.MP3"


def test_extension_rule_does_not_need_artist_title_details(library):
    song = named_song(library, "Unknown.MP3")
    item = proposals(library, [song["id"]], use_metadata=False, reason="extension")[0]
    assert item["proposed"] == "Unknown.mp3"


def test_mark_correct_survives_reopen_and_tag_edits_but_expires_when_name_changes(library):
    song = named_song(library, "The Example Band -- A Song.MP3")
    mark_names_correct(library, [song])
    assert proposals(library, [song["id"]]) == []
    with Library(library.root) as reopened:
        assert proposals(reopened, [song["id"]]) == []
        reopened.edit_metadata([song["id"]], {"genre": "Example"})
        assert proposals(reopened, [song["id"]]) == []
        library.path(song).rename(library.root / "The Example Band -- Another Song.MP3")
        reopened.scan()
        assert proposals(reopened, [song["id"]])


def test_dialog_options_rebuild_scope_and_clear_old_approvals(qapp, library):
    song = named_song(library, "Artist--A Song.MP3")
    library.edit_metadata([song["id"]], {"title": "New Title"})
    songs = [library.song(song["id"])]
    dialog = RenameDialog(build_proposals(songs, use_metadata=False), songs=songs, use_metadata=False)
    assert dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
    dialog.approve_valid()
    assert dialog.approved()
    dialog.reason_combo.setCurrentIndex(dialog.reason_combo.findData("extension"))
    assert dialog.approved() == []
    assert dialog.table.item(0, 2).text() == "Artist--A Song.mp3"
    dialog.use_metadata.setChecked(True)
    dialog.reason_combo.setCurrentIndex(dialog.reason_combo.findData("metadata_title"))
    assert dialog.table.item(0, 2).text() == "Artist--New Title.MP3"
    dialog.use_metadata.setChecked(False)
    assert dialog.reason_combo.currentData() is None
    assert dialog.table.item(0, 2).text() == "Artist--A Song.mp3"
    dialog.close()


def test_mark_correct_in_dialog_hides_persists_and_can_be_undone(qapp, library):
    song = named_song(library, "The Example Band -- A Song.MP3")
    songs = [library.song(song["id"])]
    dialog = RenameDialog(
        build_proposals(songs, use_metadata=False),
        songs=songs,
        use_metadata=False,
        on_mark_correct=lambda selected, correct: mark_names_correct(library, selected, correct),
    )
    dialog.table.selectionModel().select(
        dialog.table.model().index(0, 1),
        QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
    )
    dialog.mark_correct_button.click()
    assert dialog.table.rowCount() == 0
    assert proposals(library, [song["id"]]) == []
    dialog.show_correct.setChecked(True)
    assert dialog.table.rowCount() == 1
    dialog.approve_valid()
    assert dialog.approved() == []
    dialog.table.setCurrentCell(0, 1)
    assert dialog.mark_correct_button.text() == "Review selected again"
    dialog.mark_correct_button.click()
    assert song["id"] not in rename_marks(library)
    assert proposals(library, [song["id"]])
    dialog.close()


def test_individual_artist_order_review_keeps_separator_and_extension(qapp, library):
    song = named_song(library, "Performer, Alex -- A Song.MP3")
    item = proposals(library, [song["id"]], use_metadata=False, reason="artist_order")[0]
    dialog = RenameOneDialog(item)
    assert not dialog.title.isEnabled()
    dialog.artist.setText("Alex Performer")
    dialog.suggest()
    assert dialog.filename.text() == "Alex Performer -- A Song.MP3"
    dialog.close()


def test_enabling_metadata_can_find_changes_in_previously_omitted_files(qapp, library):
    song = named_song(library, "Artist--A Song.mp3")
    library.edit_metadata([song["id"]], {"title": "Another Title"})
    songs = [library.song(song["id"])]
    assert build_proposals(songs, use_metadata=False) == []
    dialog = RenameDialog([], songs=songs, use_metadata=False)
    dialog.use_metadata.setChecked(True)
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 2).text() == "Artist--Another Title.mp3"
    dialog.close()


def test_unparsed_filename_can_be_marked_correct_without_entering_metadata(qapp, library):
    song = named_song(library, "Unknown.MP3")
    songs = [library.song(song["id"])]
    dialog = RenameDialog(
        build_proposals(songs, use_metadata=False),
        songs=songs,
        use_metadata=False,
        on_mark_correct=lambda selected, correct: mark_names_correct(library, selected, correct),
    )
    dialog.show_missing.setChecked(True)
    dialog.table.setCurrentCell(0, 1)
    dialog.mark_correct_button.click()
    assert dialog.table.rowCount() == 0
    dialog.show_correct.setChecked(True)
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 2).text() == "Unknown.MP3"
    dialog.approve_valid()
    assert dialog.approved() == []
    dialog.close()


def test_marking_one_correct_keeps_other_rows_approved(qapp, library):
    songs = library.songs()
    for index, song in enumerate(songs):
        library.path(song).rename(library.root / f"The Example Band--Song {index}.MP3")
    library.scan()
    source = library.songs()
    dialog = RenameDialog(build_proposals(source, use_metadata=False), songs=source, use_metadata=False)
    dialog.approve_valid()
    before = {item["id"] for item in dialog.approved()}
    chosen = dialog.proposal_at(0)["id"]
    dialog.table.selectionModel().select(
        dialog.table.model().index(0, 1),
        QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
    )
    dialog.mark_correct_button.click()
    assert {item["id"] for item in dialog.approved()} == before - {chosen}
    dialog.close()
