#!/usr/bin/env python3
"""Derive audio tags from Magpie's filename convention.

Files are named {Album}_{Track}.mp3, split on the FIRST underscore:

    AC_Mirage_Escape.mp3      -> album "AC",     title "Mirage_Escape"
    WetLeg_NotFun.mp3         -> album "WetLeg", title "NotFun"
    Espresso.mp3              -> no album,       title "Espresso"

Album aliases fold related prefixes into one album — every Assassin's Creed
numbering lands under "AC". Override album_aliases in ~/.config/magpie.json to
add more.

Without this, a downloaded file carries no tags at all and iBroadcast falls
back to the filename, which is how the library ended up full of "Unknown
Album" and titles like "Music/AC3_AgainstAllOdds.mp3".
"""

import shutil
import subprocess
from pathlib import Path

# Prefix -> album it should be filed under.
DEFAULT_ALBUM_ALIASES = {
    "AC": "AC", "AC2": "AC", "AC3": "AC", "AC4": "AC", "ACB": "AC",
}

TAGGABLE_SUFFIXES = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav"}


def parse_name(stem, aliases=None):
    """Return (album, title) for a filename stem. album is None when the name
    has no underscore — inventing an album from a single word is worse than
    leaving it unset."""
    aliases = DEFAULT_ALBUM_ALIASES if aliases is None else aliases
    if "_" not in stem:
        return None, stem
    album, title = stem.split("_", 1)
    album, title = album.strip(), title.strip()
    if not album or not title:
        return None, stem
    return aliases.get(album, album), title


def _run(args, timeout=300):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def apply_tags(path, ffmpeg, aliases=None, log=lambda m: None):
    """Write album/title tags derived from the filename. Returns True if the
    file was rewritten.

    Never raises and never leaves a partial file: the retag goes to a temp
    alongside and only replaces the original once ffmpeg reports success."""
    path = Path(path)
    if path.suffix.lower() not in TAGGABLE_SUFFIXES or not path.is_file():
        return False

    album, title = parse_name(path.stem, aliases)
    tmp = path.with_name(path.name + ".tagging" + path.suffix)

    meta = ["-metadata", f"title={title}"]
    if album:
        meta += ["-metadata", f"album={album}"]

    # Try to keep everything, including embedded cover art. A file whose art is
    # stored as a second stream makes a plain "-c copy" to mp3 fail, so fall
    # back to audio only rather than leaving the file untagged.
    attempts = [
        ["-map", "0", "-c", "copy", "-id3v2_version", "3"],
        ["-map", "0:a", "-c:a", "copy"],
    ]
    for mapping in attempts:
        result = _run([ffmpeg, "-v", "error", "-y", "-i", str(path),
                       *mapping, *meta, str(tmp)])
        if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            shutil.move(str(tmp), str(path))
            log(f"tagged {path.name}: album={album!r} title={title!r}")
            return True
        tmp.unlink(missing_ok=True)

    log(f"could not tag {path.name}: {result.stderr.strip()[:160]}")
    return False


def tag_all(paths, ffmpeg, aliases=None, log=lambda m: None):
    """Tag a batch. Returns the number of files rewritten."""
    return sum(1 for p in paths if apply_tags(p, ffmpeg, aliases, log))
