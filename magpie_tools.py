#!/usr/bin/env python3
"""The executables Magpie ships with: yt-dlp, ffmpeg, ffprobe and deno.

Every platform gets pinned releases, each checked against its published
SHA-256 before anything is unpacked, installed into Magpie's own bin folder
(magpie_paths.DIRS.bin). Nothing is taken from PATH, so Homebrew, apt or
winget installs never decide which version runs.

    yt-dlp   2026.10.26   github.com/yt-dlp/yt-dlp
    deno     2.9.8        github.com/denoland/deno    (yt-dlp's JavaScript runtime
                                                       for YouTube)
    ffmpeg   macOS: 6.0   github.com/eugeneware/ffmpeg-static b6.1.1
                          (osxexperts builds; arm64 is native)
             others:      github.com/yt-dlp/FFmpeg-Builds autobuild-2026-11-15-21-54
                          (static GPL builds; include libmp3lame)

Usage:
    magpie_tools.py install      install anything missing or out of date
    magpie_tools.py check        run each installed tool and show its version
    magpie_tools.py verify-all   download every platform's files and check them

Standard library only.
"""

import gzip
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections import namedtuple
from pathlib import Path

import magpie_paths

TOOLS = ("yt-dlp", "ffmpeg", "ffprobe", "deno")

# url: where to download; sha256: of the downloaded file; kind: "file" (the
# download is the executable), "gzip" (a gzipped executable), "zip" or
# "tar.xz"; member: the path inside a zip or tar.xz to install.
Source = namedtuple("Source", "url sha256 kind member")


def _release(repo, tag, asset):
    return f"https://github.com/{repo}/releases/download/{tag}/{asset}"


def _yt_dlp(asset, sha256):
    return Source(_release("yt-dlp/yt-dlp", "2026.10.26", asset), sha256, "file", None)


def _deno(asset, sha256, member="deno"):
    return Source(_release("denoland/deno", "v2.9.8", asset), sha256, "zip", member)


def _mac_ffmpeg(asset, sha256):
    return Source(_release("eugeneware/ffmpeg-static", "b6.1.1", asset), sha256, "gzip", None)


FFMPEG_BUILD = "ffmpeg-N-128946-g33a5956d8b"


def _ffmpeg_build(target, kind, sha256, tool, windows):
    folder = f"{FFMPEG_BUILD}-{target}-gpl"
    archive = folder + (".zip" if kind == "zip" else ".tar.xz")
    exe = tool + (".exe" if windows else "")
    return Source(_release("yt-dlp/FFmpeg-Builds", "autobuild-2026-11-15-21-54", archive),
                  sha256, kind, f"{folder}/bin/{exe}")


WIN64_ZIP = "914042a7d9bdeea33fb13636eeba511ad219d450f01aaa85309f42f13533cdb3"
WINARM64_ZIP = "e568b2def3a66096cd3d618bfedb6097e38351a5b5af6b6fda91a1e39379c491"
LINUX64_TAR = "7e968070359a895c856a95e9fe79d3c78f3b1093778ce276c8e21661bc1e170e"
LINUXARM64_TAR = "3d6fd3906a050cfffea18d551ad28396835326f67ff9aeccf734ce253659c591"

