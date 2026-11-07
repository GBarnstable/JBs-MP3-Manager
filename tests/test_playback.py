import time
from types import SimpleNamespace

import pytest

from jbs_mp3_manager.playback import EQ_UNITY_PREAMP, Playback, instance_options


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
        return SimpleNamespace(release=lambda: None, add_option=lambda option: None)

    def release(self):
        pass


class FakeEqualizer:
    def __init__(self):
        self.bands = [0] * 10
        self.released = False

    def set_preamp(self, value):
        self.preamp = value

    def set_amp_at_index(self, value, index):
        self.bands[index] = value

    def release(self):
        self.released = True


@pytest.fixture
def playback(qapp):
    vlc = SimpleNamespace(
        State=SimpleNamespace(
            Error="Error",
            Opening="Opening",
            Buffering="Buffering",
            NothingSpecial="Nothing",
            Ended="Ended",
            Paused="Paused",
            Playing="Playing",
        ),
        AudioEqualizer=FakeEqualizer,
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


def test_prepares_next_song_early_but_does_not_fade_current_song(playback):
    playback.decks[0].position = 5500
    playback.tick()
    assert playback.prepared["ready"]
    assert playback.decks[1].state == "Paused"
    assert playback.decks[1].position == 0
    assert playback.fade is None
    assert playback.active == 0
    assert playback.decks[0].volume == 80


def test_slow_incoming_decoder_does_not_fade_out_current_song(playback):
    incoming = playback.decks[1]

    def delayed_play():
        incoming.state = "Opening"
        return 0

    incoming.play = delayed_play
    playback.decks[0].position = 8000
    playback.tick()
    assert playback.fade is None
    assert playback.active == 0
    assert playback.decks[0].volume == 80
    incoming.state = "Paused"
    incoming.set_pause = lambda value: setattr(incoming, "state", "Paused" if value else "Buffering")
    playback.tick()
    assert playback.fade[2] is None
    assert playback.decks[0].volume == 80
    incoming.state = "Playing"
    playback.tick()
    assert playback.fade[2] is not None
    assert playback.active == 1
    playback.fade[2] -= playback.fade[3] / 2
    playback.tick()
    assert all(0 < deck.volume < 80 for deck in playback.decks)
    # Undoing VLC's volume taper keeps the sum of the decks' signal powers near unity.
    assert sum((deck.volume / 80) ** 6 for deck in playback.decks) == pytest.approx(1, abs=0.04)


def test_equalizer_compensates_vlc_attenuation_and_flat_disables_filter(playback):
    gains = [0] * 10
    gains[-1] = -1
    playback.set_equalizer(gains)
    equalizer = playback.equalizer
    assert equalizer.preamp == pytest.approx(EQ_UNITY_PREAMP)
    assert equalizer.preamp == pytest.approx(12.0412, abs=0.001)
    assert playback.volume == 80
    playback.set_equalizer([0] * 10)
    assert playback.equalizer is None
    assert equalizer.released
    assert all(deck.eq is None for deck in playback.decks)
    playback.next()
    assert playback.decks[playback.active].eq is None
    assert playback.decks[playback.active].volume == 80


def test_windows_uses_independent_output_buffer_volumes():
    assert "--aout=directsound" in instance_options("win32")
    assert "--no-volume-save" in instance_options("win32")


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
