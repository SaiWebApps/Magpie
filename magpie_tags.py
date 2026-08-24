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
    """Return (album, title) for a filename stem.

    The title is the WHOLE stem. The leading token is a grouping key, not
    something to remove: stripping it turns AC2_FlightOverVenice, AC3_ManInWolfHood
    and AC4_Pirates into titles that no longer say which game they came from,
    and the album is "AC" for all three so the information is gone for good.

    album is None when the name has no underscore — inventing an album from a
    single word is worse than leaving it unset."""
    aliases = DEFAULT_ALBUM_ALIASES if aliases is None else aliases
    if "_" not in stem:
        return None, stem
    prefix, rest = stem.split("_", 1)
    prefix, rest = prefix.strip(), rest.strip()
    if not prefix or not rest:
        return None, stem
    return aliases.get(prefix, prefix), stem


def _run(args, timeout=300):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def apply_tags(path, ffmpeg, aliases=None, log=lambda m: None, track=None):
    """Write album/title (and optionally track number) derived from the
    filename. Returns True if the file was rewritten.

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
    if track is not None:
        meta += ["-metadata", f"track={track}"]

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


# ---------------------------------------------------------------------------
# Track numbers
# ---------------------------------------------------------------------------
#
# A YouTube download inherits whatever "track" value the source happened to
# carry, which is meaningless here — most land on 1, some on 33. Players order
# an album by that number, so an album ends up shuffled no matter how it is
# sorted. Numbering each album by title makes (album, track) order the same as
# (album, title) in any player.


def read_track_number(path, ffprobe):
    """Current track number as an int, or None if unset or unreadable."""
    r = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format_tags=track",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60)
    raw = r.stdout.strip().split("/")[0]     # "3/12" -> "3"
    try:
        return int(raw)
    except ValueError:
        return None


def set_track_number(path, number, ffmpeg):
    """Write a track number, preserving everything else. True if rewritten."""
    path = Path(path)
    tmp = path.with_name(path.name + ".numbering" + path.suffix)
    meta = ["-metadata", f"track={number}"]
    for mapping in (["-map", "0", "-c", "copy", "-id3v2_version", "3"],
                    ["-map", "0:a", "-c:a", "copy"]):
        r = _run([ffmpeg, "-v", "error", "-y", "-i", str(path), *mapping, *meta, str(tmp)])
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            shutil.move(str(tmp), str(path))
            return True
        tmp.unlink(missing_ok=True)
    return False


def renumber_albums(directory, ffmpeg, ffprobe, aliases=None, albums=None,
                    log=lambda m: None):
    """Number every album's tracks by title order, 1..N.

    Only files whose number is already wrong are rewritten, so adding one track
    to a large album touches a handful of files rather than all of them.

    albums: restrict to these album names; None means every album present.
    Returns (files_rewritten, albums_touched)."""
    directory = Path(directory)
    grouped = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in TAGGABLE_SUFFIXES:
            continue
        album, title = parse_name(path.stem, aliases)
        if album is None:
            continue                  # no album, nothing to order within
        if albums is not None and album not in albums:
            continue
        grouped.setdefault(album, []).append((title, path))

    rewritten, touched = 0, 0
    for album, entries in sorted(grouped.items()):
        entries.sort(key=lambda pair: pair[0].lower())
        changed_here = 0
        for index, (title, path) in enumerate(entries, 1):
            if read_track_number(path, ffprobe) == index:
                continue
            if set_track_number(path, index, ffmpeg):
                rewritten += 1
                changed_here += 1
            else:
                log(f"could not set track number on {path.name}")
        if changed_here:
            touched += 1
            log(f"renumbered {album}: {changed_here} of {len(entries)} files")
    return rewritten, touched
