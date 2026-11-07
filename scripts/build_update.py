"""Build a small, verified overlay for an existing 0.3 portable application."""

import argparse
import hashlib
import shutil
import subprocess
from pathlib import Path


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if not (args.baseline / "JBs-MP3-Manager.exe").is_file():
        raise ValueError("The baseline must be the extracted 0.3 application folder.")
    args.output.mkdir(parents=True, exist_ok=False)
    for source in args.current.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(args.current)
        old = args.baseline / relative
        if not old.is_file() or sha(source) != sha(old):
            target = args.output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (args.output / "UPDATE_INSTRUCTIONS.txt").write_text(
        "UPDATE AN EXISTING VERSION 0.3 APPLICATION TO 0.4.1\n\n"
        "1. Close JB's MP3 Manager.\n"
        "2. Open the ZIP and copy ALL its contents into your existing 0.3 application folder,\n"
        "   the folder containing JBs-MP3-Manager.exe. Accept replacing existing files.\n"
        "3. Keep the original _internal folder; merge the updated files into it.\n"
        "4. Start JBs-MP3-Manager.exe from that same folder.\n\n"
        "This update is not a standalone application. Use the full download for other versions.\n"
        "Do not extract this into your song library. Your existing library opens directly;\n"
        "there is no need to recreate it or import categories again.\n",
        encoding="utf-8",
    )
    # Test precisely what a user receives after merging the update into 0.3.
    patched = args.output.parent / "patched-0.3"
    shutil.copytree(args.baseline, patched)
    shutil.copytree(args.output, patched, dirs_exist_ok=True)
    for source in args.current.rglob("*"):
        if source.is_file() and sha(source) != sha(patched / source.relative_to(args.current)):
            raise ValueError("The update did not reproduce the new application's files.")
    subprocess.run(
        [
            str((patched / "JBs-MP3-Manager.exe").resolve()),
            "--smoke-test",
            "--require-audio",
            "--require-fingerprint",
        ],
        check=True,
        timeout=60,
    )
    full = args.current.parent / "JBs-MP3-Manager-Windows.zip"
    archive = Path(shutil.make_archive(str(args.output), "zip", root_dir=args.output))
    if archive.stat().st_size >= full.stat().st_size / 2:
        raise ValueError("The update is too large to offer as a small download; use the full package.")
    print(f"Verified 0.3 update: {archive.stat().st_size / 1024 / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
