import array
import json
import math
import os
import random
import shutil
import sqlite3
import subprocess
import wave
from pathlib import Path

import pytest
from mutagen.id3 import APIC, ID3, POPM, TXXX

from jbs_mp3_manager.arrival_dialogs import ComparisonDialog, RenameDialog, RenameOneDialog
from jbs_mp3_manager.duplicates import archive_inventory, merge, purge_archives, review
from jbs_mp3_manager.fileops import _batch, _save, delete_incoming, recover_actions, replace_recording
from jbs_mp3_manager.fingerprinting import (
    find_candidates,
    fingerprint,
    fingerprint_similarity,
    quality_comparison,
    tool_path,
)
from jbs_mp3_manager.library import Library
from jbs_mp3_manager.media import inspect_mp3
from jbs_mp3_manager.naming import filename_artist, proposals, rename_files, suggested_name


def test_naming_convention_and_exceptions():
    assert (
        suggested_name("The Rolling Stones", "Paint It, Black") == "Rolling Stones, The--Paint It, Black.mp3"
    )
    assert suggested_name("The Who", "Baba O'Riley") == "Who, The--Baba O'Riley.mp3"
    assert filename_artist("The The") == "The The"
    assert filename_artist("Thelonious Monk") == "Thelonious Monk"
    assert filename_artist("Beatles, The") == "Beatles, The"
    assert "The Sun" in suggested_name("The Beatles", "Here Comes The Sun")


def test_rename_preserves_id_audio_metadata_categories_and_scan_identity(library):
    song = library.songs()[0]
    library.edit_metadata([song["id"]], {"artist": "The Who", "title": "Baba O'Riley"})
    category = library.add_category("Favorites")
    library.assign([song["id"]], [category])
    song = library.song(song["id"])
    before = library.path(song).read_bytes()
    proposal = proposals(library, [song["id"]])[0]
    result = rename_files(
        library, [{"id": song["id"], "name": proposal["proposed"], "sha256": song["sha256"]}]
    )
    after = library.song(song["id"])
    assert result["renamed"] == 1
    assert after["path"] == "Who, The--Baba O'Riley.mp3"
    assert after["artist"] == "The Who"
    assert library.path(after).read_bytes() == before
    assert library.songs(category)[0]["id"] == song["id"]
    assert library.scan()["added"] == 0
    assert library.songs("missing") == []


def test_filename_fallback_needs_review_and_missing_values_are_not_invented(library):
    song = library.songs()[0]
    path = library.path(song)
    path.rename(library.root / "The Who--My Generation.mp3")
    library.scan()
    item = next(p for p in proposals(library) if p["id"] == song["id"])
    assert item["proposed"] == "Who, The--My Generation.mp3"
    assert "fallback" in item["basis"]
    assert any(not item["proposed"] for item in proposals(library))


def test_rename_collision_is_rejected_without_overwriting(library):
    first, second = library.songs()[:2]
    original = library.path(second).read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        rename_files(library, [{"id": first["id"], "name": second["path"], "sha256": first["sha256"]}])
    assert library.path(second).read_bytes() == original


def test_rename_rollback_restores_file_on_sql_failure(library):
    song = library.songs()[0]
    original = library.path(song).read_bytes()
    library.db.executescript(
        "CREATE TRIGGER reject_rename BEFORE UPDATE OF path ON songs BEGIN "
        "SELECT RAISE(ABORT,'test failure'); END;"
    )
    with pytest.raises(sqlite3.IntegrityError):
        rename_files(library, [{"id": song["id"], "name": "Renamed.mp3", "sha256": song["sha256"]}])
    assert library.path(song).read_bytes() == original
    assert not (library.root / "Renamed.mp3").exists()


def test_rename_case_only_works_and_does_not_drop_id(library):
    song = next(s for s in library.songs() if s["path"] == "Alpha.mp3")
    rename_files(library, [{"id": song["id"], "name": "alpha.mp3", "sha256": song["sha256"]}])
    assert library.song(song["id"])["path"] == "alpha.mp3"
    assert any(p.name == "alpha.mp3" for p in library.root.iterdir())


def test_interrupted_rename_recovers_files_before_scan(library):
    song = library.songs()[0]
    path = library.path(song)
    original = path.read_bytes()
    batch, manifest = _batch(library, "rename", [(song, song["sha256"])])
    manifest["moves"][0]["target"] = "After.mp3"
    _save(batch, manifest)
    path.rename(batch / manifest["moves"][0]["archive"])
    (batch / manifest["moves"][0]["archive"]).rename(library.root / "After.mp3")
    library.scan()
    assert path.read_bytes() == original
    assert not (library.root / "After.mp3").exists()
    assert library.song(song["id"])["path"] == song["path"]
    assert json.loads((batch / "manifest.json").read_text())["state"] == "rolled_back"


