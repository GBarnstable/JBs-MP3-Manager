import json
import os
import shutil
from pathlib import Path

import pytest
from mutagen.id3 import APIC, ID3, TXXX

from jbs_mp3_manager.library import DATA_DIR, Library, mp3_files
from jbs_mp3_manager.media import inspect_mp3, write_tags


def ids(lib):
    return [song["id"] for song in lib.songs()]


def test_categories_are_many_to_many_and_untagged_is_automatic(library):
    songs = ids(library)
    jazz = library.add_category("Jazz")
    evening = library.add_category("Evening")
    library.assign(songs[:1], [jazz, evening])
    assert len(library.songs("untagged")) == 2
    assert ids_for(library, jazz) == [songs[0]]
    library.assign(songs[:1], [jazz], remove=True)
    assert len(library.songs("untagged")) == 2
    library.delete_category(evening)
    assert len(library.songs("untagged")) == 3
    assert len(mp3_files(library.root, False)) == 3


def ids_for(lib, category):
    return [song["id"] for song in lib.songs(category)]


def test_detection_missing_and_restored_files_keep_categories(library, tones):
    first = library.songs()[0]
    category = library.add_category("Favorites")
    library.assign([first["id"]], [category])
    path = library.path(first)
    contents = path.read_bytes()
    path.unlink()
    report = library.scan()
    assert report["missing"] == 1
    assert len(library.songs("missing")) == 1
    path.write_bytes(contents)
    library.scan()
    assert ids_for(library, category) == [first["id"]]
    shutil.copy2(tones[0], library.root / "New.mp3")
    assert library.scan()["added"] == 1
    assert "New.mp3" in [song["path"] for song in library.songs("untagged")]


def test_tag_edits_preserve_audio_categories_and_extra_frames(library):
    song = library.songs()[0]
    category = library.add_category("Keep")
    library.assign([song["id"]], [category])
    path = library.path(song)
    tags = ID3(path)
    tags.add(TXXX(encoding=3, desc="custom", text=["preserve me"]))
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="cover", data=b"cover-test"))
    tags.save(path)
    audio_hash = inspect_mp3(path)["audio_sha256"]
    report = library.edit_metadata([song["id"]], {"title": "Edited", "artist": "New artist", "year": "1999"})
    assert report == {"updated": 1, "errors": []}
    assert inspect_mp3(path)["audio_sha256"] == audio_hash
    assert library.song(song["id"])["title"] == "Edited"
    assert library.song(song["id"])["year"] == "1999"
    assert ids_for(library, category) == [song["id"]]
    restored = ID3(path)
    assert str(restored["TXXX:custom"]) == "preserve me"
    assert restored.getall("APIC")[0].data == b"cover-test"
    assert list((library.data / "tag-backups").glob("*.id3"))


def test_import_matches_exact_and_changed_id3_and_preserves_sources(library, tmp_path):
    source = tmp_path / "Dance"
    source.mkdir()
    songs = library.songs()
    shutil.copy2(library.path(songs[0]), source / "Different filename.mp3")
    tagged = source / "Retagged.mp3"
    shutil.copy2(library.path(songs[1]), tagged)
    write_tags(tagged, {"title": "A new title in category copy"}, tmp_path / "tag-backups")
    preview = library.preview_import([source])
    assert {e["status"] for e in preview["entries"]} == {"exact", "audio"}
    assert not library.categories()
    report = library.apply_import(preview)
    assert report["safe_to_remove_sources"]
    assert report["matched_files"] == 2
    category = library.categories()[0]
    assert len(library.songs(category["id"])) == 2
    assert len(library.songs("untagged")) == 1
    assert len(list(source.glob("*.mp3"))) == 2
    assert Path(report["report_path"]).is_file()
    assert Path(report["report_path"]).with_suffix(".csv").is_file()


def test_import_reports_ambiguous_unmatched_and_invalid_files(library, tmp_path, tones):
    source = tmp_path / "Review"
    source.mkdir()
    first = library.songs()[0]
    shutil.copy2(library.path(first), library.root / "Duplicate.mp3")
    library.scan()
    shutil.copy2(library.path(first), source / "Ambiguous.mp3")
    (source / "Broken.mp3").write_bytes(b"not mp3")
    # A distinct, valid audio stream not present among the master songs.
    missing = source / "Missing.mp3"
    missing.write_bytes(tones[0].read_bytes() + tones[1].read_bytes())
    preview = library.preview_import([source])
    assert {e["status"] for e in preview["entries"]} == {"ambiguous", "error", "unmatched"}
    report = library.apply_import(preview)
    assert report["unresolved_files"] == 3
    assert not report["safe_to_remove_sources"]
    assert len(library.songs("untagged")) == 4


def test_import_revalidates_files_before_committing(library, tmp_path, tones):
    category = tmp_path / "Example"
    category.mkdir()
    source = category / "copy.mp3"
    shutil.copy2(library.path(library.songs()[0]), source)
    preview = library.preview_import([category])
    shutil.copyfile(tones[2], source)
    with pytest.raises(ValueError, match="changed since preview"):
        library.apply_import(preview)
    assert not library.categories()


