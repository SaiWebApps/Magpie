#!/usr/bin/env python3
"""Per-OS behaviour: folders, the macOS file move, file names, the server's
bind, the bundled-tool installer, and how the iBroadcast token is protected.

These run in-process with no network.
"""

import gzip
import hashlib
import io
import json
import os
import shutil
import socket
import stat
import sys
import tarfile
import tempfile
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import magpie_ibroadcast  # noqa: E402
import magpie_paths  # noqa: E402
import magpie_server  # noqa: E402
import magpie_tools  # noqa: E402


class TempDirTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="magpie-platform-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class DirsTests(TempDirTestCase):

    def test_macos(self):
        d = magpie_paths.Dirs("darwin", {}, self.tmp)
        support = self.tmp / "Library" / "Application Support" / "Magpie"
        self.assertEqual((d.config, d.data, d.state, d.bin), (support, support, support, support / "bin"))
        self.assertEqual(d.logs, self.tmp / "Library" / "Logs" / "Magpie")
        self.assertEqual(d.cache, self.tmp / "Library" / "Caches" / "Magpie")
        self.assertEqual(d.music(), self.tmp / "Music")
        self.assertEqual(d.videos(), self.tmp / "Movies")

    def test_windows_uses_local_app_data(self):
        local = self.tmp / "AppData" / "Local"
        d = magpie_paths.Dirs("win32", {"LOCALAPPDATA": str(local)}, self.tmp)
        if sys.platform == "win32":
            self.skipTest("on Windows the real Known Folder wins over LOCALAPPDATA")
        base = local / "Magpie"
        self.assertEqual((d.config, d.data, d.state), (base, base, base))
        self.assertEqual((d.logs, d.bin, d.cache), (base / "Logs", base / "bin", base / "Cache"))
        self.assertEqual(d.videos(), self.tmp / "Videos")

    def test_linux_defaults(self):
        d = magpie_paths.Dirs("linux", {}, self.tmp)
        self.assertEqual(d.config, self.tmp / ".config" / "magpie")
        self.assertEqual(d.data, self.tmp / ".local" / "share" / "magpie")
        self.assertEqual(d.state, self.tmp / ".local" / "state" / "magpie")
        self.assertEqual(d.logs, self.tmp / ".local" / "state" / "magpie" / "log")
        self.assertEqual(d.bin, self.tmp / ".local" / "share" / "magpie" / "bin")
        self.assertEqual(d.cache, self.tmp / ".cache" / "magpie")
        self.assertEqual(d.music(), self.tmp / "Music")
        self.assertEqual(d.videos(), self.tmp / "Videos")

    def test_linux_xdg_variables_absolute_only(self):
        env = {"XDG_CONFIG_HOME": "/xdg/cfg", "XDG_DATA_HOME": "relative/data"}
        d = magpie_paths.Dirs("linux", env, self.tmp)
        self.assertEqual(d.config, Path("/xdg/cfg") / "magpie")
        self.assertEqual(d.data, self.tmp / ".local" / "share" / "magpie")

    def test_linux_user_dirs_file(self):
        config = self.tmp / ".config"
        config.mkdir()
        (config / "user-dirs.dirs").write_text(
            '# written by xdg-user-dirs-update\n'
            'XDG_MUSIC_DIR="$HOME/Musik"\n'
            'XDG_VIDEOS_DIR="/srv/videos"\n'
            'XDG_MUSIC_DIR="$HOME/Musique"\n', encoding="utf-8")
        d = magpie_paths.Dirs("linux", {}, self.tmp)
        self.assertEqual(d.music(), self.tmp / "Musique")
        self.assertEqual(d.videos(), Path("/srv/videos"))

    def test_linux_user_dir_turned_off_falls_back(self):
        config = self.tmp / ".config"
        config.mkdir()
        (config / "user-dirs.dirs").write_text('XDG_MUSIC_DIR="$HOME/"\n', encoding="utf-8")
        self.assertEqual(magpie_paths.Dirs("linux", {}, self.tmp).music(), self.tmp / "Music")

    def test_magpie_home_overrides_everything(self):
        root = self.tmp / "isolated"
        for platform in ("darwin", "win32", "linux"):
            d = magpie_paths.Dirs(platform, {"MAGPIE_HOME": str(root)}, self.tmp)
            self.assertEqual((d.config, d.data, d.state), (root, root, root))
            self.assertEqual((d.logs, d.bin, d.cache), (root / "logs", root / "bin", root / "cache"))