MANIFEST = {
    ("darwin", "arm64"): {
        "yt-dlp": _yt_dlp("yt-dlp_macos", "0f922c15ccc0b83eefa1c8bc9104e434318c4bf16e408e1ee93b7745a6efe0aa"),
        "ffmpeg": _mac_ffmpeg("ffmpeg-darwin-arm64.gz", "8923876afa8db5585022d7860ec7e589af192f441c56793971276d450ed3bbfa"),
        "ffprobe": _mac_ffmpeg("ffprobe-darwin-arm64.gz", "d986a8ec7b030899fe66a8a288ed809a3543338705a3ce178cfb85869c5d80be"),
        "deno": _deno("deno-aarch64-apple-darwin.zip", "9f0bf660393da2eaff044cdc7444cb08c73f235849b61d224d825a194ce7c98e"),
    },
    ("darwin", "x86_64"): {
        "yt-dlp": _yt_dlp("yt-dlp_macos", "0f922c15ccc0b83eefa1c8bc9104e434318c4bf16e408e1ee93b7745a6efe0aa"),
        "ffmpeg": _mac_ffmpeg("ffmpeg-darwin-x64.gz", "929b375c1182d956c51f7ac25e0b2b0411fb01f6f407aa15c9758efeb4242106"),
        "ffprobe": _mac_ffmpeg("ffprobe-darwin-x64.gz", "d4da574d6e2e197bd259b47d69cf262df9e312af24ad960444f6d806d3d4c186"),
        "deno": _deno("deno-x86_64-apple-darwin.zip", "4b2f5edd2639b7470f034238b321d7002d8dfb4726d8558f52c17afac370eb2c"),
    },
    ("win32", "x86_64"): {
        "yt-dlp": _yt_dlp("yt-dlp.exe", "f5389d3698ecde4ef06f4e6b1ce0294fe9ac1396228c8990b8d3416d6d177a7d"),
        "ffmpeg": _ffmpeg_build("win64", "zip", WIN64_ZIP, "ffmpeg", True),
        "ffprobe": _ffmpeg_build("win64", "zip", WIN64_ZIP, "ffprobe", True),
        "deno": _deno("deno-x86_64-pc-windows-msvc.zip", "cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf", "deno.exe"),
    },
    ("win32", "arm64"): {
        "yt-dlp": _yt_dlp("yt-dlp_arm64.exe", "0066dbac3c124028e2dcae2a197fccee33cd91f456313d24f7af89fd54590e98"),
        "ffmpeg": _ffmpeg_build("winarm64", "zip", WINARM64_ZIP, "ffmpeg", True),
        "ffprobe": _ffmpeg_build("winarm64", "zip", WINARM64_ZIP, "ffprobe", True),
        "deno": _deno("deno-aarch64-pc-windows-msvc.zip", "b413b92b735b50c198e8fe3b85af1aa50e29d5f72e229aab701d132286e14cef", "deno.exe"),
    },
    ("linux", "x86_64"): {
        "yt-dlp": _yt_dlp("yt-dlp_linux", "767ba9dce7052aa196f8b12652f433d76de128ba168966fa84b6394d5e6c6795"),
        "ffmpeg": _ffmpeg_build("linux64", "tar.xz", LINUX64_TAR, "ffmpeg", False),
        "ffprobe": _ffmpeg_build("linux64", "tar.xz", LINUX64_TAR, "ffprobe", False),
        "deno": _deno("deno-x86_64-unknown-linux-gnu.zip", "e890975bc2549807bbcc0b584ffafda72cc7c93349dd99c80483dd784f6c4609"),
    },
    ("linux", "arm64"): {
        "yt-dlp": _yt_dlp("yt-dlp_linux_aarch64", "e87e9533e89fe5889abaa21f5114dfe7756d6aa6219b83551add04d48b18e483"),
        "ffmpeg": _ffmpeg_build("linuxarm64", "tar.xz", LINUXARM64_TAR, "ffmpeg", False),
        "ffprobe": _ffmpeg_build("linuxarm64", "tar.xz", LINUXARM64_TAR, "ffprobe", False),
        "deno": _deno("deno-aarch64-unknown-linux-gnu.zip", "d49596c20b9788cf8f5d8ddf644103d4bde56ed239753f0bdb92ba9361d51905"),
    },
}

# platform.machine() reports the same processor differently per OS.
MACHINES = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}

DOWNLOAD_TIMEOUT = 600


class ToolError(Exception):
    """A tool could not be installed or does not run."""


def this_platform():
    os_name = sys.platform if sys.platform in ("darwin", "win32") else "linux"
    return os_name, MACHINES.get(platform.machine().lower(), platform.machine().lower())


def manifest(key=None):
    key = key or this_platform()
    if key not in MANIFEST:
        raise ToolError(f"Magpie has no bundled tools for {key[0]} on {key[1]}.")
    return MANIFEST[key]


def exe_name(tool, os_name=None):
    return tool + (".exe" if (os_name or this_platform()[0]) == "win32" else "")


def tool_paths(bin_dir=None):
    """{tool: path} of the installed executables, whether or not they exist yet."""
    bin_dir = Path(bin_dir or magpie_paths.DIRS.bin)
    return {tool: bin_dir / exe_name(tool) for tool in TOOLS}


# ---------------------------------------------------------------------------
# Download, verify, unpack
# ---------------------------------------------------------------------------