def test_library_relocates_with_relative_references(library, tmp_path):
    first = ids(library)[0]
    category = library.add_category("Portable")
    library.assign([first], [category])
    destination = tmp_path / "C drive" / "Music"
    shutil.copytree(library.root, destination)
    with Library(destination) as moved:
        assert ids_for(moved, category) == [first]
        assert moved.path(moved.song(first)).is_relative_to(destination)
        assert moved.scan()["added"] == 0
        assert moved.scan()["missing"] == 0


def test_unique_renames_preserve_song_id_and_categories(library):
    song = library.songs()[0]
    category = library.add_category("Renamed")
    library.assign([song["id"]], [category])
    library.path(song).rename(library.root / "Renamed.mp3")
    assert library.scan()["renamed"] == 1
    assert ids_for(library, category) == [song["id"]]
    assert library.song(song["id"])["path"] == "Renamed.mp3"


def test_audio_replacement_does_not_inherit_categories(library, tones):
    song = next(s for s in library.songs() if s["path"] == "Alpha.mp3")
    category = library.add_category("Original")
    library.assign([song["id"]], [category])
    shutil.copyfile(tones[2], library.path(song))
    library.scan(verify=True)
    assert not library.songs(category)


def test_export_playlist_uses_utf8_relative_paths_in_sorted_order(library, tmp_path):
    songs = library.songs()
    destination = tmp_path / "playlist" / "my music.m3u8"
    library.export_playlist([s["id"] for s in reversed(songs)], destination)
    text = destination.read_text("utf-8")
    assert text.startswith("#EXTM3U\n")
    assert "Café #3.mp3" in text
    entries = [line for line in text.splitlines() if not line.startswith("#")]
    assert [(destination.parent / line).resolve() for line in entries] == [
        library.path(s) for s in reversed(songs)
    ]


def test_export_copies_and_does_not_overwrite(library, tmp_path):
    destination = tmp_path / "USB"
    destination.mkdir()
    song = library.songs()[0]
    original = destination / Path(song["path"]).name
    original.write_bytes(b"existing content")
    report = library.export_files([song["id"]], destination)
    assert report["errors"] == []
    assert len(report["copied"]) == 1
    assert original.read_bytes() == b"existing content"
    copy = destination / report["copied"][0]["destination"]
    assert copy.read_bytes() == library.path(song).read_bytes()
    playlist = next(destination.glob("*.m3u8")).read_text("utf-8")
    assert copy.name in playlist
    with pytest.raises(ValueError, match="outside"):
        library.export_files([song["id"]], library.root / "Export")


def test_master_subfolder_category_copies_are_excluded_by_default(library):
    category = library.root / "Jazz"
    category.mkdir()
    shutil.copy2(library.path(library.songs()[0]), category / "copy.mp3")
    assert library.scan()["added"] == 0
    assert library.preview_import([category])["entries"][0]["status"] == "exact"
    assert len(mp3_files(library.root, False)) == 3


def test_recursive_master_folder_and_new_nested_files(tmp_path, tones):
    root = tmp_path / "Music"
    album = root / "Artist" / "Album"
    album.mkdir(parents=True)
    shutil.copy2(tones[0], album / "song.mp3")
    with Library(root, create=True, recursive=True) as lib:
        assert lib.scan()["added"] == 1
        assert lib.songs()[0]["path"] == "Artist/Album/song.mp3"


def test_unreadable_scan_does_not_mark_everything_missing(library, monkeypatch):
    def unreadable(*args, **kwargs):
        raise PermissionError("Drive unavailable")

    monkeypatch.setattr(os, "walk", unreadable)
    with pytest.raises(PermissionError):
        library.scan()
    assert len(library.songs()) == 3


def test_literal_search_and_reserved_category_names(library):
    assert not library.songs(search="%")
    assert not library.songs(search="' OR 1=1 --")
    assert len(library.songs(search="Café")) == 1
    for name in ("", "Untagged", "all songs", "Missing files"):
        with pytest.raises(ValueError):
            library.add_category(name)
    assert library.add_category("DANCE") == library.add_category("dance")


def test_backup_is_a_usable_database(library, tmp_path):
    category = library.add_category("Backup")
    library.assign(ids(library), [category])
    backup = library.backup(tmp_path / "backup.sqlite3")
    root = tmp_path / "Restored"
    shutil.copytree(library.root, root)
    shutil.copy2(backup, root / DATA_DIR / "library.sqlite3")
    with Library(root) as restored:
        assert len(restored.songs(category)) == 3


def test_invalid_metadata_value_leaves_original_file_unchanged(library):
    song = library.songs()[0]
    path = library.path(song)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        write_tags(path, {"not-a-field": "value"}, library.data / "tag-backups")
    assert path.read_bytes() == before


def test_no_absolute_song_paths_in_database(library):
    for row in library.db.execute("SELECT path FROM songs"):
        assert not Path(row[0]).is_absolute()
    assert not list(library.root.glob("*.json"))
    assert (
        json.loads(library.db.execute("SELECT value FROM settings WHERE key='recursive'").fetchone()[0])
        is False
    )
