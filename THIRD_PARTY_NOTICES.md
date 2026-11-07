# Third-party components

JB's MP3 Manager uses these components; their respective licenses apply:

- **PySide6 / Qt for Python** — LGPLv3/GPLv3 or commercial license. https://doc.qt.io/qtforpython-6/licenses.html · https://code.qt.io/pyside/pyside-setup.git/
- **Mutagen** — GPLv2 or later. https://github.com/quodlibet/mutagen
- **python-vlc** — LGPLv2.1 or later. https://github.com/oaubert/python-vlc
- **libVLC and VLC plugins**, when included in a Windows build — libVLC is LGPLv2.1 or later; VLC/plugins include components under GPL and other licenses. The entire installed VLC runtime directory, including its distributed license/notice files, is copied into `_internal/vlc`. https://www.videolan.org/legal.html · https://download.videolan.org/pub/videolan/vlc/ (matching release sources) · https://code.videolan.org/videolan/vlc
- **Chromaprint/fpcalc 1.6.2** — distributed upstream binaries and source are available at https://github.com/acoustid/chromaprint/releases/tag/v1.6.2 and https://github.com/acoustid/chromaprint/tree/v1.6.2. Chromaprint includes MIT-licensed code and FFmpeg code under LGPLv2.1; its overall license is LGPLv2.1, with additional licenses applying to its FFmpeg/FFT dependencies. Pinned-release checksums are verified during builds; upstream license/readme notices are bundled in `_internal/fingerprint`. https://github.com/acoustid/chromaprint/blob/v1.6.2/LICENSE.md · https://ffmpeg.org/legal.html · https://ffmpeg.org/releases/ (matching FFmpeg source releases).
- **Python**, bundled in a frozen application — Python Software Foundation License. https://docs.python.org/3/license.html
- **PyInstaller bootloader** — GPL with a distribution exception. https://pyinstaller.org/en/stable/license.html

Development tests may use FFmpeg from `imageio-ffmpeg`. The separate imageio FFmpeg executable is a development dependency and is not included by the build script; the upstream fpcalc tool contains its own FFmpeg components. https://github.com/imageio/imageio-ffmpeg · https://ffmpeg.org/legal.html

This file is an index; it does not replace upstream license texts or corresponding-source requirements. Keep notices distributed with each runtime when redistributing builds.
