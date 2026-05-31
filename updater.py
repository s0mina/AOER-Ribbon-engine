"""Self-update support: check GitHub Releases for a newer build and apply it.

Pure standard library (urllib, json, zipfile, tempfile, shutil, subprocess) and
**no tkinter**, so the version/asset/script logic is unit-testable on a headless
box. ribbonengine.py drives the UI side: check on startup or from the Help menu,
prompt the user, then download + stage + apply.

Why this is careful about *what* it overwrites
----------------------------------------------
The published ``AOER-Ribbon-engine-windows.zip`` bundles the program (the
PyInstaller ``.exe`` + its ``_internal`` runtime) **next to user data** — the
``factions``, ``loadouts``, ``Engine Profiles``, ``Characters`` folders and
``settings.json``. Blindly copying the whole archive over an existing install
would clobber everything the user customized. So :func:`apply_update_windows`
copies the program files and docs but **excludes the data folders/files** — an
update refreshes the engine without touching the user's content.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional

import urllib.parse
import urllib.request

GITHUB_REPO = "s0mina/AOER-Ribbon-engine"
# Releases name the Windows zip with the version baked in so users can tell
# builds apart at a glance, e.g. ``AOER-Ribbon-engine-windowsv1.4.zip``. The
# updater therefore matches on prefix+suffix rather than an exact filename;
# ASSET_NAME is the legacy un-versioned name still accepted for old releases.
ASSET_PREFIX = "AOER-Ribbon-engine-windows"
ASSET_SUFFIX = ".zip"
ASSET_NAME = f"{ASSET_PREFIX}{ASSET_SUFFIX}"
USER_AGENT = "AOER-Ribbon-engine-Updater"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"

# User-owned content that an update must never overwrite. Matched at the top
# level of the extracted archive only (full paths), so a same-named folder
# nested inside the PyInstaller _internal runtime is still updated normally.
DATA_DIRS: tuple[str, ...] = (
    "assets",
    "factions",
    "Characters",
    "templates",
    "Engine Profiles",
    "loadouts",
    "out",
    "ribbonoutput",
)
DATA_FILES: tuple[str, ...] = ("settings.json",)

# Belt-and-braces cap so a hostile/oversized asset can't fill the disk.
MAX_DOWNLOAD_BYTES = 400 * 1024 * 1024


@dataclass(frozen=True)
class UpdateInfo:
    """Result of an update check; ``available`` is the only field the UI gates on."""

    available: bool
    current_version: str
    latest_version: str
    tag: str = ""
    notes: str = ""
    html_url: str = RELEASES_PAGE
    asset_url: str = ""


def parse_version(tag) -> tuple[int, ...]:
    """Turn a tag like ``v1.2`` or ``1.1.1`` into ``(1, 2)`` / ``(1, 1, 1)``.

    Leading ``v`` is dropped; each dot-separated chunk contributes its leading
    run of digits (so ``1.2-beta`` → ``(1, 2)``). Parsing stops at the first
    chunk with no leading digit, which keeps a malformed tag from crashing a
    background update check.
    """
    if not tag:
        return ()
    text = str(tag).strip().lstrip("vV")
    parts: list[int] = []
    for chunk in text.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(latest, current) -> bool:
    """True if ``latest`` is a strictly higher version than ``current``."""
    a, b = parse_version(latest), parse_version(current)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return a > b


def fetch_latest_release(repo: str = GITHUB_REPO, timeout: int = 15) -> dict:
    """GET the repo's latest (non-prerelease, non-draft) release as a dict."""
    req = urllib.request.Request(
        API_LATEST.format(repo=repo),
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def asset_download_url(release: dict, asset_name: str = ASSET_NAME) -> str:
    """Pull the Windows zip's download URL out of a release payload.

    Accepts the versioned asset name (``AOER-Ribbon-engine-windowsv1.4.zip``) by
    matching the ``ASSET_PREFIX``/``ASSET_SUFFIX`` pattern, and still honors the
    legacy un-versioned ``AOER-Ribbon-engine-windows.zip`` exactly so older
    releases keep working.
    """
    assets = release.get("assets", []) or []
    # Exact legacy name wins if present (un-versioned older releases).
    for asset in assets:
        if asset.get("name") == asset_name:
            return asset.get("browser_download_url", "") or ""
    # Otherwise take the first versioned Windows zip.
    for asset in assets:
        name = asset.get("name", "") or ""
        if name.startswith(ASSET_PREFIX) and name.endswith(ASSET_SUFFIX):
            return asset.get("browser_download_url", "") or ""
    return ""


def check_for_update(
    current_version: str,
    repo: str = GITHUB_REPO,
    fetch: Callable[[str], dict] = fetch_latest_release,
) -> UpdateInfo:
    """Compare the installed version against the latest release.

    ``fetch`` is injectable so tests can supply a canned release dict without
    hitting the network.
    """
    release = fetch(repo)
    tag = release.get("tag_name") or release.get("name") or ""
    return UpdateInfo(
        available=bool(tag) and is_newer(tag, current_version),
        current_version=str(current_version),
        latest_version=str(tag).lstrip("vV"),
        tag=str(tag),
        notes=release.get("body") or "",
        html_url=release.get("html_url") or RELEASES_PAGE,
        asset_url=asset_download_url(release),
    )


def download_asset(
    url: str,
    dest_dir: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    timeout: int = 30,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> str:
    """Stream the release zip to ``dest_dir``; returns the saved path.

    ``progress_cb(read, total)`` is called as bytes arrive (``total`` is 0 when
    the server omits Content-Length).
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    # Save under the asset's real (versioned) filename when the URL exposes it,
    # falling back to the legacy name. Only used as a temp file before staging.
    filename = os.path.basename(urllib.parse.urlsplit(url).path) or ASSET_NAME
    out_path = os.path.join(dest_dir, filename)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        read = 0
        with open(out_path, "wb") as fh:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                read += len(chunk)
                if read > max_bytes:
                    raise RuntimeError("Update download exceeded the size cap; aborting.")
                fh.write(chunk)
                if progress_cb:
                    progress_cb(read, total)
    return out_path


def stage_update(zip_path: str, staging_dir: str) -> str:
    """Extract the downloaded zip into ``staging_dir`` with a zip-slip guard."""
    root = os.path.realpath(staging_dir)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            target = os.path.realpath(os.path.join(staging_dir, member))
            if target != root and not target.startswith(root + os.sep):
                raise RuntimeError(f"Unsafe path in update archive: {member!r}")
        zf.extractall(staging_dir)
    return staging_dir


def build_update_script(
    staged_dir: str,
    install_dir: str,
    relaunch_exe: str,
    pid: int,
    data_dirs: tuple[str, ...] = DATA_DIRS,
    data_files: tuple[str, ...] = DATA_FILES,
) -> str:
    """Return the .bat that waits for the app to exit, copies, and relaunches.

    Excludes (``/XD`` / ``/XF``) the *top-level* data dirs/files by full staged
    path, so user content survives. robocopy exit codes 0–7 are success; 8+ is a
    real failure, so the script reports it and pauses instead of relaunching a
    half-copied install.

    The script runs in a **visible** console and echoes its progress while also
    appending to ``update_log.txt`` in the install dir — so the minute-or-so the
    copy takes looks like work in progress, not a frozen blank desktop, and a
    failure leaves a diagnosable trail.
    """
    xd = " ".join(f'"{os.path.join(staged_dir, d)}"' for d in data_dirs)
    xf = " ".join(f'"{os.path.join(staged_dir, f)}"' for f in data_files)
    log = os.path.join(install_dir, "update_log.txt")
    return (
        "@echo off\r\n"
        "setlocal enableextensions\r\n"
        'title Updating AOER Ribbon Engine\r\n'
        f'set "LOG={log}"\r\n'
        'echo ============================================================\r\n'
        "echo  Updating AOER Ribbon Engine\r\n"
        "echo  Please wait - this takes a minute. It will relaunch itself.\r\n"
        "echo  Do NOT close this window.\r\n"
        'echo ============================================================\r\n'
        '>"%LOG%" echo [update] started %DATE% %TIME%\r\n'
        "echo Waiting for the app to close...\r\n"
        '>>"%LOG%" echo [update] waiting for pid ' f"{pid} to exit\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul\r\n'
        "if not errorlevel 1 (\r\n"
        "  timeout /t 1 /nobreak >nul\r\n"
        "  goto wait\r\n"
        ")\r\n"
        "echo Copying new files...\r\n"
        '>>"%LOG%" echo [update] copying files\r\n'
        f'robocopy "{staged_dir}" "{install_dir}" /E /IS /IT /R:3 /W:2 '
        f'/NFL /NDL /NJH /NJS /NP /XD {xd} /XF {xf} >>"%LOG%" 2>&1\r\n'
        "set RC=%ERRORLEVEL%\r\n"
        '>>"%LOG%" echo [update] robocopy exit code %RC%\r\n'
        "if %RC% GEQ 8 (\r\n"
        "  echo.\r\n"
        "  echo Update FAILED (robocopy code %RC%). See update_log.txt.\r\n"
        '  >>"%LOG%" echo [update] FAILED - aborting relaunch\r\n'
        "  pause\r\n"
        "  exit /b %RC%\r\n"
        ")\r\n"
        "echo Done. Relaunching...\r\n"
        '>>"%LOG%" echo [update] relaunching\r\n'
        f'start "" "{relaunch_exe}"\r\n'
        f'rmdir /S /Q "{staged_dir}" >nul 2>&1\r\n'
        '(goto) 2>nul & del "%~f0"\r\n'
    )


# Windows process-creation flags. The helper must survive our exit (its own
# process group) AND get its own VISIBLE console window: CREATE_NEW_CONSOLE means
# the minute-long copy shows progress instead of a blank desktop, which users
# read as "nothing happened". (We deliberately do NOT use CREATE_NO_WINDOW.)
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NEW_CONSOLE = 0x00000010


def apply_update_windows(
    staged_dir: str,
    install_dir: str,
    relaunch_exe: str,
    pid: Optional[int] = None,
    data_dirs: tuple[str, ...] = DATA_DIRS,
    data_files: tuple[str, ...] = DATA_FILES,
    spawn: bool = True,
) -> str:
    """Write the update .bat and (by default) spawn it detached.

    The caller must exit promptly afterward — the script blocks until this
    process's PID disappears before it can overwrite the locked ``.exe``.
    Returns the script path (handy for tests, which pass ``spawn=False``).
    """
    if pid is None:
        pid = os.getpid()
    script = build_update_script(staged_dir, install_dir, relaunch_exe, pid, data_dirs, data_files)
    fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="aoer_update_")
    with os.fdopen(fd, "w", newline="") as fh:
        fh.write(script)
    if spawn:
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=_CREATE_NEW_PROCESS_GROUP | _CREATE_NEW_CONSOLE,
            close_fds=True,
        )
    return bat_path
