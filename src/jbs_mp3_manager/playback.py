"""Two-deck libVLC playback. No transcoding or modifications to library audio."""

from __future__ import annotations

import math
import os
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

_dll_handles = []


def load_vlc():
    if sys.platform == "win32":
        bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "vlc"
        candidates = [bundled, Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "VideoLAN/VLC"]
        for candidate in candidates:
            if (candidate / "libvlc.dll").is_file():
                os.environ["PYTHON_VLC_LIB_PATH"] = str(candidate / "libvlc.dll")
                os.environ["PYTHON_VLC_MODULE_PATH"] = str(candidate)
                os.environ["VLC_PLUGIN_PATH"] = str(candidate / "plugins")
                os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")
                _dll_handles.append(os.add_dll_directory(str(candidate)))
                break
    import vlc

    return vlc


class Playback(QObject):
    track_changed = Signal(object)
    position_changed = Signal(int, int)
    state_changed = Signal(str)
    error = Signal(str)

    def __init__(self, parent=None, *, vlc_module=None, instance=None):
        super().__init__(parent)
        self.vlc = vlc_module or load_vlc()
        self.instance = instance or self.vlc.Instance("--no-video", "--quiet", "--audio-filter=scaletempo")
        if self.instance is None:
            raise RuntimeError("Could not start the VLC audio engine.")
        self.decks = [self.instance.media_player_new(), self.instance.media_player_new()]
        self.active = 0
        self.queue = []
        self.index = -1
        self.shuffle = False
        self.repeat = "Off"
        self.rate = 1.0
        self.volume = 80
        self.crossfade = 3.0
        self.paused = False
        self.fade = None
        self.equalizer = None
        self._opening_at = 0
        self._stopped = True
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def set_queue(self, tracks: list[dict], first: int = 0):
        if not tracks:
            return
        self.stop()
        chosen = tracks[first]
        remaining = tracks[:first] + tracks[first + 1 :]
        if self.shuffle:
            random.shuffle(remaining)
            self.queue = [chosen, *remaining]
            self.index = 0
        else:
            self.queue = list(tracks)
            self.index = first
        self._load(self.active, self.queue[self.index])

    def _load(self, deck_index: int, track: dict, gain: float = 1):
        deck = self.decks[deck_index]
        deck.stop()
        media = self.instance.media_new_path(str(track["absolute_path"]))
        deck.set_media(media)
        media.release()
        deck.audio_set_volume(round(self.volume * gain))
        if self.equalizer:
            deck.set_equalizer(self.equalizer)
        if deck.play() == -1:
            self.stop()
            self.error.emit(f"Unable to play {track['path']}")
            return
        deck.set_rate(self.rate)
        self.paused = False
        self._stopped = False
        self._opening_at = time.monotonic()
        self.track_changed.emit(track)
        self.state_changed.emit("Playing")

    def next_index(self, manual=False):
        if not self.queue:
            return None
        if self.repeat == "One" and not manual:
            return self.index
        candidate = self.index + 1
        if candidate < len(self.queue):
            return candidate
        return 0 if self.repeat == "All" else None

    def next(self):
        candidate = self.next_index(manual=True)
        if candidate is None:
            self.stop()
            return
        self._finish_fade()
        self.index = candidate
        self._load(self.active, self.queue[self.index])

    def previous(self):
        if not self.queue:
            return
        self._finish_fade()
        self.index = max(0, self.index - 1)
        self._load(self.active, self.queue[self.index])

    def toggle_pause(self):
        if self._stopped:
            return
        self.paused = not self.paused
        now = time.monotonic()
        if self.paused:
            self._paused_at = now
        elif self.fade:
            self.fade[2] += now - self._paused_at
        for i, deck in enumerate(self.decks):
            if i == self.active or self.fade:
                deck.set_pause(int(self.paused))
        self.state_changed.emit("Paused" if self.paused else "Playing")

    def _finish_fade(self):
        if self.fade:
            self.decks[self.fade[0]].stop()
            self.fade = None
        self.decks[self.active].audio_set_volume(self.volume)

    def seek(self, position: int):
        self._finish_fade()
        self.decks[self.active].set_time(position)

    def set_volume(self, volume: int):
        self.volume = volume
        if not self.fade:
            self.decks[self.active].audio_set_volume(volume)

    def set_rate(self, rate: float):
        self.rate = rate
        for deck in self.decks:
            deck.set_rate(rate)

    def set_equalizer(self, gains: list[float]):
        equalizer = self.vlc.AudioEqualizer()
        equalizer.set_preamp(-max(0, max(gains, default=0)))
        for index, gain in enumerate(gains):
            equalizer.set_amp_at_index(gain, index)
        self.equalizer = equalizer
        for deck in self.decks:
            deck.set_equalizer(equalizer)

    def tick(self):
        if self._stopped or self.paused:
            return
        deck = self.decks[self.active]
        state = deck.get_state()
        now = time.monotonic()
        if state == self.vlc.State.Error:
            self.stop()
            self.error.emit("Playback failed. Check that the file exists and VLC can decode it.")
            return
        if state in (self.vlc.State.Opening, self.vlc.State.Buffering, self.vlc.State.NothingSpecial):
            if now - self._opening_at > 20:
                self.stop()
                self.error.emit("The audio engine did not finish opening the file.")
            return
        length, position = max(0, deck.get_length()), max(0, deck.get_time())
        self.position_changed.emit(position, length)
        if self.fade:
            old, new, started, seconds = self.fade
            factor = min(1.0, (now - started) / seconds)
            # Equal-power blend, with total gain limited by the user's volume.
            self.decks[old].audio_set_volume(round(self.volume * math.cos(factor * math.pi / 2)))
            self.decks[new].audio_set_volume(round(self.volume * math.sin(factor * math.pi / 2)))
            if factor >= 1:
                self._finish_fade()
        if state == self.vlc.State.Ended:
            candidate = self.next_index()
            if candidate is None:
                self.stop()
            else:
                self._finish_fade()
                self.index = candidate
                self._load(self.active, self.queue[self.index])
            return
        remaining = (length - position) / (1000 * self.rate)
        candidate = self.next_index()
        if (
            not self.fade
            and self.crossfade > 0
            and length > 0
            and position > 0
            and 0 < remaining <= self.crossfade
            and candidate is not None
        ):
            old = self.active
            self.active = 1 - old
            self.index = candidate
            self.fade = [old, self.active, now, max(0.08, remaining)]
            self._load(self.active, self.queue[self.index], gain=0)

    def stop(self):
        self.fade = None
        self.paused = False
        self._stopped = True
        for deck in self.decks:
            deck.stop()
        self.state_changed.emit("Stopped")
        self.position_changed.emit(0, 0)

    def close(self):
        self.timer.stop()
        self.stop()
        for deck in self.decks:
            deck.release()
        self.instance.release()
