"""Build a portable folder, optionally including an installed 64-bit VLC runtime."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--vlc-dir", type=Path, help="Include the full VLC installation and its plugins/licenses"
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Build Windows executables on Windows.")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        "JBs-MP3-Manager",
        "--paths",
        str(ROOT / "src"),
        "--hidden-import",
        "vlc",
    ]
    if args.vlc_dir:
        if not (args.vlc_dir / "libvlc.dll").is_file() or not (args.vlc_dir / "plugins").is_dir():
            parser.error("--vlc-dir must contain libvlc.dll and the plugins folder.")
        command += ["--add-data", str(args.vlc_dir.resolve()) + os.pathsep + "vlc"]
    command.append(str(ROOT / "launch.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    folder = ROOT / "dist" / "JBs-MP3-Manager"
    for document in ("README.md", "QUICK_START.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / document, folder / document)
    archive = shutil.make_archive(
        str(ROOT / "dist" / "JBs-MP3-Manager-Windows"), "zip", root_dir=ROOT / "dist", base_dir=folder.name
    )
    print(f"Built {archive}")


if __name__ == "__main__":
    main()
