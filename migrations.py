"""In-place filesystem migrations that converge an older on-disk layout to the
current one, run once on startup.

Pure standard library (``os``, ``shutil``) and **no tkinter**, so every
migration is unit-testable on a headless box. That matters: these functions
*move user files*, and a bug here is destructive — it must be coverable by
tests. ``ribbonengine.py`` calls them at import time with its resolved paths.

Design rules (every migration must follow them)
-----------------------------------------------
* **Backwards compatible.** New layout is canonical going forward, but the app
  must still work on the *old* layout — so the readers keep a fallback to the
  legacy location and a migration is never a precondition for the app working.
* **Non-destructive.** Never delete data; never overwrite an existing
  destination (so we can't clobber a file the user put there).
* **Best-effort.** Permission/lock errors are logged and swallowed, so a
  read-only install still launches on the old layout instead of crashing.
* **Idempotent.** Running twice is a no-op; running on a fresh install is a
  no-op.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Callable

LogFn = Callable[[str], None]

# Subfolder that holds gorget art going forward. Historically gorgets lived in
# ``commendations/`` and were identified by name; that still works as a read
# fallback, but new art belongs here.
GORGETS_SUBDIR = "gorgets"
COMMENDATIONS_SUBDIR = "commendations"
SHIRT_TEMPLATES_SUBDIR = "shirttemplates"
LEGACY_SHIRT_TEMPLATE = "shirttemplate.png"
# Folders under assets/ that are not faction trees and must be left alone.
NON_FACTION_DIRS: tuple[str, ...] = ("Characters",)


def _default_log(message: str) -> None:
    print(message, file=sys.stderr)


def is_gorget_name(name: str) -> bool:
    """Single source of truth for "is this PNG a gorget?".

    Both the relocation migration and the asset scanner's legacy fallback call
    this, so the new ``gorgets/`` folder and the old name-based detection always
    agree on what counts as a gorget.
    """
    return "gorget" in (name or "").lower()


def relocate_file(src: str, dst: str, *, label: str = "", log: LogFn = _default_log) -> bool:
    """Move ``src`` → ``dst`` if src is a file and dst doesn't already exist.

    Returns ``True`` only when a move actually happened. Never overwrites an
    existing destination and never raises — a perms/lock failure is logged and
    reported as ``False`` so the caller (and the app) carry on with the old
    layout intact.
    """
    if not os.path.isfile(src) or os.path.exists(dst):
        return False
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        return True
    except Exception as exc:  # perms / lock / partial move — stay functional
        log(f"[migration] Could not move {label or os.path.basename(src)!r} ({exc}); left in place.")
        return False


def resolve_characters_dir(base_dir: str, assets_root: str, *, log: LogFn = _default_log) -> str:
    """Return the nametape letter-tile folder, migrating the legacy layout.

    ``Characters/`` used to live at the project root; it now lives at
    ``assets/Characters/``. Fresh installs ship it in the new spot, but an
    in-place update keeps the old tree (the self-updater excludes ``assets/``
    and the top-level ``Characters/`` from its copy, so it never reshapes them).
    On first launch of the new build we move it ourselves.

    Best-effort: if the move fails (read-only install / lock) we return the
    legacy location so the nametape still renders this session. If neither
    exists we return the canonical new path so the integrity check flags it.
    """
    new_dir = os.path.join(assets_root, "Characters")
    legacy_dir = os.path.join(base_dir, "Characters")
    if os.path.isdir(new_dir):
        return new_dir
    if os.path.isdir(legacy_dir):
        try:
            os.makedirs(assets_root, exist_ok=True)
            shutil.move(legacy_dir, new_dir)
            return new_dir
        except Exception as exc:  # perms / lock — keep working on the old layout
            log(
                f"[migration] Could not move {legacy_dir!r} into assets/ "
                f"({exc}); using the legacy location for this session."
            )
            return legacy_dir
    return new_dir


def relocate_gorgets(faction_dir: str, *, log: LogFn = _default_log) -> int:
    """Move gorget-named PNGs out of ``commendations/`` into ``gorgets/``.

    Uses :func:`is_gorget_name` so it relocates exactly what the scanner would
    otherwise classify as a gorget. Returns the number of files moved. Anything
    that doesn't move (perms, name clash at destination) stays in
    ``commendations/`` and is still picked up by the scanner's fallback.
    """
    comm_dir = os.path.join(faction_dir, COMMENDATIONS_SUBDIR)
    gorget_dir = os.path.join(faction_dir, GORGETS_SUBDIR)
    if not os.path.isdir(comm_dir):
        return 0
    try:
        names = sorted(os.listdir(comm_dir))
    except OSError:
        return 0
    label_prefix = os.path.basename(faction_dir.rstrip(os.sep))
    moved = 0
    for fn in names:
        if fn.lower().endswith(".png") and is_gorget_name(fn):
            if relocate_file(
                os.path.join(comm_dir, fn),
                os.path.join(gorget_dir, fn),
                label=f"{label_prefix}/{fn}",
                log=log,
            ):
                moved += 1
    return moved


def migrate_faction_asset_layout(
    assets_root: str,
    subdirs: tuple[str, ...],
    *,
    skip_names: tuple[str, ...] = NON_FACTION_DIRS,
    log: LogFn = _default_log,
) -> None:
    """Normalize every faction's asset folder on startup, behind the scenes.

    For each faction directory under ``assets_root`` (skipping ``skip_names``):

      * create any missing subfolder in ``subdirs`` (so e.g. ``gorgets/`` and
        ``shirttemplates/`` exist even on installs that predate them),
      * move a legacy top-level ``shirttemplate.png`` into ``shirttemplates/``,
      * relocate gorget art from ``commendations/`` into ``gorgets/``.

    Every step is best-effort and the readers fall back to the old locations, so
    an install where nothing migrates still works.
    """
    if not os.path.isdir(assets_root):
        return
    try:
        entries = sorted(os.listdir(assets_root))
    except OSError:
        return
    for entry in entries:
        faction_dir = os.path.join(assets_root, entry)
        if entry in skip_names or not os.path.isdir(faction_dir):
            continue
        for sub in subdirs:
            try:
                os.makedirs(os.path.join(faction_dir, sub), exist_ok=True)
            except OSError as exc:
                log(f"[migration] Could not create {entry}/{sub} ({exc}).")
        relocate_file(
            os.path.join(faction_dir, LEGACY_SHIRT_TEMPLATE),
            os.path.join(faction_dir, SHIRT_TEMPLATES_SUBDIR, LEGACY_SHIRT_TEMPLATE),
            label=f"{entry}/{LEGACY_SHIRT_TEMPLATE}",
            log=log,
        )
        relocate_gorgets(faction_dir, log=log)
