#!/usr/bin/env python3
"""Where Magpie keeps its files on each operating system.

    macOS    ~/Library/Application Support/Magpie   config, data, state, bin
             ~/Library/Logs/Magpie                  logs
             ~/Library/Caches/Magpie                cache
    Windows  %LOCALAPPDATA%\\Magpie                  config, data, state
             %LOCALAPPDATA%\\Magpie\\Logs, \\bin, \\Cache
    Linux    $XDG_CONFIG_HOME/magpie                config
             $XDG_DATA_HOME/magpie                  data, bin
             $XDG_STATE_HOME/magpie                 state; logs in log/
             $XDG_CACHE_HOME/magpie                 cache

These match platformdirs with appname "Magpie" and no app author. Windows
uses the local, not roaming, profile: the job database and the bundled tools
belong to this machine. Linux takes an XDG variable only when it holds an
absolute path, as the XDG Base Directory spec requires.

MAGPIE_HOME, when set, puts every directory under that one folder. Tests use
it so a run never touches the real files.

Standard library only.
"""

import os
import posixpath
import shlex
import sys
from pathlib import Path

APP = "Magpie"

# Windows Known Folder ids (KNOWNFOLDERID).
FOLDERID_LOCAL_APP_DATA = "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}"
FOLDERID_MUSIC = "{4BD8D571-6D19-48D3-BE97-422220080E43}"
FOLDERID_VIDEOS = "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}"
KF_FLAG_DONT_VERIFY = 0x4000