class MigrationTests(TempDirTestCase):

    def legacy(self):
        old = self.tmp / ".config"
        old.mkdir()
        files = {"magpie.json": '{"audio_dir": "/x"}', "magpie-jobs.sqlite3": "db",
                 "magpie-ibroadcast.json": '{"access_token": "t"}',
                 "magpie-ibroadcast-sent.json": "{}", "magpie-ibroadcast-names.json": "[]"}
        for name, text in files.items():
            (old / name).write_text(text)
        return old, files

    def test_macos_files_move_once(self):
        old, files = self.legacy()
        d = magpie_paths.Dirs("darwin", {}, self.tmp)
        moved = magpie_paths.migrate(d)
        self.assertEqual(len(moved), 5)
        self.assertEqual(d.config_file.read_text(), files["magpie.json"])
        self.assertEqual(d.jobs_db.read_text(), "db")
        self.assertEqual(d.ibroadcast_token.read_text(), files["magpie-ibroadcast.json"])
        self.assertEqual([p.name for p in old.iterdir()], [])
        self.assertEqual(magpie_paths.migrate(d), [])

    def test_live_file_wins_and_the_older_one_is_kept(self):
        old, files = self.legacy()
        d = magpie_paths.Dirs("darwin", {}, self.tmp)
        d.config_file.parent.mkdir(parents=True)
        d.config_file.write_text('{"audio_dir": "/from-an-older-install"}')
        magpie_paths.migrate(d)
        self.assertEqual(d.config_file.read_text(), files["magpie.json"])
        kept = d.config_file.with_name("config.json.before-move")
        self.assertEqual(kept.read_text(), '{"audio_dir": "/from-an-older-install"}')
        self.assertFalse((old / "magpie.json").exists())

    def test_isolated_run_never_touches_the_real_files(self):
        """A run with MAGPIE_HOME (every test server) must leave the files in
        the home folder's ~/.config exactly where they are."""
        old, files = self.legacy()
        d = magpie_paths.Dirs("darwin", {"MAGPIE_HOME": str(self.tmp / "isolated")}, self.tmp)
        self.assertEqual(magpie_paths.legacy_moves(d), [])
        self.assertEqual(magpie_paths.migrate(d), [])
        self.assertEqual(sorted(p.name for p in old.iterdir()), sorted(files))

    def test_other_platforms_move_nothing(self):
        self.legacy()
        for platform in ("linux", "win32"):
            env = {"LOCALAPPDATA": str(self.tmp / "AppData" / "Local")}
            self.assertEqual(magpie_paths.migrate(magpie_paths.Dirs(platform, env, self.tmp)), [])


class SanitizeTests(unittest.TestCase):

    def test_names(self):
        cases = {
            "Song.mp3": "Song",
            "Song.mp3.m4a": "Song",
            'a:b/c\\d*e?f"g<h>i|j': "abcdefghij",
            "tab\there": "tabhere",
            "Song. . ": "Song",
            "CON": "_CON",
            "con.txt": "_con.txt",
            "Nul": "_Nul",
            "COM1": "_COM1",
            "LPT9.backup": "_LPT9.backup",
            "COM¹": "_COM¹",
            "CONSOLE": "CONSOLE",
            "COM10": "COM10",
            "...": "audio",
            "": "audio",
        }
        for raw, expected in cases.items():
            self.assertEqual(magpie_server.sanitize(raw), expected, raw)

    def test_length_is_capped(self):
        self.assertEqual(len(magpie_server.sanitize("x" * 400)), magpie_server.MAX_NAME)


class ServerBindTests(unittest.TestCase):

    def test_bind_does_no_reverse_lookup(self):
        """http.server's own bind calls socket.getfqdn, which can stall for
        half a minute; Magpie's server must not call it at all."""
        saved = socket.getfqdn

        def refuse(*args):
            raise AssertionError("getfqdn called")

        socket.getfqdn = refuse
        try:
            server = magpie_server.MagpieHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        finally:
            socket.getfqdn = saved
        try:
            self.assertEqual(server.server_name, "127.0.0.1")
            self.assertGreater(server.server_port, 0)
        finally:
            server.server_close()

    @unittest.skipUnless(sys.platform == "win32", "Windows-only socket option")
    def test_windows_binds_exclusively(self):
        server = magpie_server.MagpieHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        try:
            self.assertTrue(server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE))
        finally:
            server.server_close()


