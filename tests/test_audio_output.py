"""Verify actual VLC decoding overlap and PCM amplitude, not just mocked state changes."""

import array
import ctypes
import math
import os
import shutil
import subprocess
import time

import pytest

from jbs_mp3_manager.playback import Playback, load_vlc


@pytest.fixture
def real_vlc():
    try:
        vlc = load_vlc()
        instance = vlc.Instance(
            "--no-video", "--quiet", "--aout=dummy", "--no-volume-save", "--audio-filter=scaletempo"
        )
        if instance is None:
            raise RuntimeError("VLC is unavailable")
    except Exception as error:
        if os.environ.get("JBS_REQUIRE_VLC") == "1":
            pytest.fail(str(error))
        pytest.skip(f"VLC runtime is not installed: {error}")
    return vlc, instance


def test_real_equalizer_preserves_level_and_reset_restores_pcm(qapp, tones, real_vlc):
    vlc, instance = real_vlc
    player = Playback(vlc_module=vlc, instance=instance)
    player.crossfade = 0
    samples = array.array("h")
    errors = []
    player.error.connect(errors.append)

    @vlc.CallbackDecorators.AudioPlayCb
    def capture(opaque, data, count, pts):
        samples.frombytes(ctypes.string_at(data, count * 2 * 2))  # stereo signed 16-bit PCM

    deck = player.decks[0]
    deck.audio_set_callbacks(capture, None, None, None, None, None)
    deck.audio_set_format("S16N", 44100, 2)

    def rms(gains):
        player.stop()
        del samples[:]
        player.set_equalizer(gains)
        player.set_queue([{"path": "test.mp3", "absolute_path": str(tones[0])}])
        deadline = time.monotonic() + 8
        while not player._stopped and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert player._stopped
        assert len(samples) > 10000
        values = samples[2000:-2000]
        return math.sqrt(sum(value * value for value in values) / len(values))

    try:
        original = rms([0] * 10)
        # A tiny cut far above a 440 Hz test tone must not reduce the whole signal by 12 dB.
        adjusted = rms([0] * 9 + [-1])
        reset = rms([0] * 10)
        assert not errors
        assert adjusted / original == pytest.approx(1, abs=0.06)
        assert reset / original == pytest.approx(1, abs=0.01)
    finally:
        player.close()


def test_real_crossfade_runs_decoders_concurrently(qapp, tmp_path, real_vlc):
    binary = shutil.which("ffmpeg")
    if not binary:
        import imageio_ffmpeg

        binary = imageio_ffmpeg.get_ffmpeg_exe()
    paths = []
    for frequency in (440, 880):
        path = tmp_path / f"overlap-{frequency}.mp3"
        subprocess.run(
            [
                binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:duration=5",
                "-c:a",
                "libmp3lame",
                str(path),
            ],
            check=True,
        )
        paths.append(path)
    vlc, instance = real_vlc
    player = Playback(vlc_module=vlc, instance=instance)
    player.crossfade = 1.5
    gains = [0, 0]
    apply_gain = player._gain

    def observe_gain(index, gain):
        gains[index] = gain
        apply_gain(index, gain)

    player._gain = observe_gain
    errors, overlaps, trace = [], [], []
    player.error.connect(errors.append)
    try:
        player.set_queue([{"path": path.name, "absolute_path": str(path)} for path in paths])
        deadline = time.monotonic() + 10
        while not player._stopped and time.monotonic() < deadline:
            qapp.processEvents()
            trace.append(
                {
                    "states": [str(d.get_state()) for d in player.decks],
                    "positions": [d.get_time() for d in player.decks],
                    "volumes": [d.audio_get_volume() for d in player.decks],
                    "fade": player.fade,
                    "prepared": player.prepared,
                    "index": player.index,
                }
            )
            if (
                player.fade
                and player.fade[2] is not None
                and all(deck.get_state() == vlc.State.Playing for deck in player.decks)
                and all(gain > 0 for gain in gains)
            ):
                overlaps.append(time.monotonic())
                if overlaps[-1] - overlaps[0] > 0.35:
                    break
            time.sleep(0.01)
        assert not errors
        assert overlaps and overlaps[-1] - overlaps[0] > 0.3, trace[::10]
        assert player.index == 1
    finally:
        player.close()