def test_recovery_does_not_undo_committed_rename_with_stale_journal(library):
    song = library.songs()[0]
    result = rename_files(library, [{"id": song["id"], "name": "After.mp3", "sha256": song["sha256"]}])
    batch = Path(result["backup_folder"])
    manifest = json.loads((batch / "manifest.json").read_text())
    manifest["state"] = "prepared"  # Simulate power loss after COMMIT but before the final journal write.
    _save(batch, manifest)
    recover_actions(library)
    assert library.song(song["id"])["path"] == "After.mp3"
    assert (library.root / "After.mp3").exists()
    assert not (library.root / song["path"]).exists()


def test_interrupted_replacement_restores_both_originals(library):
    old, incoming = library.songs()[:2]
    originals = {s["path"]: library.path(s).read_bytes() for s in (old, incoming)}
    batch, manifest = _batch(library, "replace", [(s, s["sha256"]) for s in (old, incoming)])
    manifest["installed"] = [{"path": old["path"], "sha256": incoming["sha256"]}]
    _save(batch, manifest)
    for move in manifest["moves"]:
        (library.root / move["original"]).rename(batch / move["archive"])
    shutil.copy2(batch / manifest["moves"][1]["archive"], library.root / old["path"])
    library.scan()
    for relative, expected in originals.items():
        assert (library.root / relative).read_bytes() == expected
    assert len(library.songs()) == 3


def test_discard_new_rechecks_keeper_before_committing(library):
    keeper, incoming = library.songs()[:2]
    original = library.path(incoming).read_bytes()

    def external_change(message):
        with library.path(keeper).open("ab") as stream:
            stream.write(b"changed externally")

    with pytest.raises(ValueError, match="kept recording changed"):
        delete_incoming(
            library, incoming["id"], keeper["id"], incoming["sha256"], keeper["sha256"], external_change
        )
    assert library.path(incoming).read_bytes() == original
    assert library.song(incoming["id"])


def test_new_song_inbox_and_encoding_properties(library, tones):
    assert library.db.execute("SELECT COUNT(*) FROM new_arrivals").fetchone()[0] == 0
    shutil.copy2(tones[0], library.root / "new.mp3")
    report = library.scan()
    assert len(report["new_song_ids"]) == 1
    song = library.song(report["new_song_ids"][0])
    assert song["bitrate"] > 0 and song["sample_rate"] > 0
    assert library.db.execute("SELECT song_id FROM new_arrivals").fetchone()[0] == song["id"]


def test_properties_backfill_does_not_hash_all_audio_again(library, monkeypatch):
    with library.db:
        library.db.execute("UPDATE songs SET bitrate=0")
    monkeypatch.setattr(
        "jbs_mp3_manager.library.inspect_mp3", lambda path: pytest.fail("Full hash read was unnecessary")
    )
    library.scan()
    assert all(song["bitrate"] > 0 for song in library.songs())


def test_replacement_transfers_all_id3_and_categories_and_deletes_old_versions(library):
    old, incoming = library.songs()[:2]
    old_path, incoming_path = library.path(old), library.path(incoming)
    tags = ID3(old_path)
    tags.add(TXXX(encoding=3, desc="user-label", text=["keep this"]))
    tags.add(POPM(email="owner@example.invalid", rating=220, count=7))
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="cover", data=b"test-cover"))
    tags.save(old_path)
    library.scan()
    old, incoming = library.song(old["id"]), library.song(incoming["id"])
    first = library.add_category("Existing tags")
    second = library.add_category("Incoming tags")
    library.assign([old["id"]], [first])
    library.assign([incoming["id"]], [second])
    audio = incoming["audio_sha256"]
    result = replace_recording(library, old["id"], incoming["id"], old["sha256"], incoming["sha256"])
    kept = library.song(old["id"])
    assert kept["path"] == old["path"]
    assert inspect_mp3(old_path)["audio_sha256"] == audio
    assert not incoming_path.exists()
    assert len(library.songs()) == 2 and not library.songs("missing")
    assert result["deleted"] == 2  # old audio and the original incoming copy, after installing merged tags.
    assert not list((Path(result["backup_folder"]) / "files").glob("*.mp3"))
    transferred = ID3(old_path)
    assert str(transferred["TXXX:user-label"]) == "keep this"
    assert transferred.getall("POPM")[0].rating == 220
    assert transferred.getall("APIC")[0].data == b"test-cover"
    assert library.songs(first)[0]["id"] == old["id"]
    assert library.songs(second)[0]["id"] == old["id"]


def test_replacement_rollback_retains_both_original_files(library):
    old, incoming = library.songs()[:2]
    originals = {s["path"]: library.path(s).read_bytes() for s in (old, incoming)}
    library.db.executescript(
        "CREATE TRIGGER reject_remove BEFORE DELETE ON songs BEGIN SELECT RAISE(ABORT,'test failure'); END;"
    )
    with pytest.raises(sqlite3.IntegrityError):
        replace_recording(library, old["id"], incoming["id"], old["sha256"], incoming["sha256"])
    for song in (old, incoming):
        assert library.path(song).read_bytes() == originals[song["path"]]
    assert len(library.songs()) == 3


