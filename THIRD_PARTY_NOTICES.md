# Third-party components

JB's MP3 Manager uses these components; their respective licenses apply:

- **PySide6 / Qt for Python** — LGPLv3/GPLv3 or commercial license. https://doc.qt.io/qtforpython-6/licenses.html · https://code.qt.io/pyside/pyside-setup.git/
- **Mutagen** — GPLv2 or later. https://github.com/quodlibet/mutagen
- **python-vlc** — LGPLv2.1 or later. https://github.com/oaubert/python-vlc
- **libVLC and VLC plugins**, when included in a Windows build — libVLC is LGPLv2.1 or later; VLC/plugins include components under GPL and other licenses. The entire installed VLC runtime directory, including its distributed license/notice files, is copied into `_internal/vlc`. https://www.videolan.org/legal.html · https://download.videolan.org/pub/videolan/vlc/ (matching release sources) · https://code.videolan.org/videolan/vlc
- **Python**, bundled in a frozen application — Python Software Foundation License. https://docs.python.org/3/license.html
- **PyInstaller bootloader** — GPL with a distribution exception. https://pyinstaller.org/en/stable/license.html

Development tests may use FFmpeg from `imageio-ffmpeg`. FFmpeg is a development dependency and is not included in the application package by this build script. https://github.com/imageio/imageio-ffmpeg · https://ffmpeg.org/legal.html

This file is an index; it does not replace upstream license texts or corresponding-source requirements. Keep notices distributed with each runtime when redistributing builds.
