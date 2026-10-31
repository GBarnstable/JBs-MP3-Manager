import os
import shutil
import subprocess

import pytest

from jbs_mp3_manager.library import Library

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp(tmp_path_factory):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path_factory.mktemp("app-settings"))
    )
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture(scope="session")
def tones(tmp_path_factory):
    folder = tmp_path_factory.mktemp("generated-audio")
    binary = shutil.which("ffmpeg")
    if not binary:
        import imageio_ffmpeg

        binary = imageio_ffmpeg.get_ffmpeg_exe()
    paths = []
    for frequency in (440, 660, 880):
        path = folder / f"tone-{frequency}.mp3"
        subprocess.run(
            [
                binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:duration=1.5",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "5",
                str(path),
            ],
            check=True,
        )
        paths.append(path)
    return paths


@pytest.fixture
def library(tmp_path, tones):
    root = tmp_path / "All songs"
    root.mkdir()
    for source, name in zip(tones, ("Alpha.mp3", "Bravo.MP3", "Café #3.mp3")):
        shutil.copy2(source, root / name)
    with Library(root, create=True) as lib:
        lib.scan()
        yield lib
