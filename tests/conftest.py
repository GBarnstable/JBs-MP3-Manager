import os
import shutil
import subprocess

import pytest

from jbs_mp3_manager.library import Library

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp(tmp_path_factory):
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent, QSettings
    from PySide6.QtWidgets import QApplication

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path_factory.mktemp("app-settings"))
    )
    application = QApplication.instance() or QApplication([])
    yield application
    application.closeAllWindows()
    # Collect Qt/Python signal closures while the native application still exists.
    gc.collect()
    application.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


@pytest.fixture(autouse=True)
def dispose_test_windows(request):
    yield
    if "qapp" not in request.fixturenames:
        return
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    # close() only hides a widget. Signal closures can retain those hidden windows
    # until interpreter shutdown; dispose them while their QApplication is alive.
    for widget in QApplication.topLevelWidgets():
        if shiboken6.isValid(widget):
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


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