def test_exact_merge_delete_and_previous_archive_cleanup(library):
    song = library.songs()[0]
    shutil.copy2(library.path(song), library.root / "Copy.mp3")
    library.scan()
    snapshot = review(library)
    result = merge(library, snapshot, {snapshot["groups"][0]["key"]: song["id"]}, delete_extras=True)
    assert result["files_deleted"] == 1
    assert not archive_inventory(library)
    assert len(library.songs()) == 3
    shutil.copy2(library.path(library.song(song["id"])), library.root / "Another copy.mp3")
    library.scan()
    snapshot = review(library)
    merge(library, snapshot, {snapshot["groups"][0]["key"]: song["id"]})
    inventory = archive_inventory(library)
    assert len(inventory) == 1
    cleanup = purge_archives(library, [batch["id"] for batch in inventory])
    assert cleanup["deleted"] == 1 and not cleanup["errors"]


def test_quality_indicators_do_not_claim_transcode_proves_better_quality():
    current = {"audio_sha256": "one", "bitrate": 128000, "sample_rate": 44100, "channels": 2}
    incoming = {**current, "audio_sha256": "two", "bitrate": 320000}
    result = quality_comparison(current, incoming)
    assert result["possible_upgrade"]
    assert "cannot prove" in result["text"]
    assert not quality_comparison(current, {**incoming, "audio_sha256": "one"})["possible_upgrade"]
    assert fingerprint_similarity([0] * 100, [0] * 100) == 0


@pytest.fixture(scope="session")
def reencoded_music(tmp_path_factory):
    root = tmp_path_factory.mktemp("fingerprint-audio")
    binary = shutil.which("ffmpeg")
    if not binary:
        import imageio_ffmpeg

        binary = imageio_ffmpeg.get_ffmpeg_exe()
    wav = root / "music.wav"
    randomizer = random.Random(235)
    notes = [randomizer.choice([220, 246.94, 261.63, 293.66, 329.63, 349.23, 392, 440]) for _ in range(120)]
    samples = array.array("h")
    for index in range(22050 * 24):
        seconds = index / 22050
        frequency = notes[min(len(notes) - 1, int(seconds * 5))]
        envelope = min(1, (seconds % 0.2) * 30)
        value = envelope * (
            0.3 * math.sin(2 * math.pi * frequency * seconds)
            + 0.12 * math.sin(2 * math.pi * frequency * 2 * seconds)
            + 0.10 * math.sin(2 * math.pi * 110 * seconds)
        )
        samples.append(int(value * 32000))
    if os.sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(wav), "wb") as stream:
        stream.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
        stream.writeframes(samples.tobytes())
    result = []
    for bitrate in (64, 192):
        path = root / f"music-{bitrate}.mp3"
        subprocess.run(
            [
                binary,
                "-loglevel",
                "error",
                "-i",
                str(wav),
                "-c:a",
                "libmp3lame",
                "-b:a",
                f"{bitrate}k",
                str(path),
            ],
            check=True,
        )
        result.append(path)
    return result


def test_audio_fingerprint_finds_reencoding_with_unrelated_filenames(tmp_path, reencoded_music):
    if not tool_path():
        pytest.fail("Fetch the fingerprint tool before running the full tests")
    root = tmp_path / "Music"
    root.mkdir()
    shutil.copy2(reencoded_music[0], root / "Unrelated old filename.mp3")
    with Library(root, create=True) as library:
        library.scan()
        old = library.songs()[0]
        shutil.copy2(reencoded_music[1], root / "Something new.mp3")
        report = library.scan()
        incoming = library.song(report["new_song_ids"][0])
        assert old["audio_sha256"] != incoming["audio_sha256"]
        score = fingerprint_similarity(fingerprint(library, old), fingerprint(library, incoming))
        assert score >= 0.90
        result = find_candidates(library, [incoming["id"]])
        assert len(result["pairs"]) == 1
        assert result["pairs"][0]["verified_audio"]
        assert result["pairs"][0]["possible_upgrade"]


def test_naming_and_comparison_dialogs_require_approval(qapp, library):
    values = proposals(library)
    dialog = RenameDialog(values)
    assert dialog.approved() == []
    one = RenameOneDialog({**values[0], "rename_artist": "The Who", "rename_title": "My Generation"})
    one.suggest()
    assert one.filename.text() == "Who, The--My Generation.mp3"
    one.close()
    dialog.close()
    pair = {
        "incoming": library.songs()[1],
        "current": library.songs()[0],
        "match": "Metadata",
        "text": "Possible upgrade",
        "possible_upgrade": True,
        "verified_audio": False,
    }
    comparison = ComparisonDialog({"pairs": [pair], "note": "Check versions"})
    assert comparison.decisions() == []
    assert not comparison.verified.isChecked()
    comparison.close()