def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(source, cache_dir, log=lambda m: None):
    """The downloaded file for source, from the cache when its checksum still
    matches. Raises ToolError when the download's checksum is wrong."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / source.sha256
    if target.is_file() and _sha256(target) == source.sha256:
        return target
    log(f"downloading {source.url}")
    part = target.with_suffix(".part")
    with urllib.request.urlopen(source.url, timeout=DOWNLOAD_TIMEOUT) as resp, open(part, "wb") as out:
        shutil.copyfileobj(resp, out, 1 << 20)
    got = _sha256(part)
    if got != source.sha256:
        part.unlink()
        raise ToolError(f"{source.url} has SHA-256 {got}, expected {source.sha256}; not installed.")
    os.replace(part, target)
    return target


def unpack(source, archive, dest):
    """Write the executable from the downloaded archive to dest."""
    tmp = Path(str(dest) + ".tmp")
    if source.kind == "gzip":
        with gzip.open(archive, "rb") as src, open(tmp, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
    elif source.kind == "file":
        shutil.copyfile(archive, tmp)
    elif source.kind == "zip":
        with zipfile.ZipFile(archive) as z, z.open(source.member) as src, open(tmp, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
    elif source.kind == "tar.xz":
        with tarfile.open(archive, "r:xz") as t:
            src = t.extractfile(source.member)
            if src is None:
                raise ToolError(f"{source.member} in {source.url} is not a file.")
            with src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
    else:
        raise ToolError(f"Unknown archive kind {source.kind!r} for {source.url}.")
    if sys.platform != "win32":
        tmp.chmod(0o755)
    os.replace(tmp, dest)


def _record_path(bin_dir):
    return Path(bin_dir) / "installed.json"


def _installed(bin_dir):
    try:
        return json.loads(_record_path(bin_dir).read_text())
    except (OSError, ValueError):
        return {}


def missing(bin_dir=None, key=None):
    """Tools that are absent, or installed from a different pinned source."""
    bin_dir = Path(bin_dir or magpie_paths.DIRS.bin)
    record = _installed(bin_dir)
    wanted = manifest(key)
    return [tool for tool, path in tool_paths(bin_dir).items()
            if not path.is_file() or record.get(tool) != list(wanted[tool])]


def install(bin_dir=None, cache_dir=None, log=lambda m: None, progress=lambda tool, n, total: None):
    """Install every missing or outdated tool. Returns {tool: path}."""
    bin_dir = Path(bin_dir or magpie_paths.DIRS.bin)
    cache_dir = Path(cache_dir or magpie_paths.DIRS.cache / "downloads")
    bin_dir.mkdir(parents=True, exist_ok=True)
    wanted = manifest()
    todo = missing(bin_dir)
    record = _installed(bin_dir)
    for n, tool in enumerate(todo, 1):
        progress(tool, n, len(todo))
        source = wanted[tool]
        archive = fetch(source, cache_dir, log)
        unpack(source, archive, tool_paths(bin_dir)[tool])
        record[tool] = list(source)
        _record_path(bin_dir).write_text(json.dumps(record, indent=1))
        log(f"installed {tool} from {source.url}")
    return tool_paths(bin_dir)


# ---------------------------------------------------------------------------
# Checking that the installed tools run
# ---------------------------------------------------------------------------

VERSION_ARGS = {"yt-dlp": ["--version"], "ffmpeg": ["-version"],
                "ffprobe": ["-version"], "deno": ["--version"]}


def run_quiet(argv, timeout=60):
    kwargs = {"capture_output": True, "text": True, "encoding": "utf-8",
              "errors": "replace", "timeout": timeout}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(argv, **kwargs)


def check(bin_dir=None):
    """{tool: first line of its version output}. Raises ToolError if any tool
    is missing or fails to run, or if ffmpeg cannot encode MP3."""
    paths = tool_paths(bin_dir)
    versions = {}
    for tool, path in paths.items():
        if not path.is_file():
            raise ToolError(f"{tool} is not installed at {path}.")
        result = run_quiet([str(path), *VERSION_ARGS[tool]])
        if result.returncode != 0:
            raise ToolError(f"{tool} at {path} failed to run: {result.stderr.strip()[:300]}")
        versions[tool] = (result.stdout.strip().splitlines() or [""])[0]
    # MP3 output depends on libmp3lame being compiled in; encoding a tenth of a
    # second of silence proves it rather than trusting the build's label.
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "silence.mp3")
        result = run_quiet([str(paths["ffmpeg"]), "-v", "error", "-f", "lavfi", "-i",
                            "anullsrc=r=44100:cl=stereo", "-t", "0.1", "-c:a", "libmp3lame", out])
        if result.returncode != 0 or not os.path.getsize(out):
            raise ToolError(f"ffmpeg cannot encode MP3: {result.stderr.strip()[:300]}")
    return versions


def verify_all(cache_dir, log=print):
    """Download every platform's files, check each checksum, and confirm each
    archive member exists. Proves the manifest without needing every OS."""
    seen = {}
    for key, tools in sorted(MANIFEST.items()):
        for tool, source in tools.items():
            if source.url not in seen:
                seen[source.url] = fetch(source, cache_dir, log)
            archive = seen[source.url]
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / exe_name(tool, key[0])
                unpack(source, archive, dest)
                log(f"ok  {key[0]}/{key[1]}  {tool}  {dest.stat().st_size} bytes")


def main(argv):
    command = argv[1] if len(argv) > 1 else "check"
    try:
        if command == "install":
            paths = install(log=print, progress=lambda t, n, total: print(f"[{n}/{total}] {t}"))
            print(f"installed in {Path(paths['yt-dlp']).parent}")
            command = "check"
        if command == "check":
            for tool, version in check().items():
                print(f"{tool:8} {version}")
            print("ffmpeg   encodes MP3: yes")
            return 0
        if command == "verify-all":
            verify_all(magpie_paths.DIRS.cache / "downloads")
            return 0
    except ToolError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
