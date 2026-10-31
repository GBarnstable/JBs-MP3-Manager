"""MP3 inspection and ID3 writes. Category membership never touches audio files."""

from __future__ import annotations

import hashlib
import io
import os
import struct
import tempfile
from pathlib import Path

from mutagen.id3 import ID3, TALB, TCON, TDRC, TIT2, TPE1, TRCK, ID3NoHeaderError
from mutagen.mp3 import MP3

FIELDS = ("title", "artist", "album", "genre", "year", "track")
FRAMES = dict(zip(FIELDS, (TIT2, TPE1, TALB, TCON, TDRC, TRCK)))


def digest_range(path: Path, start: int = 0, end: int | None = None) -> str:
    digest = hashlib.sha256()
    end = path.stat().st_size if end is None else end
    with path.open("rb") as stream:
        stream.seek(start)
        remaining = end - start
        while remaining > 0:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise OSError(f"File changed while reading: {path.name}")
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def inspect_mp3(path: Path) -> dict:
    before = path.stat()
    audio = MP3(path)
    tags = audio.tags or ID3()
    metadata = {field: str(tags.get(frame.__name__, "")) for field, frame in FRAMES.items()}
    end = before.st_size
    with path.open("rb") as stream:
        if end >= 128:
            stream.seek(end - 128)
            if stream.read(3) == b"TAG":
                end -= 128
        if end >= 32:
            stream.seek(end - 32)
            footer = stream.read(32)
            if footer[:8] == b"APETAGEX":
                size, flags = struct.unpack_from("<II", footer, 12)
                if 32 <= size <= end - audio.info.frame_offset:
                    end -= size
                    if flags & (1 << 31):  # Optional APE header is not included in the footer size.
                        stream.seek(max(0, end - 32))
                        if stream.read(8) == b"APETAGEX":
                            end -= 32
    result = {
        **metadata,
        "duration": audio.info.length,
        "sha256": digest_range(path),
        "audio_sha256": digest_range(path, audio.info.frame_offset, end),
        "size": before.st_size,
        "mtime_ns": before.st_mtime_ns,
    }
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError(f"File is still changing; will retry next scan: {path.name}")
    return result


def write_tags(path: Path, values: dict[str, str], backup_dir: Path) -> None:
    unknown = set(values) - set(FIELDS)
    if unknown:
        raise ValueError(f"Unsupported fields: {', '.join(sorted(unknown))}")
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    # Validate all values before touching the file. Unknown frames and artwork survive.
    replacements = {}
    for field, value in values.items():
        value = value.strip()
        replacements[field] = FRAMES[field](encoding=3, text=[value]) if value else None
    backup_dir.mkdir(parents=True, exist_ok=True)
    original = io.BytesIO()
    tags.save(original)
    with tempfile.NamedTemporaryFile(dir=backup_dir, suffix=".id3", delete=False) as backup:
        backup.write(original.getvalue())
    for field, frame in replacements.items():
        tags.delall(FRAMES[field].__name__)
        if frame is not None:
            tags.add(frame)
    # Work on a sibling temporary file so a failed tag write leaves the original intact.
    import shutil

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".jbs-tag-tmp", delete=False) as temp:
            temp_path = Path(temp.name)
        shutil.copy2(path, temp_path)
        tags.save(temp_path, v2_version=3)
        inspect_mp3(temp_path)  # Do not replace the original with an invalid file.
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
