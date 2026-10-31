import time
from types import SimpleNamespace

import pytest

from jbs_mp3_manager.playback import Playback


class FakeDeck:
    def __init__(self):
        self.state = "Stopped"
        self.length = 10000
        self.position = 0
        self.volume = 0
        self.rate = 1
        self.stops = 0

    def play(self):
        self.state = "Playing"
        return 0

    def stop(self):
        self.stops += 1
        self.state = "Stopped"

    def audio_set_volume(self, value):
        self.volume = value

    def set_media(self, media):
        self.media = media
        self.position = 0

    def set_rate(self, value):
        self.rate = value

    def set_pause(self, value):
        self.state = "Paused" if value else "Playing"

    def set_time(self, value):
        self.position = value

    def get_time(self):
        return self.position

    def get_length(self):
        return self.length

    def get_state(self):
        return self.state

    def set_equalizer(self, value):
        self.eq = value

    def release(self):
        pass


class FakeInstance:
    def media_player_new(self):
        return FakeDeck()

    def media_new_path(self, path):
        return SimpleNamespace(release=lambda: None)

    def release(self):
        pass


@pytest.fixture
def playback(qapp):
    vlc = SimpleNamespace(
        State=SimpleNamespace(
            Error="Error", Opening="Opening", Buffering="Buffering", NothingSpecial="Nothing", Ended="Ended"
        )
    )
    player = Playback(vlc_module=vlc, instance=FakeInstance())
    player.set_queue([{"id": str(n), "path": f"{n}.mp3", "absolute_path": f"/{n}.mp3"} for n in range(3)])
    yield player
    player.close()


def test_crossfade_starts_second_deck_and_finishes_old_deck(playback):
    old = playback.active
    playback.decks[old].position = 8000
    playback.tick()
    assert playback.active != old
    assert playback.index == 1
    assert playback.fade is not None
    playback.fade[2] -= 3
    playback.tick()
    assert playback.fade is None
    assert playback.decks[old].state == "Stopped"
    assert playback.decks[playback.active].volume == 80


def test_pause_freezes_both_crossfade_decks(playback):
    playback.decks[playback.active].position = 8000
    playback.tick()
    playback.toggle_pause()
    assert all(d.state == "Paused" for d in playback.decks)
    before = playback.fade[2]
    playback._paused_at -= 5
    playback.toggle_pause()
    assert playback.fade[2] >= before + 4.9
    assert all(d.state == "Playing" for d in playback.decks)


def test_stop_and_repeat_behaviors(playback):
    playback.crossfade = 0
    playback.index = 2
    playback.decks[playback.active].state = "Ended"
    playback.tick()
    assert playback._stopped
    playback._load(playback.active, playback.queue[2])
    playback.repeat = "All"
    playback.decks[playback.active].state = "Ended"
    playback.tick()
    assert playback.index == 0
    playback.repeat = "One"
    playback.decks[playback.active].state = "Ended"
    playback.tick()
    assert playback.index == 0
    playback.next()
    assert playback.index == 1


def test_seeking_during_crossfade_stops_old_deck(playback):
    playback.decks[playback.active].position = 8000
    playback.tick()
    playback.seek(500)
    assert playback.fade is None
    assert playback.decks[playback.active].position == 500


def test_shuffle_keeps_selected_song_first(playback):
    tracks = list(playback.queue)
    playback.shuffle = True
    playback.set_queue(tracks, 2)
    assert playback.queue[0]["id"] == "2"
    assert {s["id"] for s in playback.queue} == {"0", "1", "2"}


def test_real_vlc_decodes_an_mp3(qapp, tones):
    import os

    try:
        from jbs_mp3_manager.playback import load_vlc

        vlc = load_vlc()
        instance = vlc.Instance("--no-video", "--quiet", "--aout=dummy", "--audio-filter=scaletempo")
        if instance is None:
            raise RuntimeError("VLC instance is unavailable")
    except Exception as error:
        if os.environ.get("JBS_REQUIRE_VLC") == "1":
            pytest.fail(str(error))
        pytest.skip(f"VLC runtime is not installed: {error}")
    player = Playback(vlc_module=vlc, instance=instance)
    errors, positions, decoded = [], [], []
    player.error.connect(errors.append)
    player.position_changed.connect(lambda p, length: positions.append(p))
    try:
        player.crossfade = 0
        player.set_queue([{"path": "generated.mp3", "absolute_path": str(tones[0])}])
        media = player.decks[player.active].get_media()
        deadline = time.monotonic() + 8
        while not player._stopped and time.monotonic() < deadline:
            qapp.processEvents()
            statistics = vlc.MediaStats()
            if media.get_stats(statistics):
                decoded.append(statistics.decoded_audio)
            time.sleep(0.02)
        assert not errors
        assert max(decoded, default=0) > 0
        assert player._stopped
    finally:
        player.close()