def _windows_known_folder(folder_id):
    """A Windows Known Folder path through SHGetKnownFolderPath, or None.

    Follows folder redirection, such as Music moved into OneDrive."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

    guid = GUID()
    if ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(folder_id), ctypes.byref(guid)) != 0:
        return None
    out = ctypes.c_wchar_p()
    try:
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), KF_FLAG_DONT_VERIFY, None, ctypes.byref(out))
    except OSError:
        return None
    try:
        return Path(out.value) if result == 0 and out.value else None
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def _xdg(env, name, fallback):
    """An XDG base directory: the variable when it is an absolute path,
    otherwise the spec's default. XDG paths are POSIX paths whatever OS is
    asking about them."""
    value = env.get(name, "")
    return Path(value) if value and posixpath.isabs(value) else fallback


class Dirs:
    """The directories for one platform. Construct with Dirs() for this
    machine; the arguments exist so tests can ask about another OS."""

    def __init__(self, platform=None, env=None, home=None):
        self.platform = platform or sys.platform
        env = os.environ if env is None else env
        home = Path(home) if home else Path.home()
        override = env.get("MAGPIE_HOME")
        # An isolated run (MAGPIE_HOME) must never reach outside its folder,
        # so it also never moves files from the user's real ~/.config.
        self.isolated = bool(override)
        if override:
            root = Path(override)
            self.config = self.data = self.state = root
            self.logs, self.bin, self.cache = root / "logs", root / "bin", root / "cache"
        elif self.platform == "darwin":
            support = home / "Library" / "Application Support" / APP
            self.config = self.data = self.state = support
            self.logs = home / "Library" / "Logs" / APP
            self.bin = support / "bin"
            self.cache = home / "Library" / "Caches" / APP
        elif self.platform == "win32":
            local = (_windows_known_folder(FOLDERID_LOCAL_APP_DATA) if self.platform == sys.platform else None) \
                or Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
            base = local / APP
            self.config = self.data = self.state = base
            self.logs, self.bin, self.cache = base / "Logs", base / "bin", base / "Cache"
        else:
            name = APP.lower()
            self.config = _xdg(env, "XDG_CONFIG_HOME", home / ".config") / name
            self.data = _xdg(env, "XDG_DATA_HOME", home / ".local" / "share") / name
            self.state = _xdg(env, "XDG_STATE_HOME", home / ".local" / "state") / name
            self.logs = self.state / "log"
            self.bin = self.data / "bin"
            self.cache = _xdg(env, "XDG_CACHE_HOME", home / ".cache") / name
        self._home = home
        self._env = env

    # ---- files ----

    @property
    def config_file(self):
        return self.config / "config.json"

    @property
    def jobs_db(self):
        return self.data / "jobs.sqlite3"

    @property
    def ibroadcast_token(self):
        return self.data / "ibroadcast-token.json"

    @property
    def ibroadcast_sent(self):
        return self.data / "ibroadcast-sent.json"

    @property
    def ibroadcast_names(self):
        return self.data / "ibroadcast-names.json"

    @property
    def log_file(self):
        return self.logs / "magpie.log"

    @property
    def server_token(self):
        return self.state / "server-token"

    # ---- where downloads go by default ----

    def music(self):
        return self._media("MUSIC", FOLDERID_MUSIC, "Music")

    def videos(self):
        # macOS calls the folder Movies; Linux and Windows call it Videos.
        return self._media("VIDEOS", FOLDERID_VIDEOS, "Movies" if self.platform == "darwin" else "Videos")

    def _media(self, xdg_key, folder_id, default_name):
        if self.platform == "win32" and self.platform == sys.platform:
            found = _windows_known_folder(folder_id)
            if found:
                return found
        if self.platform not in ("darwin", "win32"):
            found = user_dir(self._env, self._home, "XDG_%s_DIR" % xdg_key)
            if found:
                return found
        return self._home / default_name

    def ensure(self):
        """Create every directory, private to this user where the OS allows."""
        for path in {self.config, self.data, self.state, self.logs, self.bin, self.cache}:
            path.mkdir(parents=True, exist_ok=True)
            if self.platform != "win32":
                try:
                    path.chmod(0o700)
                except OSError:
                    pass


def user_dir(env, home, key):
    """A folder from ~/.config/user-dirs.dirs (the xdg-user-dirs file), or None.

    Each line is a shell assignment such as XDG_MUSIC_DIR="$HOME/Music"; the
    value is "$HOME/..." or an absolute path, and the last line for a key
    wins. "$HOME/" alone means the folder is turned off."""
    config = _xdg(env, "XDG_CONFIG_HOME", Path(home) / ".config")
    try:
        text = (config / "user-dirs.dirs").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    found = None
    for line in text.splitlines():
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            continue
        if len(words) != 1 or "=" not in words[0]:
            continue
        name, value = words[0].split("=", 1)
        if name != key:
            continue
        if value.startswith("$HOME/"):
            rest = value[len("$HOME/"):]
            found = Path(home) / rest if rest else None
        elif posixpath.isabs(value):
            found = Path(value)
    return found


# ---------------------------------------------------------------------------
# One-time move of the files Magpie kept in ~/.config on macOS
# ---------------------------------------------------------------------------

def legacy_moves(dirs):
    """(old path, new path) for each file the macOS-only Magpie kept in
    ~/.config. Empty on other platforms, where Magpie never ran before, and
    for an isolated run (MAGPIE_HOME), which must leave the real files alone."""
    if dirs.platform != "darwin" or dirs.isolated:
        return []
    old = dirs._home / ".config"
    return [
        (old / "magpie.json", dirs.config_file),
        (old / "magpie-jobs.sqlite3", dirs.jobs_db),
        (old / "magpie-ibroadcast.json", dirs.ibroadcast_token),
        (old / "magpie-ibroadcast-sent.json", dirs.ibroadcast_sent),
        (old / "magpie-ibroadcast-names.json", dirs.ibroadcast_names),
    ]


def migrate(dirs, log=lambda m: None):
    """Move each file from ~/.config to its new place.

    The ~/.config file is the one the running Magpie uses, so it wins: a file
    already at the new place (left by an older install) is kept beside it as
    <name>.before-move rather than deleted. Once moved, the ~/.config file is
    gone, so running this again changes nothing. Returns the (old, new) pairs
    that were moved."""
    moved = []
    for old, new in legacy_moves(dirs):
        if not old.is_file():
            continue
        new.parent.mkdir(parents=True, exist_ok=True)
        if new.exists():
            kept = new.with_name(new.name + ".before-move")
            os.replace(new, kept)
            log(f"kept the existing {new} as {kept}")
        os.replace(old, new)
        moved.append((old, new))
        log(f"moved {old} -> {new}")
    return moved


DIRS = Dirs()