class ToolsTests(TempDirTestCase):
    """fetch and unpack against local file:// releases."""

    def release(self, name, data):
        path = self.tmp / "release" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        return path.as_uri(), hashlib.sha256(data).hexdigest()

    def install_one(self, kind, data, member=None):
        url, digest = self.release(f"asset.{kind}", data)
        source = magpie_tools.Source(url, digest, kind, member)
        archive = magpie_tools.fetch(source, self.tmp / "cache")
        dest = self.tmp / "bin" / "tool"
        dest.parent.mkdir(exist_ok=True)
        magpie_tools.unpack(source, archive, dest)
        return dest

    def test_each_archive_kind(self):
        payload = b"#!/bin/sh\necho tool\n"
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as z:
            z.writestr("pkg/bin/tool", payload)
        tarred = io.BytesIO()
        with tarfile.open(fileobj=tarred, mode="w:xz") as t:
            info = tarfile.TarInfo("pkg/bin/tool")
            info.size = len(payload)
            t.addfile(info, io.BytesIO(payload))
        for kind, data, member in (("file", payload, None), ("gzip", gzip.compress(payload), None),
                                   ("zip", zipped.getvalue(), "pkg/bin/tool"),
                                   ("tar.xz", tarred.getvalue(), "pkg/bin/tool")):
            dest = self.install_one(kind, data, member)
            self.assertEqual(dest.read_bytes(), payload, kind)
            if sys.platform != "win32":
                self.assertTrue(dest.stat().st_mode & stat.S_IXUSR, kind)

    def test_wrong_checksum_installs_nothing(self):
        url, _ = self.release("asset.bin", b"tampered")
        source = magpie_tools.Source(url, "0" * 64, "file", None)
        with self.assertRaises(magpie_tools.ToolError):
            magpie_tools.fetch(source, self.tmp / "cache")
        self.assertEqual(list((self.tmp / "cache").iterdir()), [])

    def test_manifest_covers_every_platform(self):
        for key in (("darwin", "arm64"), ("darwin", "x86_64"), ("win32", "x86_64"),
                    ("win32", "arm64"), ("linux", "x86_64"), ("linux", "arm64")):
            self.assertEqual(sorted(magpie_tools.manifest(key)), sorted(magpie_tools.TOOLS), key)
            for source in magpie_tools.manifest(key).values():
                self.assertEqual(len(source.sha256), 64)
        self.assertIn(magpie_tools.this_platform(), magpie_tools.MANIFEST)

    def test_missing_until_installed_from_the_pinned_source(self):
        key = magpie_tools.this_platform()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        self.assertEqual(sorted(magpie_tools.missing(bin_dir)), sorted(magpie_tools.TOOLS))
        record = {}
        for tool, path in magpie_tools.tool_paths(bin_dir).items():
            path.write_bytes(b"x")
            record[tool] = list(magpie_tools.manifest(key)[tool])
        (bin_dir / "installed.json").write_text(json.dumps(record))
        self.assertEqual(magpie_tools.missing(bin_dir), [])
        record["deno"][1] = "f" * 64            # installed from some other build
        (bin_dir / "installed.json").write_text(json.dumps(record))
        self.assertEqual(magpie_tools.missing(bin_dir), ["deno"])


class TokenStorageTests(TempDirTestCase):

    def setUp(self):
        super().setUp()
        self.saved = magpie_ibroadcast.TOKEN_PATH
        magpie_ibroadcast.TOKEN_PATH = self.tmp / "ibroadcast-token.json"

    def tearDown(self):
        magpie_ibroadcast.TOKEN_PATH = self.saved
        super().tearDown()

    def test_round_trip(self):
        token = {"access_token": "secret-access", "refresh_token": "secret-refresh"}
        magpie_ibroadcast.save_token(token)
        self.assertEqual(magpie_ibroadcast.load_token(), token)

    @unittest.skipIf(sys.platform == "win32", "permission bits do not protect files on Windows")
    def test_owner_only_permissions(self):
        magpie_ibroadcast.save_token({"access_token": "a"})
        self.assertEqual(stat.S_IMODE(magpie_ibroadcast.TOKEN_PATH.stat().st_mode), 0o600)

    @unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows-only")
    def test_windows_file_holds_no_plain_secret(self):
        magpie_ibroadcast.save_token({"access_token": "secret-access"})
        stored = json.loads(magpie_ibroadcast.TOKEN_PATH.read_text())
        self.assertEqual(list(stored), ["dpapi"])
        self.assertNotEqual(stored["dpapi"], "")
        self.assertEqual(magpie_ibroadcast.load_token(), {"access_token": "secret-access"})


if __name__ == "__main__":
    unittest.main()
