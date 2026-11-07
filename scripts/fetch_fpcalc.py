"""Fetch the pinned upstream Chromaprint tool into the build workspace."""

import hashlib
import platform
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = "1.6.2"
ROOT = Path(__file__).resolve().parents[1]
CHECKSUMS = {
    "windows-x86_64.zip": "b74369726676670af3ab0eadff45f960cf969fdf4a87a971ba37eaf8d530e874",
    "linux-x86_64.tar.gz": "c968ad4a0821592a79b158fb2e1f46cbe0a22a0a1dbc0743c67d68e75d15d2bb",
    "linux-arm64.tar.gz": "3aa1498126ed272d805bc9d081c77965c9e1e1a0891bb112c13b56ef2ad47bad",
}


def main():
    name = (
        f"chromaprint-fpcalc-{VERSION}-windows-x86_64.zip"
        if sys.platform == "win32"
        else f"chromaprint-fpcalc-{VERSION}-linux-{'arm64' if platform.machine() == 'aarch64' else 'x86_64'}.tar.gz"
    )
    folder = ROOT / "build" / "fingerprint"
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / name
    url = f"https://github.com/acoustid/chromaprint/releases/download/v{VERSION}/{name}"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    expected = next(value for suffix, value in CHECKSUMS.items() if name.endswith(suffix))
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Fingerprint tool checksum did not match the pinned upstream release.")
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                if not member.is_dir():
                    (folder / Path(member.filename).name).write_bytes(package.read(member))
    else:
        with tarfile.open(archive) as package:
            for member in package.getmembers():
                if member.isfile():
                    stream = package.extractfile(member)
                    if stream:
                        (folder / Path(member.name).name).write_bytes(stream.read())
        (folder / "fpcalc").chmod(0o755)
    for document in ("LICENSE.md", "README.md"):
        target = folder / ("CHROMAPRINT-" + document)
        if not target.exists():
            urllib.request.urlretrieve(
                f"https://raw.githubusercontent.com/acoustid/chromaprint/v{VERSION}/{document}", target
            )
    print(f"Fingerprint tool available in {folder}")


if __name__ == "__main__":
    main()
