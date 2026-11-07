"""Two independent VLC decks, prepared in advance for overlapping playback."""

from __future__ import annotations

import math
import os
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

_dll_handles = []
# VLC 3's equalizer.c multiplies its dry signal by EQZ_IN_FACTOR (0.25).
EQ_UNITY_PREAMP = 20 * math.log10(4)


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


def instance_options(platform=None):
    options = ["--no-video", "--quiet", "--audio-filter=scaletempo", "--no-volume-save"]
    if (platform or sys.platform) == "win32":
        # MMDevice sets shared session volume. DirectSound has per-buffer volume,
        # so lowering one deck does not also lower the overlapping deck.
        options.append("--aout=directsound")
    return options


class Playback(QObject):
    track_changed = Signal(object)
    position_changed = Signal(int, int)
    state_changed = Signal(str)
    error = Signal(str)

    def __init__(self, parent=None, *, vlc_module=None, instance=None):
        super().__init__(parent)
        self.vlc = vlc_module or load_vlc()
        self.instance = instance or self.vlc.Instance(*instance_options())
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
        self.prepared = None
        self.equalizer = None
        self.saved_gains = [0] * 10
        self._failed_preparation = None
        self._opening_at = 0
        self._stopped = True
        self.timer = QTimer(self)
        self.timer.setInterval(40)
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

    def _gain(self, deck_index, gain):
        # Undo VLC's cubic volume taper for an equal-power amplitude blend.
        value = round(self.volume * max(0.0, min(1.0, gain)) ** (1 / 3))
        self.decks[deck_index].audio_set_volume(value)

    def _configure(self, deck_index, track, gain=1, *, paused=False):
        deck = self.decks[deck_index]
        deck.stop()
        media = self.instance.media_new_path(str(track["absolute_path"]))
        media.add_option(":file-caching=100")
        if paused:
            media.add_option(":start-paused")
        deck.set_media(media)
        media.release()
        deck.set_equalizer(self.equalizer)
        self._gain(deck_index, gain)
        success = deck.play() != -1
        deck.set_rate(self.rate)
        return success

    def _load(self, deck_index, track, gain=1):
        self._discard_prepared()
        if not self._configure(deck_index, track, gain):
            self.stop()
            self.error.emit(f"Unable to play {track['path']}")
            return
        self.paused = False
        self._stopped = False
        self._opening_at = time.monotonic()
        self._failed_preparation = None
        self.track_changed.emit(track)
        self.state_changed.emit("Playing")

    def next_index(self, manual=False):
        if not self.queue:
            return None
        if self.repeat == "One" and not manual:
            return self.index
        candidate = self.index + 1
        return candidate if candidate < len(self.queue) else (0 if self.repeat == "All" else None)

    def _discard_prepared(self):
        if self.prepared:
            self.decks[self.prepared["deck"]].stop()
            self.prepared = None

    def _prepare(self, index):
        deck_index = 1 - self.active
        if self._configure(deck_index, self.queue[index], 0, paused=True):
            self.prepared = {"index": index, "deck": deck_index, "ready": False, "started": time.monotonic()}
        else:
            self._failed_preparation = (self.index, index)

    def _prepared_ready(self):
        if not self.prepared:
            return False
        prepared = self.prepared
        deck = self.decks[prepared["deck"]]
        state = deck.get_state()
        if state == self.vlc.State.Error or time.monotonic() - prepared["started"] > 20:
            self._failed_preparation = (self.index, prepared["index"])
            self._discard_prepared()
            return False
        if not prepared["ready"] and state in (self.vlc.State.Paused, self.vlc.State.Playing):
            self._gain(prepared["deck"], 0)
            if state == self.vlc.State.Playing:
                deck.set_pause(1)
                deck.set_time(0)
            prepared["ready"] = True
        return prepared["ready"]

    def _begin_transition(self, remaining):
        prepared = self.prepared
        self.prepared = None
        self._transition_index = prepared["index"]
        self.fade = [self.active, prepared["deck"], None, max(0.08, min(self.crossfade, remaining))]
        self._transition_opened = time.monotonic()
        self.decks[prepared["deck"]].set_pause(0)
        self.decks[prepared["deck"]].set_rate(self.rate)
        self._tick_fade(time.monotonic())

    def _tick_fade(self, now):
        old, new, started, seconds = self.fade
        incoming = self.decks[new]
        state = incoming.get_state()
        if state == self.vlc.State.Error or (started is None and now - self._transition_opened > 20):
            self._finish_fade()
            self.error.emit("The next song could not start. The current song was kept at full volume.")
            return
        if started is None:
            if state != self.vlc.State.Playing:
                return
            self.active = new
            self.index = self._transition_index
            self.fade[2] = started = now
            self._opening_at = now
            self.track_changed.emit(self.queue[self.index])
        fraction = max(0, min(1, (now - started) / seconds))
        self._gain(old, math.cos(fraction * math.pi / 2))
        self._gain(new, math.sin(fraction * math.pi / 2))
        self.position_changed.emit(max(0, incoming.get_time()), max(0, incoming.get_length()))
        if fraction >= 1 or state == self.vlc.State.Ended:
            self._finish_fade()

    def next(self):
        candidate = self.next_index(manual=True)
        if candidate is None:
            self.stop()
            return
        self._finish_fade()
        self._discard_prepared()
        self.index = candidate
        self._load(self.active, self.queue[self.index])

    def previous(self):
        if not self.queue:
            return
        self._finish_fade()
        self._discard_prepared()
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
            if self.fade[2] is not None:
                self.fade[2] += now - self._paused_at
            self._transition_opened += now - self._paused_at
        for i, deck in enumerate(self.decks):
            if i == self.active or self.fade:
                deck.set_pause(int(self.paused))
        self.state_changed.emit("Paused" if self.paused else "Playing")

    def _finish_fade(self):
        if self.fade:
            old, new, started, _ = self.fade
            self.decks[new if started is None else old].stop()
            self.fade = None
        self._gain(self.active, 1)

    def seek(self, position):
        self._finish_fade()
        self._discard_prepared()
        self.decks[self.active].set_time(position)

    def set_volume(self, volume):
        self.volume = volume
        if self.fade:
            self._tick_fade(self._paused_at if self.paused else time.monotonic())
        else:
            self._gain(self.active, 1)

    def set_rate(self, rate):
        self.rate = rate
        for deck in self.decks:
            deck.set_rate(rate)

    def set_equalizer(self, gains):
        if len(gains) != 10:
            raise ValueError("The equalizer needs ten band values.")
        previous = self.equalizer
        equalizer = None
        if any(gains):
            equalizer = self.vlc.AudioEqualizer()
            equalizer.set_preamp(EQ_UNITY_PREAMP)
            for index, gain in enumerate(gains):
                equalizer.set_amp_at_index(gain, index)
        for deck in self.decks:
            deck.set_equalizer(equalizer)
        self.equalizer = equalizer
        self.saved_gains = list(gains)
        if previous is not None:
            previous.release()

    def tick(self):
        if self._stopped or self.paused:
            return
        now = time.monotonic()
        if self.fade:
            self._tick_fade(now)
            if self.fade:
                return
        deck = self.decks[self.active]
        state = deck.get_state()
        if state == self.vlc.State.Error:
            self.stop()
            self.error.emit("Playback failed. Check that the file exists and VLC can decode it.")
            return
        if state in (self.vlc.State.Opening, self.vlc.State.Buffering, self.vlc.State.NothingSpecial):
            if now - self._opening_at > 20:
                self.stop()
                self.error.emit("The audio engine did not finish opening the file.")
            return
        self._gain(self.active, 1)
        length, position = max(0, deck.get_length()), max(0, deck.get_time())
        self.position_changed.emit(position, length)
        candidate = self.next_index()
        if self.prepared and (self.prepared["index"] != candidate or self.crossfade == 0):
            self._discard_prepared()
        if state == self.vlc.State.Ended:
            if candidate is None:
                self.stop()
            elif self._prepared_ready():
                self._begin_transition(0)
            else:
                self.index = candidate
                self._load(self.active, self.queue[self.index])
            return
        remaining = (length - position) / (1000 * self.rate)
        if (
            self.crossfade > 0
            and candidate is not None
            and length > 0
            and position > 0
            and 0 < remaining <= self.crossfade + 2
        ):
            if not self.prepared and self._failed_preparation != (self.index, candidate):
                self._prepare(candidate)
            if self._prepared_ready() and remaining <= self.crossfade:
                self._begin_transition(remaining)

    def stop(self):
        self.fade = None
        self.prepared = None
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
        if self.equalizer is not None:
            self.equalizer.release()
        self.instance.release()
