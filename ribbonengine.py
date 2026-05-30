from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
import urllib.request
import webbrowser
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from typing import Callable, Optional

from PIL import Image, ImageGrab, ImageTk, PngImagePlugin

from factions import (
    Faction,
    FactionRecolorCache,
    FactionRegistry,
    RecolorOptions,
    load_faction_registry,
)
from profiles import LayoutProfile
from renderer import RibbonRenderer
import updater


# Image/layout constants
imageSize = 128
ribbonAreaWidth = 43
maxMedalsPerSide = 3
defaultNameplateWidth = 31
nameplateLetterSpacing = 1
hoverPreviewSize = 96
expandedIcon = "\u25be"
collapsedIcon = "\u25b8"

# -------------------------------
# Layout values are profile-driven; these are safe bootstrap defaults until a profile is loaded.
partCoordsKeys = ("corpus", "nametape", "sacks", "commendations", "ribbons", "gorget", "spbadge")
# Canonical defaults for the stock 128x128 layout. Used by the Profile Editor's
# "Reset to defaults" button and by the `New…` profile template.
DEFAULT_PART_COORDS: dict[str, tuple[int, int]] = {
    "corpus": (8, 16),
    "nametape": (13, 31),
    "sacks": (14, 62),
    "commendations": (8, 25),
    "ribbons": (80, 33),
    "gorget": (43, 0),
    "spbadge": (90, 59),
}
partCoords = {key: (0, 0) for key in partCoordsKeys}
pocketColSpacing = 0
pocketRightOffset = 0
pocketXOffset = 0
corpusXOffset = 0
ribbonsRightAlignOffset = 0

# The de-globalized view of the active profile's render-relevant layout. Built
# by applyProfile() alongside the legacy globals above; the renderer reads this
# object instead of the scattered globals (see renderer.py / Stage 2).
currentLayout: LayoutProfile = LayoutProfile()

# Medal name lists (filenames without .png)
awardMedalNames = {
    "Diamond Medal",
    "Galaxy Medal",
    "Quantum Medal",
}
bonusMedalNames = {
    "Teto Medal",
    "Teto Medal Shiny",
    "ANROSOC Medal",
}

# -------------------------------
# Paths and manifests
# When frozen by PyInstaller, __file__ points inside the temporary unpack dir,
# which is wrong for reading the asset tree and writing output. Use the folder
# the .exe actually lives in so the data folders sit right next to it.
if getattr(sys, "frozen", False):
    baseDir = os.path.dirname(sys.executable)
else:
    baseDir = os.path.dirname(os.path.abspath(__file__))
# Per-faction asset tree: assets/<FACTION_KEY>/{ribbons,awards,commendations}/*.png.
# The filesystem IS the allowlist — a faction can only render PNGs physically
# present under its directory. This is out-of-band protection: a recipient who
# was never shipped a ribbon file cannot render it, regardless of code edits.
assetsRoot = os.path.join(baseDir, "assets")
ribbonOutputDir = os.path.join(baseDir, "ribbonoutput")
ASSET_SUBDIRS: tuple[str, ...] = ("ribbons", "awards", "commendations")
# Characters Windows forbids in filenames. We flag these in the asset
# validator and strip them from generated output filenames so the engine
# stays portable when most recipients are on Windows.
WINDOWS_ILLEGAL_CHARS: frozenset[str] = frozenset('<>:"/\\|?*')


def scanAssetTree() -> tuple[list[str], dict[str, list[str]]]:
    """Walk assets/ and return (illegal_files, duplicates_by_hash).

    `illegal_files`: list of `<faction>/<sub>/<file>` entries whose filename
    contains a Windows-forbidden character.

    `duplicates_by_hash`: { sha256_hex: [relpath, …] } for any file whose
    exact bytes appear in two or more places (e.g., the same ribbon copied
    into two faction trees — likely to drift).
    """
    illegal: list[str] = []
    by_hash: dict[str, list[str]] = {}
    if not os.path.isdir(assetsRoot):
        return illegal, {}
    for factionKey in os.listdir(assetsRoot):
        facDir = os.path.join(assetsRoot, factionKey)
        if not os.path.isdir(facDir):
            continue
        for sub in ASSET_SUBDIRS:
            subDir = os.path.join(facDir, sub)
            if not os.path.isdir(subDir):
                continue
            for name in os.listdir(subDir):
                if not name.lower().endswith(".png"):
                    continue
                rel = f"{factionKey}/{sub}/{name}"
                bad = WINDOWS_ILLEGAL_CHARS.intersection(name)
                if bad:
                    illegal.append(f"{rel} (illegal char(s): {''.join(sorted(bad))})")
                try:
                    with open(os.path.join(subDir, name), "rb") as handle:
                        digest = hashlib.sha256(handle.read()).hexdigest()
                except OSError:
                    continue
                by_hash.setdefault(digest, []).append(rel)
    duplicates = {h: paths for h, paths in by_hash.items() if len(paths) > 1}
    return illegal, duplicates
charactersDir = os.path.join(baseDir, "Characters")
settingsPath = os.path.join(baseDir, "settings.json")
profilesDir = os.path.join(baseDir, "Engine Profiles")
legacyProfilePath = os.path.join(baseDir, "engine_profile.json")
defaultProfileName = "default"
factionsConfigPath = os.path.join(baseDir, "factions")
if not os.path.isdir(factionsConfigPath):
    factionsConfigPath = os.path.join(baseDir, "factions.json")

# Named loadouts directory — each *.json is a saved snapshot of {ribbons,
# nameplate, faction, custom_offsets}. Lets multiple users on one install keep
# their own ribbon sets without overwriting each other's last-session state.
loadoutsDir = os.path.join(baseDir, "loadouts")


# Current schema version baked into exported PNGs / loadout JSON. Bump when
# the format changes incompatibly so importers can detect old payloads and
# migrate (or warn). v1 = original {ribbons, nameplate, faction,
# custom_offsets}. v2 adds awards/bonuses/department_badge/manual_slots and
# the profile key.
METADATA_SCHEMA_VERSION = 2

# The app's own release version, compared against the latest GitHub Release tag
# by the self-updater. Keep this in lock-step with the pushed ``vX.Y`` tag.
APP_VERSION = "1.4.1"


class _Tooltip:
    """Tiny hover-tooltip helper.

    Attach to any Tk widget with `_Tooltip(widget, lambda: "text")`. The
    callback is re-evaluated each time the tooltip shows, so dynamic state
    (e.g. the current effective color) stays fresh.
    """

    def __init__(self, widget, text_provider, delay_ms: int = 400):
        self.widget = widget
        self.provide = text_provider if callable(text_provider) else (lambda t=text_provider: t)
        self.delay_ms = delay_ms
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        text = ""
        try:
            text = str(self.provide() or "")
        except Exception:
            return
        if not text:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tip, text=text, justify="left",
            background="#2a2a2a", foreground="#eaeaea",
            relief="solid", borderwidth=1, padx=6, pady=3,
            font=("TkDefaultFont", 8),
        ).pack()

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def _atomicWriteBytes(path: str, data: bytes) -> None:
    """Write `data` to `path` atomically.

    Writes to `path + ".tmp"` first, then os.replace's it into place — so a
    crash or power loss mid-write leaves the previous file intact rather
    than producing a half-written corrupt file.
    """
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _atomicWriteText(path: str, text: str) -> None:
    _atomicWriteBytes(path, text.encode("utf-8"))


def _atomicSaveImage(image, path: str, **save_kwargs) -> None:
    """PIL Image.save via tmp + os.replace so crashes don't corrupt the file.

    PIL normally infers the format from the file extension, but the tmp file
    ends in `.tmp` — so pass the format explicitly, derived from the real
    target extension.
    """
    tmp = path + ".tmp"
    save_kwargs.setdefault("format", os.path.splitext(path)[1].lstrip(".").upper() or "PNG")
    image.save(tmp, **save_kwargs)
    os.replace(tmp, path)

# Faction state — populated at startup, mutated when the user switches factions.
_factionRegistry: Optional[FactionRegistry] = None
_factionRecolorCache: Optional[FactionRecolorCache] = None
_activeFactionKey: str = "AOER"

# Active recolor options. Defaults: border-only (the AOER policy). Toggleable
# via the Settings dialog; persisted to settings.json under `recolor_*` keys.
_recolorOptions: RecolorOptions = RecolorOptions(border=True, stripe=False, base=False)


def getRecolorOptions() -> RecolorOptions:
    return _recolorOptions


def setRecolorOptions(options: RecolorOptions) -> None:
    """Replace the active recolor options and invalidate the cache."""
    global _recolorOptions
    _recolorOptions = options
    if _factionRecolorCache is not None:
        _factionRecolorCache.clear()


def recolorOptionsFromSettings(settings: dict) -> RecolorOptions:
    return RecolorOptions(
        border=bool(settings.get("recolor_border", True)),
        stripe=bool(settings.get("recolor_stripe", False)),
        base=bool(settings.get("recolor_base", False)),
    )


def getFactionRegistry() -> FactionRegistry:
    global _factionRegistry, _factionRecolorCache, _activeFactionKey
    if _factionRegistry is None:
        _factionRegistry = load_faction_registry(factionsConfigPath)
        _factionRecolorCache = FactionRecolorCache(_factionRegistry)
        _activeFactionKey = _factionRegistry.default_key
    return _factionRegistry


def setActiveFaction(key: str) -> Faction:
    global _activeFactionKey
    registry = getFactionRegistry()
    faction = registry.get(key)
    _activeFactionKey = faction.key
    return faction


def getActiveFaction() -> Faction:
    return getFactionRegistry().get(_activeFactionKey)


def getRecolorCache() -> FactionRecolorCache:
    getFactionRegistry()
    assert _factionRecolorCache is not None
    return _factionRecolorCache

characterAliases = {" ": "Space", ".": "Period"}
previewOverlayPath = os.path.join(charactersDir, "anro_hr_formals_template.png")
# Drop additional *_template.png files here to make them selectable in the
# "Shirt preview" picker. The legacy ANRO template inside Characters/ is also
# always included so the default install keeps working.
templatesDir = os.path.join(baseDir, "templates")

categoryLabels = {
    "sacks": "Awards",
    "gorget": "Gorgets",
    "spbadge": "Special Badges",
    "commendations": "Commendations",
    "corpus": "Corpus Commendations",
    "ribbons": "Ribbons",
}

# Profile-driven behavior defaults
enabledCategories = set(categoryLabels.keys())
allowedAssetsByCategory: dict[str, set[str]] = {}
certificationKeyword = "certification"
certificationsSectionLabel = "Certifications"
anrocomSectionLabel = "ANROCOM Ribbons"
anrocomSettingsKey = "ANROCOM"
ribbonCenteredRowCapacity = 4
ribbonRightStartRow = 5
ribbonRightFirstRowCapacity = 3
ribbonRightSubsequentRowCapacity = 2
medalSingleOrder = ("middle", "left", "right")
medalMultiOrder = ("left", "middle", "right")
overlayTemplateSize = (585, 559)
overlayFrontCropBox = (132, 74, 260, 202)
profileSelectedShirt = ""

defaultProfile = {
    "image_size": imageSize,
    "ribbon_area_width": ribbonAreaWidth,
    "max_medals_per_side": maxMedalsPerSide,
    "default_nameplate_width": defaultNameplateWidth,
    "nameplate_letter_spacing": nameplateLetterSpacing,
    "hover_preview_size": hoverPreviewSize,
    "part_coords": {key: list(DEFAULT_PART_COORDS[key]) for key in partCoordsKeys},
    "offsets": {
        "pocket_col_spacing": pocketColSpacing,
        "pocket_right_offset": pocketRightOffset,
        "pocket_x_offset": pocketXOffset,
        "corpus_x_offset": corpusXOffset,
        "ribbons_right_align_offset": ribbonsRightAlignOffset,
    },
    "medals": {
        "award_names": sorted(awardMedalNames),
        "bonus_names": sorted(bonusMedalNames),
        "single_order": list(medalSingleOrder),
        "multi_order": list(medalMultiOrder),
    },
    "ribbon_rows": {
        "centered_row_capacity": ribbonCenteredRowCapacity,
        "right_start_row": ribbonRightStartRow,
        "first_right_row_capacity": ribbonRightFirstRowCapacity,
        "subsequent_right_row_capacity": ribbonRightSubsequentRowCapacity,
    },
    # Per-slot (x, y) nudges added to each medal's auto-computed pocket
    # position. award_* applies to the award row (under the ribbons); bonus_*
    # applies to the bonus row (under the nametape). 0 = no change (auto layout).
    "medal_slot_offsets": {
        "award_1_x": 0,
        "award_1_y": 0,
        "award_2_x": 0,
        "award_2_y": 0,
        "award_3_x": 0,
        "award_3_y": 0,
        "bonus_1_x": 0,
        "bonus_1_y": 0,
        "bonus_2_x": 0,
        "bonus_2_y": 0,
        "bonus_3_x": 0,
        "bonus_3_y": 0,
        # Center-to-center spacing per row, in px. 0 = auto (medal width + 1).
        "award_spacing": 0,
        "bonus_spacing": 0,
    },
    "character_aliases": deepcopy(characterAliases),
    "ui": {
        "expanded_icon": expandedIcon,
        "collapsed_icon": collapsedIcon,
        "certification_keyword": certificationKeyword,
        "certifications_label": certificationsSectionLabel,
        "anrocom_label": anrocomSectionLabel,
        "anrocom_settings_key": anrocomSettingsKey,
    },
    "categories": {
        "labels": deepcopy(categoryLabels),
        "enabled": sorted(categoryLabels.keys()),
        "allowed_assets": {},
    },
    "preview_overlay": {
        "template_size": list(overlayTemplateSize),
        "front_crop_box": list(overlayFrontCropBox),
    },
    "selected_shirt": "",
}


@dataclass(frozen=True)
class AssetItem:
    name: str
    path: str


@dataclass
class SectionUI:
    key: str
    header: ttk.Frame
    toggle: Optional[ttk.Button]
    content: ttk.Frame
    items: list[dict]
    collapsed: bool = False


try:
    import tkinterdnd2  # type: ignore
    _TKDND_AVAILABLE = True
except Exception:
    tkinterdnd2 = None  # type: ignore
    _TKDND_AVAILABLE = False


def _createRoot() -> tk.Tk:
    """Return a Tk root that supports OS file drops if tkinterdnd2 is installed."""
    if _TKDND_AVAILABLE:
        try:
            return tkinterdnd2.TkinterDnD.Tk()
        except Exception:
            pass
    return tk.Tk()


def _askString(parent, title: str, prompt: str, initial: str = "") -> Optional[str]:
    """Modal text prompt. Returns None on cancel."""
    return simpledialog.askstring(title, prompt, initialvalue=initial, parent=parent)


def _linuxClipboardImage() -> Optional[Image.Image]:
    """Best-effort clipboard image read on Linux. Tries wl-paste then xclip."""
    import io
    import shutil
    import subprocess

    candidates = []
    if shutil.which("wl-paste"):
        candidates.append(["wl-paste", "--type", "image/png"])
    if shutil.which("xclip"):
        candidates.append(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"])

    for cmd in candidates:
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=2)
        except Exception:
            continue
        if result.returncode == 0 and result.stdout:
            try:
                return Image.open(io.BytesIO(result.stdout))
            except Exception:
                continue
    return None


def listPngs(directory: str) -> list[AssetItem]:
    """Return every `*.png` in `directory` as `AssetItem`s, sorted by name.

    Missing directories return `[]` rather than raising — callers walk many
    optional per-faction subdirs and silent absence is the right default
    there. (Use `os.path.isdir` first if you need the strict check.)
    """
    if not os.path.isdir(directory):
        return []

    items: list[AssetItem] = []
    for filename in sorted(os.listdir(directory), key=str.lower):
        if not filename.lower().endswith(".png"):
            continue
        name = os.path.splitext(filename)[0]
        items.append(AssetItem(name=name, path=os.path.join(directory, filename)))
    return items


def _factionAssetDir(factionKey: str, sub: str) -> str:
    """Path to `assets/<factionKey>/<sub>/` (no existence check)."""
    return os.path.join(assetsRoot, factionKey, sub)


def _contributingFactionKeys(registry: Optional[FactionRegistry], active: str) -> list[str]:
    """Faction keys whose asset dirs contribute to the active sidebar.

    Active faction first, then every *hidden* faction (corp-wide pool, e.g.
    AOER). Hidden factions act as the shared distribution: drop a PNG into
    `assets/AOER/ribbons/` to expose it under every faction.
    """
    if registry is None:
        return [active]
    keys = [active]
    for k, f in registry.factions.items():
        if f.hidden and k != active:
            keys.append(k)
    return keys


def loadRibbonGroups() -> dict[str, list[AssetItem]]:
    """Scan `assets/<faction>/{ribbons,awards,commendations}/` and bucket by category.

    The on-disk file set defines the allowlist — there is no JSON-side
    enumeration. Adding `assets/SORO/ribbons/NewThing.png` makes it
    appear in SORO's sidebar on the next reload, no code/JSON edits.

    Resolution order:
      1. Active faction's own dirs.
      2. Every hidden faction's dirs (e.g. `assets/AOER/`) as the
         corp-wide pool, visible regardless of who's active.
    Earlier sources win on name collision so a faction can locally
    override a corp-wide ribbon (e.g. ship a recolored variant).
    """
    try:
        registry = getFactionRegistry()
    except (FileNotFoundError, ValueError):
        registry = None

    contributingKeys = _contributingFactionKeys(registry, _activeFactionKey)

    def collect(sub: str) -> list[AssetItem]:
        """Return assets ordered as: hidden (AOER, global) A→Z, then active A→Z.

        Local-override semantics still hold — if the active faction ships a
        same-named file, the active one wins and shows up in the *active*
        group, not the hidden group. `listPngs` already sorts each source
        A→Z, so we just stitch the buckets together in the desired order.
        """
        active_items: list[AssetItem] = []
        active_names: set[str] = set()
        if contributingKeys:
            for item in listPngs(_factionAssetDir(contributingKeys[0], sub)):
                if item.name in active_names:
                    continue
                active_names.add(item.name)
                active_items.append(item)

        hidden_items: list[AssetItem] = []
        hidden_names: set[str] = set()
        for key in contributingKeys[1:]:
            for item in listPngs(_factionAssetDir(key, sub)):
                if item.name in active_names or item.name in hidden_names:
                    continue
                hidden_names.add(item.name)
                hidden_items.append(item)
        # Each bucket is already A→Z from `listPngs`; sort again defensively
        # in case multiple hidden factions contribute (concat would interleave).
        hidden_items.sort(key=lambda it: it.name.lower())
        active_items.sort(key=lambda it: it.name.lower())
        return hidden_items + active_items

    groups: dict[str, list[AssetItem]] = {
        "sacks": collect("awards"),
        "gorget": [],
        "spbadge": [],
        "commendations": [],
        "corpus": [],
        "ribbons": collect("ribbons"),
    }
    # The commendations dir holds gorgets, corpus commendations, special
    # badges, and plain commendations — classify by filename pattern.
    for item in collect("commendations"):
        lowerName = item.name.lower()
        if "gorget" in lowerName:
            groups["gorget"].append(item)
        elif lowerName.startswith(("mr ", "hr ", "anrocom ")):
            groups["corpus"].append(item)
        elif "badge" in lowerName:
            groups["spbadge"].append(item)
        else:
            groups["commendations"].append(item)

    for category in list(groups.keys()):
        if category not in enabledCategories:
            groups[category] = []
            continue
        allowedAssets = allowedAssetsByCategory.get(category)
        if allowedAssets:
            groups[category] = [item for item in groups[category] if item.name in allowedAssets]
    return groups


def _readRibbonSidecar(ribbonPath: str) -> dict:
    """Load `<ribbon>.meta.json` sidecar if present.

    Sidecar schema (all keys optional):
      {
        "no_recolor": true,          # skip recolor entirely
        "recolor": {                 # per-region toggle override
          "border": bool,
          "stripe": bool,
          "base":   bool
        },
        "colors": {                  # per-ribbon palette override (hex)
          "border": "#ffffff",
          "stripe": "#ff6b00",
          "base":   "#001a4d"
        }
      }
    Missing file or invalid JSON → `{}` (defaults apply).
    """
    sidecar = ribbonPath + ".meta.json"
    if not os.path.exists(sidecar):
        return {}
    try:
        with open(sidecar, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def loadRibbonImage(item: AssetItem, factionKey: Optional[str] = None) -> Image.Image:
    if not item.path:
        raise FileNotFoundError("Missing ribbon image path.")
    if not os.path.exists(item.path):
        raise FileNotFoundError(f"Missing ribbon image: {item.path}")
    with Image.open(item.path) as img:
        source = img.convert("RGBA")

    try:
        registry = getFactionRegistry()
    except FileNotFoundError:
        return source

    # Only ribbons participate in recoloring. Medals, commendations, gorgets
    # etc. always render in their original art. With the per-faction asset
    # tree, "is a ribbon" means the file lives in any `.../ribbons/` subdir
    # under `assets/`.
    #
    # Cross-platform notes:
    #   - `realpath` + `normpath` collapses symlinks and `..` so the
    #     `startswith` test compares like-for-like on every OS.
    #   - `normcase` lowercases on Windows (case-insensitive paths) and is
    #     a no-op on POSIX, so the check works for both case-sensitive Linux
    #     and case-insensitive Windows/macOS-HFS+.
    #   - `relpath` can raise `ValueError` on Windows when the source and
    #     anchor are on different drives — treat that as "not a ribbon."
    try:
        normalizedPath = os.path.normcase(os.path.normpath(os.path.realpath(item.path)))
        assetsRootNorm = os.path.normcase(os.path.normpath(os.path.realpath(assetsRoot)))
    except OSError:
        return source
    isRibbon = False
    if normalizedPath.startswith(assetsRootNorm + os.sep):
        try:
            rel = os.path.relpath(normalizedPath, assetsRootNorm)
        except ValueError:
            return source
        parts = rel.split(os.sep)
        # Expect <FACTION>/<sub>/<file.png>; sub == "ribbons" → recolor.
        # normcase already lowercased on Windows, so compare against the
        # lowercase subdir name regardless of OS.
        if len(parts) >= 3 and parts[1].lower() == "ribbons":
            isRibbon = True
    if not isRibbon:
        return source

    # Honor per-faction `no_recolor` opt-outs — bespoke art that should never
    # be tinted regardless of the active palette.
    if registry.is_no_recolor(item.name):
        return source

    # Read the per-ribbon sidecar BEFORE consulting the global options, so a
    # sidecar with custom colors / toggles can re-enable recolor even when
    # the user has every region turned off in Settings.
    sidecar = _readRibbonSidecar(item.path)
    if sidecar.get("no_recolor") is True:
        return source
    options = getRecolorOptions()
    sidecarRecolor = sidecar.get("recolor")
    if isinstance(sidecarRecolor, dict):
        from factions import RecolorOptions
        options = RecolorOptions(
            border=bool(sidecarRecolor.get("border", options.border)),
            stripe=bool(sidecarRecolor.get("stripe", options.stripe)),
            base=bool(sidecarRecolor.get("base", options.base)),
        )
    sidecarColors = sidecar.get("colors") if isinstance(sidecar.get("colors"), dict) else None
    if options.is_passthrough and not sidecarColors:
        return source

    # Pick which palette to apply:
    #   - corp-wide / shared assets use the owning hidden faction's palette
    #     (so e.g. AOER's locked border color is enforced)
    #   - everything else uses the active faction's palette
    if registry.is_shared_asset(item.name):
        paletteFaction = registry.hidden_faction_for(item.name)
        if paletteFaction is None:
            return source
        paletteKey = paletteFaction.key
    else:
        paletteKey = factionKey or _activeFactionKey
        if paletteKey == registry.default_key and not sidecarColors:
            # Default faction is the passthrough sentinel — no recolor.
            # Per-ribbon color overrides still apply on the default faction.
            return source

    if sidecarColors:
        return _renderWithCustomColors(item.path, paletteKey, source, options, sidecarColors, registry)
    return getRecolorCache().get(item.path, paletteKey, source, options)


_customRecolorCache: dict[str, Image.Image] = {}


def _renderWithCustomColors(asset_path, paletteKey, source, options, sidecarColors, registry):
    """Apply a per-ribbon palette override from the sidecar's `colors` block.

    Falls back to the active faction's palette for any channel the sidecar
    leaves out (or supplies invalid hex for). Caches per (path, palette key,
    options token, override token) so repeated paints stay fast.
    """
    from factions import Faction, FactionPalette, _hex_to_rgb, recolor_ribbon

    basePalette = (
        registry.factions[paletteKey].palette
        if paletteKey in registry.factions
        else FactionPalette(base_color=(0, 0, 0), stripe_color=(136, 136, 136), border_color=(255, 255, 255))
    )

    def _parse(name: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
        v = sidecarColors.get(name)
        if isinstance(v, str) and v.strip():
            try:
                return _hex_to_rgb(v.strip())
            except Exception:
                return fallback
        return fallback

    override = FactionPalette(
        base_color=_parse("base", basePalette.base_color),
        stripe_color=_parse("stripe", basePalette.stripe_color),
        border_color=_parse("border", basePalette.border_color),
    )
    token = f"{override.border_color}-{override.stripe_color}-{override.base_color}"
    key = f"{asset_path}::{paletteKey}::{options.cache_token()}::{token}"
    cached = _customRecolorCache.get(key)
    if cached is not None:
        return cached.copy()

    customFaction = Faction(
        key=f"{paletteKey}__custom",
        display_name="",
        ribbon_groups=(),
        palette=override,
    )
    rendered = recolor_ribbon(source, customFaction, options, registry.border_thickness)
    _customRecolorCache[key] = rendered
    return rendered.copy()


def renderRibbonWithColors(item, factionKey: Optional[str], colors: dict) -> Image.Image:
    """Render a single ribbon with explicit per-call color overrides.

    Used for per-slot recoloring in the manual placement grid (where two
    instances of the same ribbon can show in different palettes). Bypasses
    the sidecar entirely and forces every region with a supplied color on.
    """
    from factions import RecolorOptions
    if not os.path.exists(item.path):
        raise FileNotFoundError(item.path)
    with Image.open(item.path) as img:
        source = img.convert("RGBA")
    registry = getFactionRegistry()
    paletteKey = factionKey or _activeFactionKey
    if registry.is_shared_asset(item.name):
        owner = registry.hidden_faction_for(item.name)
        if owner is not None:
            paletteKey = owner.key
    options = RecolorOptions(
        border=bool(colors.get("border")),
        stripe=bool(colors.get("stripe")),
        base=bool(colors.get("base")),
    )
    if options.is_passthrough:
        return source.copy()
    return _renderWithCustomColors(item.path, paletteKey, source, options, colors, registry)


def invalidateRibbonCache(asset_path: str) -> None:
    """Drop every cached render for one ribbon, both faction-palette and custom-palette."""
    try:
        cache = getRecolorCache()._cache
        for k in [k for k in cache if k.startswith(asset_path + "::")]:
            cache.pop(k, None)
    except Exception:
        pass
    for k in [k for k in _customRecolorCache if k.startswith(asset_path + "::")]:
        _customRecolorCache.pop(k, None)


def writeRibbonSidecar(ribbonPath: str, data: dict) -> None:
    """Write `<ribbon>.meta.json`. Pass `{}` (or an effectively empty dict) to delete it."""
    sidecar = ribbonPath + ".meta.json"
    # Strip empty sub-dicts so the file stays clean.
    clean = {k: v for k, v in data.items() if not (isinstance(v, dict) and not v)}
    if not clean:
        try:
            if os.path.exists(sidecar):
                os.remove(sidecar)
        except OSError:
            pass
        return
    _atomicWriteText(sidecar, json.dumps(clean, indent=2))


def loadCharacterImage(ch: str) -> Image.Image:
    token = characterAliases.get(ch, ch)
    path = os.path.join(charactersDir, f"{token}.png")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing character image: {path}")
    with Image.open(path) as img:
        return img.convert("RGBA")


def loadSettings() -> dict:
    if not os.path.exists(settingsPath):
        return {}
    try:
        # Use utf-8-sig so settings authored by Windows editors with BOM still parse.
        with open(settingsPath, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def saveSettings(settings: dict) -> None:
    try:
        _atomicWriteText(settingsPath, json.dumps(settings, indent=2))
    except Exception:
        return


def ensureSettingsDefaults(settings: dict) -> dict:
    data = dict(settings) if isinstance(settings, dict) else {}
    data.setdefault("presets", {})
    data.setdefault("theme", "xp")
    data.setdefault("profile", defaultProfileName)
    data.setdefault("sections", {anrocomSettingsKey: []})
    if isinstance(data.get("sections"), dict):
        data["sections"].setdefault(anrocomSettingsKey, [])
    data.setdefault("collapsed_sections", {})
    data.setdefault("preview_scale", 1.0)
    data.setdefault("only_show_selected", False)
    data.setdefault("preview_overlay", False)
    # Self-updater: check Releases on startup (frozen builds only), and remember
    # a version the user chose to skip so we don't nag about it again.
    data.setdefault("check_updates_on_startup", True)
    data.setdefault("skip_update_version", "")
    return data


def _deepMerge(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deepMerge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalizeOrder(value, fallback: tuple[str, str, str]) -> tuple[str, str, str]:
    if not isinstance(value, list):
        return fallback
    tokens = []
    for token in value:
        if not isinstance(token, str):
            continue
        normalized = token.strip().lower()
        if normalized in ("left", "middle", "right") and normalized not in tokens:
            tokens.append(normalized)
    if len(tokens) != 3:
        return fallback
    return (tokens[0], tokens[1], tokens[2])


def _normalizeCategoryAssets(value) -> dict[str, set[str]]:
    parsed: dict[str, set[str]] = {}
    if not isinstance(value, dict):
        return parsed
    for category, names in value.items():
        if not isinstance(category, str) or not isinstance(names, list):
            continue
        cleaned = set()
        for name in names:
            if not isinstance(name, str):
                continue
            normalized = name.strip()
            if normalized.lower().endswith(".png"):
                normalized = normalized[:-4]
            if normalized:
                cleaned.add(normalized)
        if cleaned:
            parsed[category] = cleaned
    return parsed


def _normalizeSizePair(value, fallback: tuple[int, int]) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        return fallback
    try:
        width = int(value[0])
        height = int(value[1])
    except Exception:
        return fallback
    if width <= 0 or height <= 0:
        return fallback
    return (width, height)


def _normalizeCropBox(value, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not isinstance(value, list) or len(value) != 4:
        return fallback
    try:
        x1, y1, x2, y2 = (int(value[0]), int(value[1]), int(value[2]), int(value[3]))
    except Exception:
        return fallback
    if x2 <= x1 or y2 <= y1:
        return fallback
    return (x1, y1, x2, y2)


def _normalizeProfileName(name: Optional[str]) -> str:
    raw = str(name or "").strip()
    if raw.lower().endswith(".json"):
        raw = raw[:-5]
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_", " ")).strip()
    if not cleaned:
        return defaultProfileName
    return cleaned.replace(" ", "_")


def normalizeProfileName(name: Optional[str]) -> str:
    return _normalizeProfileName(name)


def _profilePathForName(profileName: Optional[str]) -> str:
    normalized = _normalizeProfileName(profileName)
    return os.path.join(profilesDir, f"{normalized}.json")


def listProfileNames() -> list[str]:
    if not os.path.isdir(profilesDir):
        return [defaultProfileName]
    names = []
    for filename in sorted(os.listdir(profilesDir), key=str.lower):
        if not filename.lower().endswith(".json"):
            continue
        names.append(os.path.splitext(filename)[0])
    if not names:
        return [defaultProfileName]
    return names


def _loadProfileFromPath(path: str) -> dict:
    if not os.path.exists(path):
        return deepcopy(defaultProfile)
    try:
        # Use utf-8-sig so profile files with BOM do not silently fall back to defaults.
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return deepcopy(defaultProfile)
        return _deepMerge(defaultProfile, data)
    except Exception:
        return deepcopy(defaultProfile)


def loadProfile(profileName: Optional[str] = None) -> dict:
    if not os.path.isdir(profilesDir):
        os.makedirs(profilesDir, exist_ok=True)

    targetPath = _profilePathForName(profileName)
    if os.path.exists(targetPath):
        return _loadProfileFromPath(targetPath)

    if os.path.exists(legacyProfilePath):
        return _loadProfileFromPath(legacyProfilePath)
    return deepcopy(defaultProfile)


def saveProfile(profile: dict, profileName: Optional[str] = None) -> None:
    if not os.path.isdir(profilesDir):
        os.makedirs(profilesDir, exist_ok=True)
    targetPath = _profilePathForName(profileName)
    try:
        _atomicWriteText(targetPath, json.dumps(profile, indent=2))
    except Exception:
        return


def ensureProfileFile(profileName: Optional[str] = None) -> dict:
    if not os.path.isdir(profilesDir):
        os.makedirs(profilesDir, exist_ok=True)

    normalized = _normalizeProfileName(profileName)
    targetPath = _profilePathForName(normalized)
    if os.path.exists(targetPath):
        return loadProfile(normalized)

    if os.path.exists(legacyProfilePath):
        profile = _loadProfileFromPath(legacyProfilePath)
    else:
        profile = deepcopy(defaultProfile)

    saveProfile(profile, normalized)
    return profile


def applyProfile(profile: dict) -> None:
    global imageSize
    global ribbonAreaWidth
    global maxMedalsPerSide
    global defaultNameplateWidth
    global nameplateLetterSpacing
    global hoverPreviewSize
    global pocketColSpacing
    global pocketRightOffset
    global pocketXOffset
    global corpusXOffset
    global ribbonsRightAlignOffset
    global awardMedalNames
    global bonusMedalNames
    global characterAliases
    global partCoords
    global categoryLabels
    global enabledCategories
    global allowedAssetsByCategory
    global certificationKeyword
    global certificationsSectionLabel
    global anrocomSectionLabel
    global anrocomSettingsKey
    global ribbonCenteredRowCapacity
    global ribbonRightStartRow
    global ribbonRightFirstRowCapacity
    global ribbonRightSubsequentRowCapacity
    global medalSingleOrder
    global medalMultiOrder
    global expandedIcon
    global collapsedIcon
    global overlayTemplateSize
    global overlayFrontCropBox
    global profileSelectedShirt
    global currentLayout

    imageSize = max(1, _safeInt(profile.get("image_size", imageSize), imageSize))
    ribbonAreaWidth = max(1, _safeInt(profile.get("ribbon_area_width", ribbonAreaWidth), ribbonAreaWidth))
    maxMedalsPerSide = max(1, _safeInt(profile.get("max_medals_per_side", maxMedalsPerSide), maxMedalsPerSide))
    defaultNameplateWidth = max(1, _safeInt(profile.get("default_nameplate_width", defaultNameplateWidth), defaultNameplateWidth))
    nameplateLetterSpacing = max(0, _safeInt(profile.get("nameplate_letter_spacing", nameplateLetterSpacing), nameplateLetterSpacing))
    hoverPreviewSize = max(16, _safeInt(profile.get("hover_preview_size", hoverPreviewSize), hoverPreviewSize))

    offsets = profile.get("offsets", {})
    if isinstance(offsets, dict):
        pocketColSpacing = _safeInt(offsets.get("pocket_col_spacing", pocketColSpacing), pocketColSpacing)
        pocketRightOffset = _safeInt(offsets.get("pocket_right_offset", pocketRightOffset), pocketRightOffset)
        pocketXOffset = _safeInt(offsets.get("pocket_x_offset", pocketXOffset), pocketXOffset)
        corpusXOffset = _safeInt(offsets.get("corpus_x_offset", corpusXOffset), corpusXOffset)
        ribbonsRightAlignOffset = _safeInt(offsets.get("ribbons_right_align_offset", ribbonsRightAlignOffset), ribbonsRightAlignOffset)

    coords = profile.get("part_coords", {})
    if isinstance(coords, dict):
        updated = dict(partCoords)
        for key, value in coords.items():
            if key not in updated:
                continue
            if isinstance(value, list) and len(value) == 2:
                try:
                    updated[key] = (int(value[0]), int(value[1]))
                except Exception:
                    continue
        partCoords = updated

    medals = profile.get("medals", {})
    if isinstance(medals, dict):
        awardNames = medals.get("award_names")
        bonusNames = medals.get("bonus_names")
        if isinstance(awardNames, list):
            awardMedalNames = {name.strip() for name in awardNames if isinstance(name, str) and name.strip()}
        if isinstance(bonusNames, list):
            bonusMedalNames = {name.strip() for name in bonusNames if isinstance(name, str) and name.strip()}
        medalSingleOrder = _normalizeOrder(medals.get("single_order"), medalSingleOrder)
        medalMultiOrder = _normalizeOrder(medals.get("multi_order"), medalMultiOrder)

    rowCfg = profile.get("ribbon_rows", {})
    if isinstance(rowCfg, dict):
        ribbonCenteredRowCapacity = max(1, _safeInt(rowCfg.get("centered_row_capacity", ribbonCenteredRowCapacity), ribbonCenteredRowCapacity))
        ribbonRightStartRow = max(1, _safeInt(rowCfg.get("right_start_row", ribbonRightStartRow), ribbonRightStartRow))
        ribbonRightFirstRowCapacity = max(1, _safeInt(rowCfg.get("first_right_row_capacity", ribbonRightFirstRowCapacity), ribbonRightFirstRowCapacity))
        ribbonRightSubsequentRowCapacity = max(1, _safeInt(rowCfg.get("subsequent_right_row_capacity", ribbonRightSubsequentRowCapacity), ribbonRightSubsequentRowCapacity))

    aliases = profile.get("character_aliases")
    if isinstance(aliases, dict):
        cleanedAliases = {}
        for k, v in aliases.items():
            if isinstance(k, str) and isinstance(v, str) and k:
                cleanedAliases[k] = v
        if cleanedAliases:
            characterAliases = cleanedAliases

    uiCfg = profile.get("ui", {})
    if isinstance(uiCfg, dict):
        expandedIcon = str(uiCfg.get("expanded_icon", expandedIcon))
        collapsedIcon = str(uiCfg.get("collapsed_icon", collapsedIcon))
        certificationKeyword = str(uiCfg.get("certification_keyword", certificationKeyword)).lower()
        certificationsSectionLabel = str(uiCfg.get("certifications_label", certificationsSectionLabel))
        anrocomSectionLabel = str(uiCfg.get("anrocom_label", anrocomSectionLabel))
        anrocomSettingsKey = str(uiCfg.get("anrocom_settings_key", anrocomSettingsKey))

    categoriesCfg = profile.get("categories", {})
    if isinstance(categoriesCfg, dict):
        labels = categoriesCfg.get("labels")
        if isinstance(labels, dict):
            for key, label in labels.items():
                if key in categoryLabels and isinstance(label, str) and label.strip():
                    categoryLabels[key] = label.strip()

        enabled = categoriesCfg.get("enabled")
        if isinstance(enabled, list):
            normalized = {key for key in enabled if isinstance(key, str) and key in categoryLabels}
            if normalized:
                enabledCategories = normalized

        allowedAssetsByCategory = _normalizeCategoryAssets(categoriesCfg.get("allowed_assets"))

    previewOverlayCfg = profile.get("preview_overlay", {})
    if isinstance(previewOverlayCfg, dict):
        overlayTemplateSize = _normalizeSizePair(
            previewOverlayCfg.get("template_size"),
            overlayTemplateSize,
        )
        overlayFrontCropBox = _normalizeCropBox(
            previewOverlayCfg.get("front_crop_box"),
            overlayFrontCropBox,
        )

    selectedShirt = profile.get("selected_shirt", "")
    if isinstance(selectedShirt, str):
        profileSelectedShirt = selectedShirt.strip()
    else:
        profileSelectedShirt = ""

    # De-globalized snapshot of the render-relevant layout. Parsed straight from
    # the same profile dict so it stays in lock-step with the legacy globals
    # above; the renderer consumes this instead of reaching for module globals.
    currentLayout = LayoutProfile.from_dict(profile)


def _hexToRgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def getThemePalette(name: str) -> dict[str, str]:
    if name == "dark":
        return {
            "bg": "#1e1e1e",
            "panel_bg": "#252526",
            "text": "#e6e6e6",
            "accent": "#7aa2f7",
            "header_bg": "#2d2d30",
            "header_fg": "#ffffff",
            "status": "#ff6b6b",
        }
    if name == "light":
        return {
            "bg": "#f5f5f5",
            "panel_bg": "#ffffff",
            "text": "#1f2933",
            "accent": "#2b6cb0",
            "header_bg": "#e6e6e6",
            "header_fg": "#111111",
            "status": "#b91c1c",
        }
    return {
        "bg": "#ece9d8",
        "panel_bg": "#ece9d8",
        "text": "#000000",
        "accent": "#0a246a",
        "header_bg": "#0a246a",
        "header_fg": "#ffffff",
        "status": "#a80000",
    }


def _normalizeSectionNames(values) -> set[str]:
    names: set[str] = set()
    if not isinstance(values, list):
        return names
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if cleaned.lower().endswith(".png"):
            cleaned = cleaned[:-4]
        if cleaned:
            names.add(cleaned)
    return names


def _safeFloat(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safeInt(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _defaultOutputFilename(nameplate: str) -> str:
    """Format: `[NAMETAPE]_[YYYY-MM-DD-HH-MM-SS].png`.

    Spaces inside the nametape become `_`; characters Windows forbids in
    filenames (`<>:"/\\|?*`) are stripped. The timestamp uses `-` between
    every component (including hours/minutes/seconds) because `:` is also
    a reserved character on Windows.
    """
    rawName = nameplate.strip()
    # Strip Windows-illegal chars and other non-printable junk; keep
    # alphanumerics, spaces, underscores, and hyphens.
    safeName = "".join(ch for ch in rawName if ch.isalnum() or ch in (" ", "_", "-")).strip()
    safeName = safeName.replace(" ", "_") or "ribbon"
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    return f"[{safeName}]_[{stamp}].png"



def makeRenderer(groups):
    """Construct a display-free RibbonRenderer wired to this module's loaders.

    Threads the active LayoutProfile (currentLayout, kept in lock-step with the
    profile by applyProfile) plus the asset-loading callables into renderer.py,
    which itself imports only PIL + profiles. Call sites recreate the renderer
    after every applyProfile (faction/profile switch), so currentLayout is fresh.
    """
    return RibbonRenderer(
        groups,
        currentLayout,
        load_ribbon_image=loadRibbonImage,
        render_ribbon_with_colors=renderRibbonWithColors,
        load_character_image=loadCharacterImage,
        characters_dir=charactersDir,
        award_medal_names=awardMedalNames,
        bonus_medal_names=bonusMedalNames,
    )


class RibbonEngineApp:
    def __init__(self):
        self.settingsData = ensureSettingsDefaults(loadSettings())
        self.profileName = _normalizeProfileName(self.settingsData.get("profile", defaultProfileName))
        self.profileData = ensureProfileFile(self.profileName)
        applyProfile(self.profileData)

        # Create the Tk root FIRST, before any messagebox/Variable. A parent-less
        # dialog (or a tk Variable) created earlier would auto-spawn a *second*,
        # stray default root, leaving tkinter._default_root pointing at a different
        # Tcl interpreter than our widgets. With tkinterdnd2's TkinterDnD.Tk()
        # (bundled in the frozen build) that split surfaces as the fatal
        # `image "pyimageN" doesn't exist` on the first preview. Building the real
        # root up front guarantees every Variable/PhotoImage binds to it.
        self.root = _createRoot()

        try:
            self.factionRegistry = getFactionRegistry()
        except (FileNotFoundError, ValueError) as exc:
            messagebox.showerror(
                "Faction config error",
                f"{exc}\nFalling back to AOER defaults.",
                parent=self.root,
            )
            self.factionRegistry = None

        # Recolor options are global (apply to every faction) and persisted
        # across restarts via the Settings dialog.
        setRecolorOptions(recolorOptionsFromSettings(self.settingsData))

        savedFaction = self.settingsData.get("faction")
        if self.factionRegistry is not None:
            selectableNames = set(self.factionRegistry.names())
            if savedFaction in selectableNames:
                initialFaction = savedFaction
            else:
                initialFaction = self.factionRegistry.selectable_default_key()
            setActiveFaction(initialFaction)
            self.activeFactionKey = initialFaction
        else:
            self.activeFactionKey = "AOER"

        self.themeName = self.settingsData.get("theme", "xp")
        if self.themeName not in ("xp", "dark", "light"):
            self.themeName = "xp"
        self.theme = getThemePalette(self.themeName)
        self.themeBgRgb = _hexToRgb(self.theme["bg"])

        self.baseImage: Optional[Image.Image] = None
        self.previewImg: Optional[ImageTk.PhotoImage] = None
        self.hoverPreviewImg: Optional[ImageTk.PhotoImage] = None
        self.previewJob: Optional[str] = None

        # Per-ribbon (dx, dy) offsets applied on top of the algorithmic position.
        # Populated by drag-to-place; persisted inside named loadouts.
        self.customOffsets: dict[str, tuple[int, int]] = {}
        self.lastPlacements: list[dict] = []
        self.currentLoadoutName: str = ""

        # Manual ribbon placement: when enabled, ribbons checkboxes are ignored
        # for layout and `manualRibbonSlots` (slot_index -> ribbon_name) drives
        # the renderer instead.
        self.manualRibbonMode: bool = bool(self.settingsData.get("manual_ribbon_mode", False))
        self.manualRibbonSlots: dict[int, str] = {}
        self._dragData: Optional[dict] = None
        self._dragHoverSlot: Optional[int] = None
        for k, v in (self.settingsData.get("manual_ribbon_slots") or {}).items():
            try:
                self.manualRibbonSlots[int(k)] = str(v)
            except (TypeError, ValueError):
                continue
        # Per-slot color overrides (slot_idx -> {"border": "#...", "stripe": "#...", "base": "#..."}).
        # Keyed by slot so duplicate placements of the same ribbon can be tinted independently.
        self.manualSlotColors: dict[int, dict[str, str]] = {}
        for k, v in (self.settingsData.get("manual_slot_colors") or {}).items():
            if not isinstance(v, dict):
                continue
            try:
                cleaned = {region: str(v[region]) for region in ("border", "stripe", "base") if isinstance(v.get(region), str)}
                if cleaned:
                    self.manualSlotColors[int(k)] = cleaned
            except (TypeError, ValueError):
                continue
        self._pendingPlaceRibbon: Optional[str] = None

        # Undo/redo history of (selected_names, nameplate, offsets) snapshots.
        # Capped at 50 entries each to keep memory bounded.
        self._historyPast: list[dict] = []
        self._historyFuture: list[dict] = []
        self._historyMax = 50
        # Suppresses history capture during programmatic state restores.
        self._suppressHistory = False

        # Drag-to-place runtime state — populated in mouse callbacks.
        self._dragState: Optional[dict] = None

        self.root.title("Ribbon Engine")
        self.root.geometry("700x500")
        self.root.option_add("*Font", ("Tahoma", 9))
        self.recentFiles: list[str] = [
            p for p in (self.settingsData.get("recent_files") or [])
            if isinstance(p, str) and os.path.exists(p)
        ][:8]
        self.loadoutFavorites: set[str] = {
            n for n in (self.settingsData.get("loadout_favorites") or [])
            if isinstance(n, str)
        }
        self.profileVar = tk.StringVar(master=self.root, value=self.profileName)
        self.factionVar = tk.StringVar(master=self.root, value=self.activeFactionKey)
        # Shirt-preview source priority on startup: the active faction's own
        # template (assets/<faction>/) first so the overlay matches the faction,
        # then a saved manual pick, then the profile shirt, then the ANRO default.
        factionShirt = self._factionShirtPath(self.activeFactionKey)
        savedOverlay = str(self.settingsData.get("overlay_template", "") or "").strip()
        if factionShirt:
            self.overlaySourcePath = factionShirt
        elif savedOverlay and os.path.exists(savedOverlay):
            self.overlaySourcePath = savedOverlay
        else:
            self.overlaySourcePath = self._resolveProfileShirtPath(profileSelectedShirt)
            if not self.overlaySourcePath and os.path.exists(previewOverlayPath):
                self.overlaySourcePath = previewOverlayPath

        self._configureStyle()

        try:
            self.ribbonGroups = loadRibbonGroups()
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            self.ribbonGroups = {key: [] for key in categoryLabels}

        self.renderer = makeRenderer(self.ribbonGroups)

        # Catch faction JSONs that reference PNGs which aren't on disk. This is
        # the most common distribution-time mistake — surface it loud, on launch,
        # before the recipient sees a silent empty slot.
        self._runAssetValidator()

        self.checkboxVars: dict[str, tk.IntVar] = {}
        self.categorySections: list[SectionUI] = []

        self._buildLayout()
        self._buildSections()
        self._loadCollapsedSettings()
        self.applyFilter()
        self.updatePreviewScaleLabel()
        self.updatePreview()
        self.clearHoverPreview()
        self._buildMenuBar()
        self._enableDragAndDrop()
        self._maybeAutoCheckUpdates()

    def _configureStyle(self) -> None:
        style = ttk.Style(self.root)
        # Dark/Light themes need "clam" because xpnative/vista ignore most color overrides.
        if self.themeName in ("dark", "light"):
            preferred = ("clam", "alt", "default")
        else:
            preferred = ("xpnative", "vista", "winnative", "clam")
        for systemTheme in preferred:
            if systemTheme in style.theme_names():
                style.theme_use(systemTheme)
                break

        bg = self.theme["bg"]
        panel = self.theme["panel_bg"]
        fg = self.theme["text"]
        accent = self.theme["accent"]
        # Slightly contrasting field background for inputs in dark mode.
        field_bg = "#2d2d30" if self.themeName == "dark" else panel
        select_bg = accent
        select_fg = "#ffffff"

        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure(
            "Section.TLabel",
            background=bg,
            foreground=accent,
            font=("Tahoma", 9, "bold"),
        )
        style.configure("Toggle.TButton", font=("Tahoma", 7, "bold"), padding=(0, 0))
        style.configure("TButton", padding=(10, 6), background=panel, foreground=fg)
        style.map("TButton", background=[("active", field_bg)], foreground=[("active", fg)])
        style.configure("TCheckbutton", background=bg, foreground=fg)
        style.map(
            "TCheckbutton",
            background=[("active", bg)],
            foreground=[("active", fg)],
            indicatorcolor=[("selected", accent), ("!selected", field_bg)],
        )
        style.configure("TRadiobutton", background=bg, foreground=fg)
        style.map(
            "TRadiobutton",
            background=[("active", bg)],
            foreground=[("active", fg)],
            indicatorcolor=[("selected", accent), ("!selected", field_bg)],
        )
        style.configure(
            "TEntry",
            padding=4,
            fieldbackground=field_bg,
            foreground=fg,
            insertcolor=fg,
        )
        style.configure(
            "TCombobox",
            fieldbackground=field_bg,
            background=panel,
            foreground=fg,
            selectbackground=select_bg,
            selectforeground=select_fg,
            arrowcolor=fg,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", field_bg)],
            foreground=[("readonly", fg)],
        )
        # Combobox popdown listbox (a non-ttk Listbox child) — recolor via option db.
        self.root.option_add("*TCombobox*Listbox.background", field_bg)
        self.root.option_add("*TCombobox*Listbox.foreground", fg)
        self.root.option_add("*TCombobox*Listbox.selectBackground", select_bg)
        self.root.option_add("*TCombobox*Listbox.selectForeground", select_fg)
        self.root.configure(background=bg)

    def _repaintTkWidgets(self) -> None:
        """Walk the widget tree and repaint raw tk.* widgets to match the active theme.

        ttk widgets follow style changes automatically; classic tk widgets (Frame, Label,
        Canvas, etc.) don't, so we recolor them by hand each time the theme switches.
        """
        bg = self.theme["bg"]
        fg = self.theme["text"]
        panel = self.theme["panel_bg"]
        header_bg = self.theme["header_bg"]
        header_fg = self.theme["header_fg"]
        status_fg = self.theme["status"]

        self.root.configure(background=bg)

        def walk(widget):
            cls = widget.winfo_class()
            try:
                if cls == "Frame":
                    parent_bg = widget.master.cget("background") if widget.master else bg
                    widget.configure(background=header_bg if parent_bg == header_bg else bg)
                elif cls == "Label":
                    parent_bg = widget.master.cget("background") if widget.master else bg
                    if parent_bg == header_bg:
                        widget.configure(background=header_bg, foreground=header_fg)
                    elif widget is getattr(self, "labelStatus", None):
                        widget.configure(background=panel, foreground=status_fg)
                    else:
                        widget.configure(background=bg, foreground=fg)
                elif cls == "Canvas":
                    widget.configure(background=bg, highlightthickness=0)
                elif cls == "Toplevel":
                    widget.configure(background=bg)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                walk(child)

        walk(self.root)
        # Right-side preview panel uses panel_bg specifically.
        if hasattr(self, "rightFrame"):
            try:
                self.rightFrame.configure(background=panel)
            except tk.TclError:
                pass

    def _buildLayout(self) -> None:
        headerFrame = tk.Frame(self.root, bg=self.theme["header_bg"])
        headerFrame.pack(fill="x")

        headerTitle = tk.Label(
            headerFrame,
            text="AOER Ribbon Engine",
            bg=self.theme["header_bg"],
            fg=self.theme["header_fg"],
            font=("Tahoma", 11, "bold"),
            padx=10,
            pady=6,
        )
        headerTitle.pack(side="left")

        mainFrame = tk.Frame(self.root, bg=self.theme["bg"])
        mainFrame.pack(fill="both", expand=True)

        self.leftFrame = tk.Frame(mainFrame, bg=self.theme["bg"])
        self.leftFrame.pack(side="left", fill="both", expand=True)

        self.rightFrame = tk.Frame(mainFrame, bg=self.theme["panel_bg"], bd=1, relief="sunken")
        self.rightFrame.pack(side="right", fill="y", padx=10, pady=10)

        self.canvas = tk.Canvas(self.leftFrame, background=self.theme["bg"], highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(self.leftFrame, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.scrollableFrame = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.scrollableFrame, anchor="nw")
        self.scrollableFrame.bind("<Configure>", self._onCanvasConfigure)

        self.root.bind_all("<MouseWheel>", self._onMousewheel)
        self.root.bind_all("<Button-4>", self._onMousewheel)
        self.root.bind_all("<Button-5>", self._onMousewheel)

        profileRow = ttk.Frame(self.scrollableFrame)
        profileRow.pack(fill="x", pady=(6, 4))
        ttk.Label(profileRow, text="Profile:").pack(side="left")
        self.profileCombo = ttk.Combobox(profileRow, textvariable=self.profileVar, state="readonly", width=24)
        self.profileCombo.pack(side="left", padx=(6, 6))
        self.profileCombo.bind("<<ComboboxSelected>>", self._onProfileChanged)
        ttk.Button(profileRow, text="Reload", width=8, command=self.reloadCurrentProfile).pack(side="left")
        ttk.Button(profileRow, text="New…", width=6, command=self._createNewProfile).pack(side="left", padx=(4, 0))
        self.refreshProfileChoices()

        factionRow = ttk.Frame(self.scrollableFrame)
        factionRow.pack(fill="x", pady=(2, 4))
        ttk.Label(factionRow, text="Faction:").pack(side="left")
        self.factionCombo = ttk.Combobox(factionRow, textvariable=self.factionVar, state="readonly", width=14)
        self.factionCombo.pack(side="left", padx=(6, 6))
        self.factionCombo.bind("<<ComboboxSelected>>", self._onFactionChanged)
        self.factionPaletteLabel = ttk.Label(factionRow, text="")
        self.factionPaletteLabel.pack(side="left")
        ttk.Button(factionRow, text="Settings…", width=10, command=self._openSettingsDialog).pack(side="right")
        ttk.Button(factionRow, text="Diff…", width=8, command=self._openDiffDialog).pack(side="right", padx=(0, 4))
        ttk.Button(factionRow, text="Export Loadout", width=14, command=self._exportLoadoutImage).pack(side="right", padx=(0, 4))
        ttk.Button(factionRow, text="Loadouts…", width=10, command=self._openLoadoutDialog).pack(side="right", padx=(0, 4))
        self._refreshFactionChoices()

        ttk.Label(self.scrollableFrame, text="Nametape:").pack(pady=5)
        self.entry = ttk.Entry(self.scrollableFrame)
        self.entry.pack(pady=5)
        self.entry.bind("<KeyRelease>", lambda _event: self.schedulePreview())

        ttk.Label(self.scrollableFrame, text="Search:").pack(pady=(10, 5))
        self.searchVar = tk.StringVar()
        self.searchEntry = ttk.Entry(self.scrollableFrame, textvariable=self.searchVar)
        self.searchEntry.pack(pady=(0, 10))
        self.searchEntry.bind("<KeyRelease>", lambda _event: self.applyFilter())

        self.showSelectedVar = tk.BooleanVar(value=bool(self.settingsData.get("only_show_selected", False)))
        ttk.Checkbutton(
            self.scrollableFrame,
            text="Only show selected",
            variable=self.showSelectedVar,
            command=self._onToggleShowSelected,
        ).pack(pady=(0, 10))

        self.overlayVar = tk.BooleanVar(value=bool(self.settingsData.get("preview_overlay", False)))
        ttk.Checkbutton(
            self.scrollableFrame,
            text="Show shirt preview",
            variable=self.overlayVar,
            command=self._onToggleOverlay,
        ).pack(pady=(0, 10))

        overlayRow = ttk.Frame(self.scrollableFrame)
        overlayRow.pack(fill="x", pady=(0, 8))
        ttk.Label(overlayRow, text="Template:").pack(side="left")
        self.overlayTemplateVar = tk.StringVar()
        self.overlayTemplateCombo = ttk.Combobox(
            overlayRow,
            textvariable=self.overlayTemplateVar,
            state="readonly",
            width=28,
        )
        self.overlayTemplateCombo.pack(side="left", padx=(6, 0), fill="x", expand=True)
        self.overlayTemplateCombo.bind("<<ComboboxSelected>>", self._onOverlayTemplateChanged)
        self.overlaySourceLabel = ttk.Label(self.scrollableFrame, text="")
        self.overlaySourceLabel.pack(anchor="w", pady=(0, 8))
        self._refreshOverlayTemplateChoices()
        self.updateOverlaySourceLabel()

        columnsFrame = ttk.Frame(self.scrollableFrame)
        columnsFrame.pack(fill="x", expand=True)

        self.leftColumn = ttk.Frame(columnsFrame)
        self.leftColumn.pack(side="left", fill="both", expand=True, padx=(0, 10))

        self.rightColumn = ttk.Frame(columnsFrame)
        self.rightColumn.pack(side="right", fill="both", expand=True)

        self._buildRibbonPlacementPanel(self.scrollableFrame)

        ttk.Button(self.scrollableFrame, text="Paste Image from Clipboard", command=self.pasteFromClipboard).pack(pady=5)
        ttk.Button(self.scrollableFrame, text="Clear All", command=self.clearAll).pack(pady=5)
        ttk.Button(self.scrollableFrame, text="Generate Image", command=self.generateImage).pack(pady=10)

        ttk.Label(
            self.scrollableFrame,
            text="Ribbon Engine v3 — Developed by Lolcraft101_owner",
            foreground=self.theme.get("status", "#888"),
        ).pack(pady=(8, 4))

        previewHeader = ttk.Frame(self.rightFrame)
        previewHeader.pack(fill="x", pady=(4, 0))
        ttk.Label(previewHeader, text="Preview", style="Section.TLabel").pack(side="left", padx=(8, 0))
        self.previewVisible = True
        self.togglePreviewBtn = ttk.Button(previewHeader, text="Hide", width=8, command=self._toggleBiggerPreview)
        self.togglePreviewBtn.pack(side="right", padx=(0, 8))
        self.labelPreview = ttk.Label(self.rightFrame)
        self.labelPreview.pack(pady=10)
        self.labelPreview.bind("<Button-1>", self._onPreviewPress)
        self.labelPreview.bind("<B1-Motion>", self._onPreviewDrag)
        self.labelPreview.bind("<ButtonRelease-1>", self._onPreviewRelease)
        self.labelPreview.bind("<Button-3>", self._onPreviewRightClick)
        self.root.bind_all("<Control-z>", self.undo)
        self.root.bind_all("<Control-y>", self.redo)
        self.root.bind_all("<Control-Shift-Z>", self.redo)
        self.root.bind_all("<Control-s>", lambda _e: (self.generateImage(), "break")[1])
        self.root.bind_all("<Control-e>", lambda _e: (self._exportLoadoutImage(), "break")[1])
        self.root.bind_all("<Control-Shift-C>", lambda _e: (self.copyImageToClipboard(), "break")[1])
        self.root.bind_all("<Control-d>", lambda _e: (self._openDiffDialog(), "break")[1])
        self.root.bind_all("<Control-Escape>", lambda _e: (self.clearAll(), "break")[1])

        self.labelStatus = tk.Label(
            self.rightFrame,
            text="",
            fg=self.theme["status"],
            bg=self.theme["panel_bg"],
            justify="left",
        )
        self.labelStatus.pack(pady=(0, 4))

        self.loadoutLabel = tk.Label(
            self.rightFrame,
            text="",
            fg=self.theme["status"],
            bg=self.theme["panel_bg"],
            justify="left",
        )
        self.loadoutLabel.pack(pady=(0, 10))
        self._updateLoadoutLabel()

        self.previewScale = _safeFloat(self.settingsData.get("preview_scale", 1.0), 1.0)
        if self.previewScale < 1.0:
            self.previewScale = 1.0

        self.previewSizeLabel = ttk.Label(self.rightFrame, text="Preview size: 1.0x")
        self.previewSizeLabel.pack()

        previewControls = ttk.Frame(self.rightFrame)
        previewControls.pack(pady=(5, 10))
        ttk.Button(previewControls, text="-", width=3, command=lambda: self.adjustPreviewScale(-0.5)).pack(side="left", padx=2)
        ttk.Button(previewControls, text="+", width=3, command=lambda: self.adjustPreviewScale(0.5)).pack(side="left", padx=2)

        ttk.Label(self.rightFrame, text="Hover Preview", style="Section.TLabel").pack(pady=(10, 0))
        self.hoverPreviewLabel = ttk.Label(self.rightFrame)
        self.hoverPreviewLabel.pack(pady=6)
        self.hoverNameLabel = ttk.Label(self.rightFrame, text="")
        self.hoverNameLabel.pack()
        self.hoverDescLabel = ttk.Label(self.rightFrame, text="", wraplength=240, justify="left")
        self.hoverDescLabel.pack()

        self.root.bind_all("<Control-f>", self.focusSearch)
        self.root.bind_all("<KeyPress-slash>", self.focusSearch)

    def _onCanvasConfigure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _onMousewheel(self, event) -> None:
        try:
            widget = self.root.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            # Tk raises KeyError('popdown') when the pointer is over an open
            # ttk.Combobox dropdown — its child widget path isn't in the tree.
            return
        parent = widget
        while parent is not None and parent is not self.root:
            if parent is self.leftFrame:
                break
            parent = parent.master
        else:
            return

        if getattr(event, "delta", 0):
            delta = -1 if event.delta > 0 else 1
            if abs(event.delta) >= 120:
                delta = int(-event.delta / 120)
            self.canvas.yview_scroll(delta, "units")
        elif getattr(event, "num", None) == 4:
            self.canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            self.canvas.yview_scroll(1, "units")

    def _resolveProfileShirtPath(self, value: str) -> str:
        path = str(value or "").strip()
        if not path:
            return ""
        if os.path.isabs(path):
            return path
        return os.path.join(baseDir, path)

    def _factionShirtPath(self, factionKey: str) -> str:
        """Return the faction's own shirt-preview PNG, or '' if it has none.

        Looks at the *top level* of ``assets/<faction>/`` (not the ribbons/
        awards/commendations subdirs). Prefers a file literally named
        ``shirttemplate.png``; otherwise falls back to the first top-level PNG,
        so a faction can ship any single shirt image and have it picked up.
        """
        if not factionKey:
            return ""
        facDir = os.path.join(assetsRoot, factionKey)
        if not os.path.isdir(facDir):
            return ""
        preferred = os.path.join(facDir, "shirttemplate.png")
        if os.path.isfile(preferred):
            return preferred
        for fn in sorted(os.listdir(facDir), key=str.lower):
            full = os.path.join(facDir, fn)
            if fn.lower().endswith(".png") and os.path.isfile(full):
                return full
        return ""

    def _applyFactionShirt(self) -> None:
        """Point the shirt preview at the active faction's template, if it has one.

        Faction-driven shirt wins over the profile/ANRO default so the overlay
        tracks whatever faction is selected. Factions with no top-level PNG keep
        the current shirt, so the default install still shows the ANRO mockup.
        """
        shirt = self._factionShirtPath(self.activeFactionKey)
        if not shirt:
            return
        self.overlaySourcePath = shirt
        self.updateOverlaySourceLabel()
        self._refreshOverlayTemplateChoices()

    def refreshProfileChoices(self) -> None:
        names = listProfileNames()
        self.profileCombo["values"] = names
        if self.profileName not in names:
            self.profileName = names[0]
        self.profileVar.set(self.profileName)

    def _applySelectedProfile(self, profileName: str) -> None:
        normalized = _normalizeProfileName(profileName)
        selectedBefore = self.getSelectedRibbonNames() if self.checkboxVars else []

        self.profileName = normalized
        self.profileData = ensureProfileFile(self.profileName)
        applyProfile(self.profileData)

        profileShirt = self._resolveProfileShirtPath(profileSelectedShirt)
        if profileShirt:
            self.overlaySourcePath = profileShirt
        else:
            self.overlaySourcePath = previewOverlayPath if os.path.exists(previewOverlayPath) else ""
        # A faction's own shirt template (assets/<faction>/) takes priority over
        # the profile/ANRO default so the preview tracks the selected faction.
        factionShirt = self._factionShirtPath(self.activeFactionKey)
        if factionShirt:
            self.overlaySourcePath = factionShirt
        self.updateOverlaySourceLabel()

        try:
            self.ribbonGroups = loadRibbonGroups()
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            self.ribbonGroups = {key: [] for key in categoryLabels}
        self.renderer = makeRenderer(self.ribbonGroups)

        self._rebuildSections(selectedBefore)
        self.refreshProfileChoices()
        self._resyncManualGridToProfile()
        self.applyFilter()
        self.schedulePreview()
        self.saveCurrentSettings()

    def _resyncManualGridToProfile(self) -> None:
        """Resize the manual placement canvas + prune slots after a profile swap.

        Row counts and capacities come from module globals that `applyProfile`
        just rewrote, so the layout will now compute against the new profile.
        Any slot indices that no longer exist get dropped.
        """
        if not hasattr(self, "manualGridCanvas"):
            return
        rects, gw, gh = self._manualGridLayout()
        valid = {idx for idx, *_ in rects}
        stale = [k for k in self.manualRibbonSlots if k not in valid]
        for k in stale:
            self.manualRibbonSlots.pop(k, None)
            self.manualSlotColors.pop(k, None)
        if self._manualSelectedSlot not in valid:
            self._manualSelectedSlot = None
        self.manualGridCanvas.config(width=gw, height=gh)
        self._refreshManualRibbonChoices()
        self._redrawManualGrid()

    def _rebuildSections(self, selectedNames: list[str]) -> None:
        for widget in list(self.leftColumn.winfo_children()):
            widget.destroy()
        for widget in list(self.rightColumn.winfo_children()):
            widget.destroy()
        self.checkboxVars = {}
        self.categorySections = []
        self._buildSections()
        self._loadCollapsedSettings()
        for name in selectedNames:
            if name in self.checkboxVars:
                self.checkboxVars[name].set(1)

    def reloadAssets(self) -> None:
        """Rescan assets/ and factions/ without restarting the app.

        Useful after adding a new ribbon PNG, swapping a faction's palette
        in factions/<KEY>.json, or moving files around — the engine picks
        up the new state on the next preview render.
        """
        global _factionRegistry, _factionRecolorCache
        _factionRegistry = None
        _factionRecolorCache = None
        selectedBefore = self.getSelectedRibbonNames()
        try:
            self.factionRegistry = getFactionRegistry()
            setActiveFaction(self.activeFactionKey)
        except Exception as exc:
            messagebox.showerror("Reload failed", str(exc))
            return
        try:
            self.ribbonGroups = loadRibbonGroups()
        except Exception as exc:
            messagebox.showerror("Reload failed", str(exc))
            self.ribbonGroups = {key: [] for key in categoryLabels}
        self.renderer = makeRenderer(self.ribbonGroups)
        self._rebuildSections(selectedBefore)
        self._refreshManualRibbonChoices()
        self._refreshOverlayTemplateChoices()
        if hasattr(self, "_loadoutThumbCache"):
            self._loadoutThumbCache.clear()
        self.applyFilter()
        self.schedulePreview()
        self._toast("Assets reloaded")

    def _onProfileChanged(self, _event=None) -> None:
        self._applySelectedProfile(self.profileVar.get())

    def reloadCurrentProfile(self) -> None:
        self._applySelectedProfile(self.profileVar.get())

    def _createNewProfile(self) -> None:
        """Duplicate the active profile under a new name and switch to it.

        The user can then open Tools → Profile Editor… to tweak the copy.
        """
        name = _askString(
            self.root, "New profile", "Name for the new profile:", "custom"
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror("Error", "Profile name cannot be empty.")
            return
        normalized = _normalizeProfileName(name) if "_normalizeProfileName" in globals() else name
        targetPath = _profilePathForName(normalized)
        if os.path.exists(targetPath):
            messagebox.showerror("Error", f"A profile named {normalized!r} already exists.")
            return
        try:
            saveProfile(deepcopy(self.profileData), normalized)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self.profileName = normalized
        self.profileVar.set(normalized)
        self.refreshProfileChoices()
        self._applySelectedProfile(normalized)
        self._toast(f"Created profile {normalized!r}")

    def _refreshFactionChoices(self) -> None:
        if self.factionRegistry is None:
            self.factionCombo["values"] = [self.activeFactionKey]
            self.factionCombo.configure(state="disabled")
            return
        names = self.factionRegistry.names()
        self.factionCombo["values"] = names
        if self.activeFactionKey not in names:
            self.activeFactionKey = self.factionRegistry.default_key
        self.factionVar.set(self.activeFactionKey)
        self._updateFactionPaletteLabel()

    def _updateFactionPaletteLabel(self) -> None:
        if self.factionRegistry is None:
            self.factionPaletteLabel.config(text="")
            return
        faction = self.factionRegistry.get(self.activeFactionKey)
        p = faction.palette
        text = (
            f"  base #{p.base_color[0]:02x}{p.base_color[1]:02x}{p.base_color[2]:02x}"
            f"  stripe #{p.stripe_color[0]:02x}{p.stripe_color[1]:02x}{p.stripe_color[2]:02x}"
            f"  border #{p.border_color[0]:02x}{p.border_color[1]:02x}{p.border_color[2]:02x}"
        )
        self.factionPaletteLabel.config(text=text)

    def _openSettingsDialog(self) -> None:
        """Modal Settings window: recolor toggles + theme.

        All changes apply live where possible and persist to settings.json.
        Theme switching warns that a restart fully refreshes ttk styling.
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("Settings")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(background=self.theme["bg"])

        opts = getRecolorOptions()
        borderVar = tk.BooleanVar(value=opts.border)
        stripeVar = tk.BooleanVar(value=opts.stripe)
        baseVar = tk.BooleanVar(value=opts.base)
        themeVar = tk.StringVar(value=self.themeName)

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Recolor regions", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Checkbutton(frame, text="Recolor border", variable=borderVar).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(frame, text="Recolor stripe (bright pixels)", variable=stripeVar).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(frame, text="Recolor base (dark pixels)", variable=baseVar).grid(row=3, column=0, columnspan=2, sticky="w")

        if self.factionRegistry is not None:
            faction = self.factionRegistry.get(self.activeFactionKey)
            p = faction.palette
            swatchRow = ttk.Frame(frame)
            swatchRow.grid(row=10, column=0, columnspan=2, sticky="w", pady=(6, 0))
            ttk.Label(swatchRow, text=f"{faction.key} palette:").pack(side="left", padx=(0, 6))
            for label, rgb in (("base", p.base_color), ("stripe", p.stripe_color), ("border", p.border_color)):
                hexcode = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
                tk.Label(swatchRow, text="  ", background=hexcode, relief="solid", borderwidth=1, width=2).pack(side="left", padx=(0, 2))
                ttk.Label(swatchRow, text=f"{label} {hexcode}").pack(side="left", padx=(0, 8))

        ttk.Separator(frame, orient="horizontal").grid(row=4, column=0, columnspan=2, sticky="ew", pady=8)

        ttk.Label(frame, text="Theme", style="Section.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 4))
        for index, (key, label) in enumerate((("xp", "XP (classic)"), ("light", "Light"), ("dark", "Dark"))):
            ttk.Radiobutton(frame, text=label, variable=themeVar, value=key).grid(row=6 + index, column=0, columnspan=2, sticky="w")

        ttk.Separator(frame, orient="horizontal").grid(row=9, column=0, columnspan=2, sticky="ew", pady=8)

        statusLabel = ttk.Label(frame, text="", foreground=self.theme["accent"])
        statusLabel.grid(row=11, column=0, columnspan=2, sticky="w", pady=(0, 6))

        def apply_and_close() -> None:
            newOpts = RecolorOptions(
                border=bool(borderVar.get()),
                stripe=bool(stripeVar.get()),
                base=bool(baseVar.get()),
            )
            setRecolorOptions(newOpts)

            themeChanged = themeVar.get() != self.themeName
            if themeChanged and themeVar.get() in ("xp", "dark", "light"):
                self.themeName = themeVar.get()
                self.theme = getThemePalette(self.themeName)
                self.themeBgRgb = _hexToRgb(self.theme["bg"])
                self._configureStyle()
                self._repaintTkWidgets()
                statusLabel.config(text="Theme applied.")

            self.saveCurrentSettings()
            self.schedulePreview()
            self._redrawManualGrid()
            dialog.after(150 if themeChanged else 0, dialog.destroy)

        buttons = ttk.Frame(frame)
        buttons.grid(row=12, column=0, columnspan=2, sticky="e", pady=(4, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Apply", command=apply_and_close).pack(side="right")

        dialog.grab_set()
        dialog.focus_set()

    def _onFactionChanged(self, _event=None) -> None:
        chosen = self.factionVar.get()
        if self.factionRegistry is None or chosen not in self.factionRegistry.factions:
            return
        setActiveFaction(chosen)
        self.activeFactionKey = chosen
        self._updateFactionPaletteLabel()
        # Swap the shirt preview to this faction's template (if it ships one).
        # Done before the profile-bound branch can early-return so the shirt
        # tracks the faction regardless of which path we take below.
        self._applyFactionShirt()
        # Loadout thumbnails are recolored per-faction; bust the cache so the
        # next loadout dialog repaints them in the new palette.
        if hasattr(self, "_loadoutThumbCache"):
            self._loadoutThumbCache.clear()

        # If the faction binds a specific Engine Profile, switch to it. This
        # lets different units use different uniform layouts without manual
        # profile-picking each time.
        boundProfile = self.factionRegistry.engine_profile_for(chosen) if self.factionRegistry else ""
        if boundProfile:
            normalized = _normalizeProfileName(boundProfile)
            if normalized in listProfileNames() and normalized != self.profileName:
                self._applySelectedProfile(normalized)
                return  # _applySelectedProfile already handles rebuild + save.

        selectedBefore = self.getSelectedRibbonNames() if self.checkboxVars else []
        try:
            self.ribbonGroups = loadRibbonGroups()
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            self.ribbonGroups = {key: [] for key in categoryLabels}
        self.renderer = makeRenderer(self.ribbonGroups)
        self._rebuildSections(selectedBefore)
        self.applyFilter()
        self._refreshManualRibbonChoices()
        self._redrawManualGrid()

        self.saveCurrentSettings()
        self.schedulePreview()

    def _buildSections(self) -> None:
        for category, items in self.ribbonGroups.items():
            if not items:
                continue
            if category == "ribbons":
                sectionsConfig = self.settingsData.get("sections", {})
                anrocomNames = _normalizeSectionNames(sectionsConfig.get(anrocomSettingsKey, []))

                anrocomItems = [item for item in items if item.name in anrocomNames]
                remainingItems = [item for item in items if item.name not in anrocomNames]

                certItems = [item for item in remainingItems if certificationKeyword in item.name.lower()]
                otherItems = [item for item in remainingItems if certificationKeyword not in item.name.lower()]

                self._addSection(self.leftColumn, certificationsSectionLabel, certItems, draggable=True)
                self._addSection(self.leftColumn, categoryLabels.get(category, category), otherItems, draggable=True)
                self._addSection(self.leftColumn, anrocomSectionLabel, anrocomItems, draggable=True)
                continue

            if category in ("gorget", "spbadge"):
                self._addDropdownSection(self.rightColumn, categoryLabels.get(category, category), items)
            elif category == "sacks":
                self._addMedalSlotsSection(self.rightColumn, categoryLabels.get(category, category), items)
            else:
                self._addSection(self.rightColumn, categoryLabels.get(category, category), items)

    def _addSection(self, parent, labelText: str, items: list[AssetItem], draggable: bool = False) -> None:
        if not items:
            return

        header = ttk.Frame(parent)
        header.pack(fill="x", pady=(10, 0))

        label = ttk.Label(header, text=labelText, style="Section.TLabel")
        label.pack(side="left", anchor="w")

        toggleBtn = ttk.Button(header, text=expandedIcon, width=1, style="Toggle.TButton")
        toggleBtn.pack(side="right")

        content = ttk.Frame(parent)
        content.pack(fill="x")

        sectionItems = []
        for item in items:
            var = tk.IntVar()
            self.checkboxVars[item.name] = var
            widget = ttk.Checkbutton(
                content,
                text=item.name,
                variable=var,
                command=self._onCheckboxChanged,
            )
            widget.pack(anchor="w")
            sectionItems.append({"name": item.name, "widget": widget, "path": item.path})
            widget.bind("<Enter>", lambda _event, it=item: self.setHoverPreview(it))
            widget.bind("<Leave>", lambda _event: self.clearHoverPreview())
            if draggable:
                widget.bind("<ButtonPress-1>", lambda e, n=item.name: self._onSidebarDragStart(e, n), add="+")
                widget.bind("<B1-Motion>", self._onSidebarDragMotion, add="+")
                widget.bind("<ButtonRelease-1>", self._onSidebarDragEnd, add="+")

        section = SectionUI(
            key=labelText,
            header=header,
            toggle=toggleBtn,
            content=content,
            items=sectionItems,
            collapsed=False,
        )

        def toggleSection() -> None:
            section.collapsed = not section.collapsed
            section.toggle.config(text=collapsedIcon if section.collapsed else expandedIcon)
            self.applyFilter()
            self.saveCurrentSettings()

        toggleBtn.config(command=toggleSection)
        self.categorySections.append(section)

    def _addDropdownSection(self, parent, labelText: str, items: list[AssetItem]) -> None:
        """Single-select dropdown section (gorget, special badges).

        One item can be active at a time. Underneath we drive the same
        IntVar-per-item dict the renderer reads, so the rest of the pipeline
        is untouched.
        """
        if not items:
            return
        header = ttk.Frame(parent)
        header.pack(fill="x", pady=(10, 0))
        ttk.Label(header, text=labelText, style="Section.TLabel").pack(side="left", anchor="w")

        content = ttk.Frame(parent)
        content.pack(fill="x")

        names = [item.name for item in items]
        choices = ["NONE"] + names
        var = tk.StringVar(value="NONE")
        combo = ttk.Combobox(content, textvariable=var, values=choices, state="readonly")
        combo.pack(fill="x", padx=2, pady=2)

        # Back the dropdown with hidden BooleanVars so existing selection
        # plumbing (loadouts, PNG metadata, getSelectedRibbonNames) still works.
        intVars: dict[str, tk.IntVar] = {}
        for item in items:
            intVar = tk.IntVar()
            self.checkboxVars[item.name] = intVar
            intVars[item.name] = intVar

        def onChange(_event=None) -> None:
            chosen = var.get()
            self._captureHistory()
            for name, iv in intVars.items():
                iv.set(1 if name == chosen else 0)
            self.schedulePreview()

        combo.bind("<<ComboboxSelected>>", onChange)

        # Restore previous selection from checkboxVars (e.g. after loadout load).
        for item in items:
            if self.checkboxVars.get(item.name) and self.checkboxVars[item.name].get():
                var.set(item.name)
                break

        sectionItems = [{"name": item.name, "widget": combo, "path": item.path} for item in items]
        section = SectionUI(
            key=labelText, header=header, toggle=None, content=content,
            items=sectionItems, collapsed=False,
        )
        self.categorySections.append(section)

    def _addBadgeToggle(self, container) -> dict:
        """Toggle + dropdown for the 'department badge replaces bonus medals' mode.

        Returns a dict {use_var, name_var, dropdown} so the medal section can
        wire the toggle into its enable/disable logic.
        """
        if not hasattr(self, "useDepartmentBadgeVar"):
            self.useDepartmentBadgeVar = tk.BooleanVar(value=bool(self.settingsData.get("use_department_badge", False)))
        if not hasattr(self, "departmentBadgeVar"):
            self.departmentBadgeVar = tk.StringVar(value=self.settingsData.get("department_badge", "NONE") or "NONE")

        # Source = spbadge category (already user-extensible via faction JSON).
        badgeItems = self.ribbonGroups.get("spbadge", [])
        choices = ["NONE"] + sorted(item.name for item in badgeItems)

        row = ttk.Frame(container)
        row.pack(fill="x", pady=(2, 0))
        self._badgeToggleRow = row
        ttk.Checkbutton(
            row,
            text="Department badge (replaces bonus medals)",
            variable=self.useDepartmentBadgeVar,
            command=self._onBadgeToggleChanged,
        ).pack(anchor="w")

        badgeRow = ttk.Frame(container)
        badgeRow.pack(fill="x", pady=(2, 4))
        self._badgeDropdownRow = badgeRow
        ttk.Label(badgeRow, text="Badge:").pack(side="left")
        self.departmentBadgeCombo = ttk.Combobox(
            badgeRow,
            textvariable=self.departmentBadgeVar,
            values=choices,
            state="readonly",
        )
        self.departmentBadgeCombo.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.departmentBadgeCombo.bind("<<ComboboxSelected>>", lambda _e: (self.saveCurrentSettings(), self.schedulePreview()))
        return {"row": badgeRow}

    def _onBadgeToggleChanged(self) -> None:
        """Toggle handler for the department-badge checkbox.

        When the toggle turns ON, the bonus row (under the nametape) is
        replaced by a single department badge, so we clear every bonus-medal
        slot first to avoid the renderer silently dropping them under the
        badge. Then `_applyBadgeAwardVisibility` adjusts what's visible (hide
        the bonus group, show the badge dropdown — or the inverse on
        toggle-off). Finally we persist and re-render.
        """
        self._captureHistory()
        # Clear bonus medal slots when switching to badge mode so they don't
        # silently render under the badge that replaces them.
        if self.useDepartmentBadgeVar.get():
            for v in getattr(self, "medalBonusVars", []):
                v.set("NONE")
        self._applyBadgeAwardVisibility()
        self.saveCurrentSettings()
        self.schedulePreview()

    def _applyBadgeAwardVisibility(self) -> None:
        """Reconcile the medal-section layout with the department-badge state.

        The badge replaces the **bonus** row (under the nametape), so:
          * **Badge ON** — hide the entire `Bonus medals` group (clearing its
            StringVars so stale picks never leak into the render) and show the
            badge dropdown row. The `Award medals` group is untouched.
          * **Badge OFF** — restore the `Bonus medals` group, hide the badge
            dropdown row, and reset its StringVar to "NONE" so the renderer
            doesn't apply a stale badge.

        Idempotent and called from three places: initial section build,
        the toggle handler, and `clearAll`. All widget references are
        looked up with `getattr` so partial-construction states (e.g.
        called during boot before `_addBadgeToggle` runs) no-op cleanly.
        """
        badgeOn = bool(getattr(self, "useDepartmentBadgeVar", tk.BooleanVar()).get())
        bonus = getattr(self, "_bonusMedalsGroup", None)
        if bonus is not None:
            if badgeOn:
                bonus.pack_forget()
            else:
                # Keep the bonus group above the badge toggle row.
                toggleRow = getattr(self, "_badgeToggleRow", None)
                if toggleRow is not None:
                    bonus.pack(fill="x", before=toggleRow)
                else:
                    bonus.pack(fill="x")

        # Clear bonus slots while the badge owns that row so they don't render.
        if badgeOn:
            for v in getattr(self, "medalBonusVars", []):
                v.set("NONE")

        # Badge dropdown row only when toggle is on.
        badgeRow = getattr(self, "_badgeDropdownRow", None)
        if badgeRow is not None:
            if badgeOn:
                badgeRow.pack(fill="x", pady=(2, 4))
            else:
                badgeRow.pack_forget()
                if hasattr(self, "departmentBadgeVar"):
                    self.departmentBadgeVar.set("NONE")

    def _addMedalSlotsSection(self, parent, labelText: str, items: list[AssetItem]) -> None:
        """Two mirrored slot groups of single-select dropdowns (profile-driven):

          * `Award medals` — `maxMedalsPerSide` slots. Render as a centred row
            UNDER the ribbons block (right side).
          * `Bonus medals` — `maxMedalsPerSide` slots. Render as a centred row
            UNDER the nametape (left side); replaced by the department badge
            when that toggle is on.

        Both groups are sourced from the full medal pool (every PNG in awards/),
        so any medal can sit in any of the 6 slots and new PNGs are pickable
        without a profile edit. The two rows share one Y anchor and identical
        spacing so a medal looks the same in either.

        Slot counts come straight from the engine profile, so growing
        the medal pool is a JSON-only change. Duplicates across slots
        are allowed (triple Diamond is a real configuration). Behind the
        UI we still maintain a hidden per-name `IntVar` map so loadouts,
        PNG metadata roundtrip, and `getSelectedRibbonNames` keep working
        unchanged — the StringVar-per-slot list is what carries duplicate
        info through to the renderer's `awardSlots` / `bonusSlots` params.

        A department-badge toggle is appended at the bottom of the
        section; see `_addBadgeToggle` and `_applyBadgeAwardVisibility`
        for how it reshapes this UI at runtime.
        """
        if not items:
            return
        header = ttk.Frame(parent)
        header.pack(fill="x", pady=(10, 0))
        ttk.Label(header, text=labelText, style="Section.TLabel").pack(side="left", anchor="w")

        content = ttk.Frame(parent)
        content.pack(fill="x")

        # Back every medal with a hidden IntVar so existing pipeline is unchanged.
        intVars: dict[str, tk.IntVar] = {}
        for item in items:
            iv = tk.IntVar()
            self.checkboxVars[item.name] = iv
            intVars[item.name] = iv

        # Any medal can go in any slot: both the award row (under the ribbons)
        # and the bonus row (under the nametape) are populated from the full
        # medal pool. The award/bonus split is purely positional now — "bonus"
        # is just an overflow set of award slots. Dropping a new PNG into
        # assets/<faction>/awards/ makes it pickable in either row immediately.
        allNames = sorted(item.name for item in items)
        awardNames = allNames
        bonusNames = allNames

        self.medalAwardVars: list[tk.StringVar] = []
        self.medalBonusVars: list[tk.StringVar] = []

        def buildGroup(groupLabel: str, names: list[str], slotVars: list[tk.StringVar], slotCount: int, comboList: Optional[list[ttk.Combobox]] = None) -> Optional[ttk.Frame]:
            if not names:
                return None
            group = ttk.Frame(content)
            group.pack(fill="x")
            ttk.Label(group, text=groupLabel).pack(anchor="w", pady=(4, 0))
            for idx in range(slotCount):
                v = tk.StringVar(value="NONE")
                slotVars.append(v)
                combo = ttk.Combobox(group, textvariable=v, values=["NONE"] + names, state="readonly")
                combo.pack(fill="x", padx=2, pady=1)
                combo.bind("<<ComboboxSelected>>", lambda _e: self._onMedalSlotChanged(intVars))
                if comboList is not None:
                    comboList.append(combo)
            return group

        self._bonusMedalCombos: list[ttk.Combobox] = []
        self._awardMedalsGroup = buildGroup("Award medals (under ribbons)", awardNames, self.medalAwardVars, maxMedalsPerSide)
        # Bonus row mirrors the award row under the nametape; same slot count.
        self._bonusMedalsGroup = buildGroup("Bonus medals (under nametape)", bonusNames, self.medalBonusVars, maxMedalsPerSide, self._bonusMedalCombos)

        # Department badge toggle goes at the end of the awards section.
        self._addBadgeToggle(content)

        # Restore from current IntVar state (loadout / settings).
        def restore(slotVars: list[tk.StringVar], pool: list[str]) -> None:
            used = [n for n in pool if intVars.get(n) and intVars[n].get()]
            for i, var in enumerate(slotVars):
                var.set(used[i] if i < len(used) else "NONE")
        restore(self.medalAwardVars, awardNames)
        restore(self.medalBonusVars, bonusNames)

        # Reflect persisted badge state on startup (after restore so hidden
        # slots get cleared if badge mode caps bonus to 3).
        self._applyBadgeAwardVisibility()

        sectionItems = [{"name": item.name, "widget": content, "path": item.path} for item in items]
        section = SectionUI(
            key=labelText, header=header, toggle=None, content=content,
            items=sectionItems, collapsed=False,
        )
        self.categorySections.append(section)

    def _onMedalSlotChanged(self, intVars: dict[str, tk.IntVar]) -> None:
        """Sync hidden IntVars after a medal-slot dropdown changes.

        The visible dropdowns hold per-slot StringVars (one entry per
        physical pocket position) and *allow duplicates* — e.g. three
        Diamond slots all set to "Diamond Medal". The renderer still reads
        the legacy per-name IntVar map, so we flip every name's IntVar to
        1 if it appears in *any* slot. Loadouts / PNG metadata roundtrip
        on those IntVars; the per-slot list is what `buildImage` consumes
        when present, which is what carries the duplicates through.
        """
        self._captureHistory()
        active = set()
        for slotVars in (self.medalAwardVars, self.medalBonusVars):
            for v in slotVars:
                if v.get() != "NONE":
                    active.add(v.get())
        for name, iv in intVars.items():
            iv.set(1 if name in active else 0)
        self.schedulePreview()

    def _loadCollapsedSettings(self) -> None:
        collapsedSettings = self.settingsData.get("collapsed_sections", {})
        if not isinstance(collapsedSettings, dict):
            return
        for section in self.categorySections:
            if section.key in collapsedSettings:
                section.collapsed = bool(collapsedSettings[section.key])
                if section.toggle is not None:
                    section.toggle.config(text=collapsedIcon if section.collapsed else expandedIcon)

    def _onCheckboxChanged(self) -> None:
        self._captureHistory()
        if self.showSelectedVar.get():
            self.applyFilter()
        self.schedulePreview()

    def _onToggleShowSelected(self) -> None:
        self.applyFilter()
        self.saveCurrentSettings()

    def _onToggleOverlay(self) -> None:
        self.updatePreview()
        self.saveCurrentSettings()

    def _listOverlayTemplates(self) -> list[tuple[str, str]]:
        """Return [(label, abs_path)] of every shirt template the user can pick.

        Sources: every PNG in templates/, plus the legacy ANRO template inside
        Characters/, plus the user's current overlaySourcePath if it lives
        somewhere else (e.g. a profile-defined path).
        """
        seen: dict[str, str] = {}
        if os.path.isdir(templatesDir):
            for fn in sorted(os.listdir(templatesDir), key=str.lower):
                if fn.lower().endswith(".png"):
                    seen[os.path.splitext(fn)[0]] = os.path.join(templatesDir, fn)
        if os.path.exists(previewOverlayPath):
            seen.setdefault(os.path.splitext(os.path.basename(previewOverlayPath))[0], previewOverlayPath)
        current = (self.overlaySourcePath or "").strip()
        if current and os.path.exists(current) and current not in seen.values():
            seen.setdefault(os.path.splitext(os.path.basename(current))[0], current)
        return sorted(seen.items(), key=lambda kv: kv[0].lower())

    def _refreshOverlayTemplateChoices(self) -> None:
        if not hasattr(self, "overlayTemplateCombo"):
            return
        templates = self._listOverlayTemplates()
        self._overlayTemplateMap = {label: path for label, path in templates}
        labels = list(self._overlayTemplateMap.keys())
        self.overlayTemplateCombo["values"] = labels
        currentPath = (self.overlaySourcePath or "").strip()
        matchedLabel = next(
            (label for label, path in self._overlayTemplateMap.items() if path == currentPath),
            "",
        )
        if not matchedLabel and labels:
            matchedLabel = labels[0]
            self.overlaySourcePath = self._overlayTemplateMap[matchedLabel]
        self.overlayTemplateVar.set(matchedLabel)

    def _onOverlayTemplateChanged(self, _event=None) -> None:
        label = self.overlayTemplateVar.get()
        path = getattr(self, "_overlayTemplateMap", {}).get(label, "")
        if not path:
            return
        self.overlaySourcePath = path
        self.updateOverlaySourceLabel()
        self.saveCurrentSettings()
        if self.overlayVar.get():
            self.schedulePreview()

    def updateOverlaySourceLabel(self) -> None:
        path = self.overlaySourcePath.strip()
        if path:
            source = os.path.basename(path)
        elif os.path.exists(previewOverlayPath):
            source = os.path.basename(previewOverlayPath)
        else:
            source = "None"
        self.overlaySourceLabel.config(text=f"Shirt source: {source}")

    def schedulePreview(self) -> None:
        if self.previewJob is not None:
            self.root.after_cancel(self.previewJob)
        self.previewJob = self.root.after(150, self.updatePreview)

    def applyFilter(self) -> None:
        query = self.searchVar.get().strip().lower()
        onlySelected = self.showSelectedVar.get()
        filterActive = bool(query) or onlySelected

        for section in self.categorySections:
            # Dropdown/slot sections don't have individual per-item widgets — they
            # own their own combobox layout — so skip the per-item repack dance.
            if section.toggle is None:
                section.header.pack(fill="x", pady=(10, 0))
                section.content.pack(fill="x")
                continue

            section.header.pack_forget()
            section.content.pack_forget()
            for item in section.items:
                item["widget"].pack_forget()

            visibleItems = [
                item
                for item in section.items
                if query in item["name"].lower()
                and (not onlySelected or self.checkboxVars[item["name"]].get())
            ]

            if visibleItems:
                section.header.pack(fill="x", pady=(10, 0))
                if filterActive or not section.collapsed:
                    section.content.pack(fill="x")
                    for item in visibleItems:
                        item["widget"].pack(anchor="w")

        self.canvas.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def saveCurrentSettings(self) -> None:
        settings = dict(self.settingsData)
        settings["preview_scale"] = self.previewScale
        settings["only_show_selected"] = bool(self.showSelectedVar.get())
        settings["collapsed_sections"] = {
            section.key: bool(section.collapsed) for section in self.categorySections
        }
        settings["preview_overlay"] = bool(self.overlayVar.get())
        settings["overlay_template"] = (self.overlaySourcePath or "").strip()
        settings["recent_files"] = list(getattr(self, "recentFiles", []))
        settings["loadout_favorites"] = sorted(getattr(self, "loadoutFavorites", set()))
        settings["profile"] = self.profileName
        settings["faction"] = self.activeFactionKey
        settings["theme"] = self.themeName
        opts = getRecolorOptions()
        settings["recolor_border"] = bool(opts.border)
        settings["recolor_stripe"] = bool(opts.stripe)
        settings["recolor_base"] = bool(opts.base)
        settings["manual_ribbon_mode"] = bool(self.manualRibbonMode)
        settings["manual_ribbon_slots"] = {str(k): v for k, v in self.manualRibbonSlots.items()}
        settings["manual_slot_colors"] = {str(k): dict(v) for k, v in self.manualSlotColors.items()}
        if hasattr(self, "useDepartmentBadgeVar"):
            settings["use_department_badge"] = bool(self.useDepartmentBadgeVar.get())
        if hasattr(self, "departmentBadgeVar"):
            settings["department_badge"] = self.departmentBadgeVar.get()
        settings.setdefault("presets", {})
        settings.setdefault("theme", self.themeName)
        settings.setdefault("sections", {anrocomSettingsKey: []})
        if isinstance(settings.get("sections"), dict):
            settings["sections"].setdefault(anrocomSettingsKey, [])
        saveSettings(settings)
        self.settingsData = settings

    def focusSearch(self, _event=None):
        current = self.root.focus_get()
        if current == self.searchEntry:
            return None

        try:
            if isinstance(current, (tk.Entry, ttk.Entry)):
                cursor = current.index(tk.INSERT)
                text = current.get()
                if cursor > 0 and cursor <= len(text) and text[cursor - 1] == "/":
                    current.delete(cursor - 1)
        except Exception:
            pass

        self.searchEntry.focus_set()
        self.searchEntry.select_range(0, tk.END)
        return "break"

    def adjustPreviewScale(self, delta: float) -> None:
        self.previewScale = max(1.0, self.previewScale + delta)
        self.updatePreviewScaleLabel()
        self.updatePreview()
        self.ensureWindowSize()
        self.saveCurrentSettings()

    def updatePreviewScaleLabel(self) -> None:
        self.previewSizeLabel.config(text=f"Preview size: {self.previewScale:.1f}x")

    def ensureWindowSize(self) -> None:
        self.root.update_idletasks()
        reqWidth = self.root.winfo_reqwidth()
        reqHeight = self.root.winfo_reqheight()
        currentWidth = self.root.winfo_width()
        currentHeight = self.root.winfo_height()
        newWidth = max(currentWidth, reqWidth)
        newHeight = max(currentHeight, reqHeight)
        if newWidth != currentWidth or newHeight != currentHeight:
            self.root.geometry(f"{newWidth}x{newHeight}")

    def setStatus(self, message: str) -> None:
        self.labelStatus.config(text=message or "")

    def _updateLoadoutLabel(self) -> None:
        if not hasattr(self, "loadoutLabel"):
            return
        text = f"Loadout: {self.currentLoadoutName}" if self.currentLoadoutName else ""
        self.loadoutLabel.config(text=text)

    def _toast(self, message: str, duration_ms: int = 1800) -> None:
        """Lightweight non-modal notification — small label at bottom-right of root.

        Replaces messagebox.showinfo for routine confirmations so the user
        isn't interrupted by a modal dialog for "Saved!" type messages.
        Errors still use messagebox so they can't be missed.
        """
        try:
            toast = tk.Toplevel(self.root)
            toast.overrideredirect(True)
            toast.attributes("-topmost", True)
            try:
                toast.attributes("-alpha", 0.92)
            except tk.TclError:
                pass
            bg = self.theme.get("accent", "#3a78c2")
            label = tk.Label(
                toast,
                text=message,
                bg=bg,
                fg="#ffffff",
                padx=14,
                pady=8,
                font=("Tahoma", 9, "bold"),
            )
            label.pack()
            self.root.update_idletasks()
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = self.root.winfo_width()
            rh = self.root.winfo_height()
            tw = toast.winfo_reqwidth()
            th = toast.winfo_reqheight()
            x = rx + rw - tw - 24
            y = ry + rh - th - 32
            toast.geometry(f"+{max(rx, x)}+{max(ry, y)}")
            toast.after(duration_ms, toast.destroy)
        except Exception:
            # Toast is best-effort — fall back to a status update silently.
            self.setStatus(message)

    # ------------------------------------------------------------------ Recent files
    def _rememberRecentFile(self, path: str) -> None:
        """Prepend a freshly-generated PNG to the recent-files list."""
        try:
            path = os.path.abspath(path)
        except Exception:
            return
        self.recentFiles = [p for p in self.recentFiles if p != path]
        self.recentFiles.insert(0, path)
        self.recentFiles = self.recentFiles[:8]
        self.saveCurrentSettings()
        if hasattr(self, "_rebuildRecentMenu"):
            self._rebuildRecentMenu()

    def _openRecentFile(self, path: str) -> None:
        if not os.path.exists(path):
            messagebox.showerror("Missing", f"File no longer exists:\n{path}")
            self.recentFiles = [p for p in self.recentFiles if p != path]
            self.saveCurrentSettings()
            self._rebuildRecentMenu()
            return
        self._loadImageFromPath(path)

    def _loadImageFromPath(self, path: str) -> None:
        """Open a PNG, apply embedded metadata if any, else just show it."""
        try:
            with Image.open(path) as img:
                metadata = dict(img.info)
                temp = img.convert("RGBA")
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        applied = False
        payload = metadata.get("ribbonengine")
        if isinstance(payload, str):
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                # kind:"loadout" PNGs apply directly. To keep one
                # permanently, drop the file into the `loadouts/` folder.
                try:
                    applied = self.applyMetadata(parsed)
                except Exception:
                    applied = False
        if applied:
            self.baseImage = None
        else:
            self.baseImage = temp
        self.schedulePreview()
        self._toast("Loaded image + metadata" if applied else "Loaded image")

    def _buildMenuBar(self) -> None:
        menubar = tk.Menu(self.root)
        fileMenu = tk.Menu(menubar, tearoff=False)
        fileMenu.add_command(label="Generate Image\tCtrl+S", command=self.generateImage)
        fileMenu.add_command(label="Export Loadout\tCtrl+E", command=self._exportLoadoutImage)
        fileMenu.add_command(label="Copy to Clipboard\tCtrl+Shift+C", command=self.copyImageToClipboard)
        fileMenu.add_command(label="Diff…\tCtrl+D", command=self._openDiffDialog)
        fileMenu.add_command(label="Reload Assets", command=self.reloadAssets)
        fileMenu.add_separator()
        self.recentMenu = tk.Menu(fileMenu, tearoff=False)
        fileMenu.add_cascade(label="Recent", menu=self.recentMenu)
        fileMenu.add_separator()
        fileMenu.add_command(label="Clear All\tCtrl+Esc", command=self.clearAll)
        fileMenu.add_command(label="Quit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=fileMenu)

        toolsMenu = tk.Menu(menubar, tearoff=False)
        toolsMenu.add_command(label="Loadouts…", command=self._openLoadoutDialog)
        toolsMenu.add_command(label="Import from URL…", command=self._openImportUrlDialog)
        toolsMenu.add_separator()
        toolsMenu.add_command(label="Export Faction Pack…", command=self._exportFactionPack)
        toolsMenu.add_command(label="Import Faction Pack…", command=self._importFactionPack)
        toolsMenu.add_command(label="Profile Editor…", command=self._openProfileEditorDialog)
        toolsMenu.add_command(label="Move/Rename Ribbon…", command=self._openMoveRibbonDialog)
        toolsMenu.add_command(label="Ribbon Editor…", command=self._openRibbonEditorDialog)
        toolsMenu.add_command(label="Settings…", command=self._openSettingsDialog)
        menubar.add_cascade(label="Tools", menu=toolsMenu)

        helpMenu = tk.Menu(menubar, tearoff=False)
        md_files = sorted(
            f for f in os.listdir(baseDir)
            if f.lower().endswith(".md") and os.path.isfile(os.path.join(baseDir, f))
        )
        if md_files:
            for fname in md_files:
                helpMenu.add_command(
                    label=fname,
                    command=lambda f=fname: self._openMarkdownViewer(os.path.join(baseDir, f)),
                )
        else:
            helpMenu.add_command(label="(no .md files found)", state="disabled")
        helpMenu.add_separator()
        helpMenu.add_command(
            label="Check for Updates…", command=lambda: self._checkForUpdates(silent=False)
        )
        helpMenu.add_command(label=f"About (v{APP_VERSION})", command=self._showAbout)
        menubar.add_cascade(label="Help", menu=helpMenu)

        self.root.config(menu=menubar)
        self._rebuildRecentMenu()

    def _openMarkdownViewer(self, path: str) -> None:
        """Open a markdown file in a read-only Toplevel with light formatting.

        No external dependencies — we lex the file ourselves and apply
        Tk Text tags for headings, bold/italic inline runs, code blocks,
        list bullets, and blockquotes. Hyperlinks are intentionally
        left as plain text (the user said they aren't needed).
        """
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                raw = fh.read()
        except Exception as exc:
            messagebox.showerror("Help", f"Couldn't open {os.path.basename(path)}: {exc}")
            return

        win = tk.Toplevel(self.root)
        win.title(f"Help — {os.path.basename(path)}")
        win.configure(background=self.theme["bg"])
        win.geometry("780x640")

        outer = ttk.Frame(win, padding=8)
        outer.pack(fill="both", expand=True)

        bg = self.theme.get("panel_bg", "#1f1f1f")
        fg = self.theme.get("fg", "#e6e6e6")
        accent = self.theme.get("accent", "#ffcc00")
        muted = self.theme.get("status", "#888")

        text = tk.Text(
            outer,
            wrap="word",
            background=bg,
            foreground=fg,
            insertbackground=fg,
            relief="flat",
            padx=14,
            pady=10,
            font=("Helvetica", 11),
            spacing1=2,
            spacing3=4,
        )
        vsb = ttk.Scrollbar(outer, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=vsb.set)
        text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Style tags.
        text.tag_configure("h1", font=("Helvetica", 20, "bold"), foreground=accent, spacing1=10, spacing3=6)
        text.tag_configure("h2", font=("Helvetica", 16, "bold"), foreground=accent, spacing1=8, spacing3=4)
        text.tag_configure("h3", font=("Helvetica", 13, "bold"), foreground=fg, spacing1=6, spacing3=3)
        text.tag_configure("h4", font=("Helvetica", 12, "bold"), foreground=fg, spacing1=4, spacing3=2)
        text.tag_configure("bold", font=("Helvetica", 11, "bold"))
        text.tag_configure("italic", font=("Helvetica", 11, "italic"))
        text.tag_configure("code", font=("Courier", 10), background="#2a2a2a", foreground="#f0c674")
        text.tag_configure(
            "codeblock", font=("Courier", 10), background="#161616",
            foreground="#f0c674", lmargin1=20, lmargin2=20, spacing1=4, spacing3=4,
        )
        text.tag_configure("bullet", lmargin1=18, lmargin2=36)
        text.tag_configure("numbered", lmargin1=18, lmargin2=36)
        text.tag_configure("quote", lmargin1=20, lmargin2=20, foreground=muted, font=("Helvetica", 11, "italic"))
        text.tag_configure("hr", foreground=muted)

        lines = raw.splitlines()
        in_code = False
        for line in lines:
            stripped = line.rstrip()

            # Fenced code block.
            if stripped.startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                text.insert("end", line + "\n", ("codeblock",))
                continue

            # Horizontal rule.
            if stripped in ("---", "***", "___"):
                text.insert("end", "─" * 60 + "\n", ("hr",))
                continue

            # Headings.
            m_heading = 0
            while m_heading < 6 and m_heading < len(stripped) and stripped[m_heading] == "#":
                m_heading += 1
            if m_heading and m_heading <= 6 and (len(stripped) == m_heading or stripped[m_heading] == " "):
                level = min(m_heading, 4)
                content = stripped[m_heading:].strip()
                self._insertMarkdownInline(text, content, base_tag=f"h{level}")
                text.insert("end", "\n", (f"h{level}",))
                continue

            # Blockquote.
            if stripped.startswith("> "):
                self._insertMarkdownInline(text, stripped[2:], base_tag="quote")
                text.insert("end", "\n", ("quote",))
                continue

            # Unordered list.
            ls = line.lstrip(" \t")
            if ls.startswith(("- ", "* ", "+ ")):
                indent = len(line) - len(ls)
                prefix = ("    " * (indent // 2)) + "• "
                text.insert("end", prefix, ("bullet",))
                self._insertMarkdownInline(text, ls[2:], base_tag="bullet")
                text.insert("end", "\n", ("bullet",))
                continue

            # Ordered list (1. / 2. / …).
            n = 0
            while n < len(ls) and ls[n].isdigit():
                n += 1
            if n > 0 and n < len(ls) and ls[n] == "." and (n + 1 == len(ls) or ls[n + 1] == " "):
                indent = len(line) - len(ls)
                prefix = ("    " * (indent // 2)) + ls[: n + 1] + " "
                text.insert("end", prefix, ("numbered",))
                self._insertMarkdownInline(text, ls[n + 2:] if len(ls) > n + 1 else "", base_tag="numbered")
                text.insert("end", "\n", ("numbered",))
                continue

            # Blank line.
            if not stripped:
                text.insert("end", "\n")
                continue

            # Paragraph.
            self._insertMarkdownInline(text, stripped)
            text.insert("end", "\n")

        text.configure(state="disabled")
        text.bind("<Control-c>", lambda _e: None)  # allow copy via default binding
        win.transient(self.root)
        win.focus_set()

    def _insertMarkdownInline(self, text: tk.Text, line: str, base_tag: Optional[str] = None) -> None:
        """Insert a single line with inline `code`, **bold**, *italic* runs.

        Hyperlinks (`[text](url)`) are rendered as plain "text (url)"
        — no clickable behavior, matching the user's spec.
        """
        i = 0
        n = len(line)
        # First, flatten [text](url) → "text (url)" so the inline pass
        # can ignore link syntax.
        import re
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)
        n = len(line)

        def tags_with(extra: str) -> tuple:
            return (base_tag, extra) if base_tag else (extra,)

        plain_tag: tuple = (base_tag,) if base_tag else ()

        while i < n:
            ch = line[i]
            # Inline code.
            if ch == "`":
                end = line.find("`", i + 1)
                if end != -1:
                    text.insert("end", line[i + 1:end], tags_with("code"))
                    i = end + 1
                    continue
            # Bold (**…**).
            if ch == "*" and i + 1 < n and line[i + 1] == "*":
                end = line.find("**", i + 2)
                if end != -1:
                    text.insert("end", line[i + 2:end], tags_with("bold"))
                    i = end + 2
                    continue
            # Italic (*…*) — but not **.
            if ch == "*":
                end = line.find("*", i + 1)
                if end != -1 and end > i + 1:
                    text.insert("end", line[i + 1:end], tags_with("italic"))
                    i = end + 1
                    continue
            # Plain character.
            text.insert("end", ch, plain_tag)
            i += 1

    def _rebuildRecentMenu(self) -> None:
        if not hasattr(self, "recentMenu"):
            return
        self.recentMenu.delete(0, "end")
        if not self.recentFiles:
            self.recentMenu.add_command(label="(empty)", state="disabled")
            return
        for path in self.recentFiles:
            label = os.path.basename(path)
            self.recentMenu.add_command(
                label=label, command=lambda p=path: self._openRecentFile(p)
            )

    # ------------------------------------------------------------------ Drag and drop
    def _enableDragAndDrop(self) -> None:
        """Bind OS file drops on the preview area when tkinterdnd2 is available."""
        if not _TKDND_AVAILABLE:
            return
        try:
            self.labelPreview.drop_target_register(tkinterdnd2.DND_Files)
            self.labelPreview.dnd_bind("<<Drop>>", self._onDropFile)
        except Exception:
            pass

    def _onDropFile(self, event) -> None:
        raw = event.data or ""
        # tkdnd returns space-separated paths, with braces around paths
        # containing spaces. Pull out the first path defensively.
        paths: list[str] = []
        token = ""
        in_brace = False
        for ch in raw:
            if ch == "{":
                in_brace = True
            elif ch == "}":
                in_brace = False
                if token:
                    paths.append(token)
                    token = ""
            elif ch == " " and not in_brace:
                if token:
                    paths.append(token)
                    token = ""
            else:
                token += ch
        if token:
            paths.append(token)
        for p in paths:
            if p.lower().endswith(".png") and os.path.isfile(p):
                self._loadImageFromPath(p)
                return
        self._toast("Drop a PNG to load it")

    def getSelectedRibbonNames(self) -> list[str]:
        return sorted([name for name, var in self.checkboxVars.items() if var.get()])

    def buildMetadata(self) -> dict:
        awards = [v.get() for v in getattr(self, "medalAwardVars", [])]
        bonuses = [v.get() for v in getattr(self, "medalBonusVars", [])]
        useBadge = bool(getattr(self, "useDepartmentBadgeVar", None) and self.useDepartmentBadgeVar.get())
        badge = self.departmentBadgeVar.get() if (useBadge and hasattr(self, "departmentBadgeVar")) else ""
        return {
            "version": METADATA_SCHEMA_VERSION,
            "profile": self.profileName,
            "ribbons": self.getSelectedRibbonNames(),
            "nameplate": self.entry.get().strip(),
            "faction": self.activeFactionKey,
            "awards": awards,
            "bonuses": bonuses,
            "department_badge": badge,
            "manual_slots": {str(k): v for k, v in self.manualRibbonSlots.items()},
            "manual_slot_colors": {str(k): dict(v) for k, v in self.manualSlotColors.items()},
        }

    def applyMetadata(self, metadata: dict) -> bool:
        if not isinstance(metadata, dict):
            return False

        # Profile mismatch: warn before silently dropping manual-slot indices
        # that the current profile's grid can't address.
        sourceProfile = metadata.get("profile")
        if (
            isinstance(sourceProfile, str)
            and sourceProfile
            and sourceProfile != self.profileName
        ):
            choice = messagebox.askyesnocancel(
                "Profile mismatch",
                f"This image was made under profile {sourceProfile!r}.\n"
                f"You're currently on {self.profileName!r}.\n\n"
                "Switch to the source profile? (No = load anyway, Cancel = abort)",
            )
            if choice is None:
                return False
            if choice:
                try:
                    self._applySelectedProfile(sourceProfile)
                except Exception:
                    pass

        ribbons = metadata.get("ribbons", [])
        if isinstance(ribbons, list):
            for var in self.checkboxVars.values():
                var.set(0)
            for name in ribbons:
                if name in self.checkboxVars:
                    self.checkboxVars[name].set(1)

        nameplate = metadata.get("nameplate")
        if isinstance(nameplate, str):
            self.entry.delete(0, tk.END)
            self.entry.insert(0, nameplate)

        factionKey = metadata.get("faction")
        if isinstance(factionKey, str) and self.factionRegistry is not None and factionKey in self.factionRegistry.factions:
            setActiveFaction(factionKey)
            self.activeFactionKey = factionKey
            self.factionVar.set(factionKey)
            self._updateFactionPaletteLabel()

        awards = metadata.get("awards")
        if isinstance(awards, list):
            for i, val in enumerate(awards):
                if i < len(getattr(self, "medalAwardVars", [])) and isinstance(val, str):
                    self.medalAwardVars[i].set(val or "NONE")
        bonuses = metadata.get("bonuses")
        if isinstance(bonuses, list):
            for i, val in enumerate(bonuses):
                if i < len(getattr(self, "medalBonusVars", [])) and isinstance(val, str):
                    self.medalBonusVars[i].set(val or "NONE")
        badge = metadata.get("department_badge")
        if isinstance(badge, str) and hasattr(self, "departmentBadgeVar"):
            if badge:
                if hasattr(self, "useDepartmentBadgeVar"):
                    self.useDepartmentBadgeVar.set(True)
                self.departmentBadgeVar.set(badge)
            else:
                if hasattr(self, "useDepartmentBadgeVar"):
                    self.useDepartmentBadgeVar.set(False)

        manualSlots = metadata.get("manual_slots")
        if isinstance(manualSlots, dict):
            self.manualRibbonSlots = {}
            for k, v in manualSlots.items():
                try:
                    self.manualRibbonSlots[int(k)] = str(v)
                except (TypeError, ValueError):
                    continue
            self.manualSlotColors = {}
            slotColors = metadata.get("manual_slot_colors")
            if isinstance(slotColors, dict):
                for k, v in slotColors.items():
                    if not isinstance(v, dict):
                        continue
                    try:
                        cleaned = {region: str(v[region]) for region in ("border", "stripe", "base") if isinstance(v.get(region), str)}
                        if cleaned:
                            self.manualSlotColors[int(k)] = cleaned
                    except (TypeError, ValueError):
                        continue
            if hasattr(self, "manualGridCanvas"):
                self._manualSelectedSlot = None
                self._manualThumbCache.clear()
                self._redrawManualGrid()

        if self.showSelectedVar.get():
            self.applyFilter()

        return True

    def _centeredPreview(self, image: Image.Image, size: int, bgColor: tuple[int, int, int, int]) -> Image.Image:
        w, h = image.size
        scale = min(size / w, size / h)
        newW = max(1, int(w * scale))
        newH = max(1, int(h * scale))
        resized = image.resize((newW, newH), Image.NEAREST)
        canvasImg = Image.new("RGBA", (size, size), bgColor)
        x = (size - newW) // 2
        y = (size - newH) // 2
        canvasImg.paste(resized, (x, y), resized)
        return canvasImg

    def _resolveOverlaySourcePath(self) -> Optional[str]:
        path = self.overlaySourcePath.strip()
        if path and os.path.exists(path):
            return path
        if os.path.exists(previewOverlayPath):
            return previewOverlayPath
        return None

    def _scaledOverlayCropBox(self, sourceSize: tuple[int, int]) -> Optional[tuple[int, int, int, int]]:
        sourceW, sourceH = sourceSize
        refW, refH = overlayTemplateSize
        if refW <= 0 or refH <= 0:
            return None

        x1, y1, x2, y2 = overlayFrontCropBox
        scaleX = sourceW / refW
        scaleY = sourceH / refH
        sx1 = int(round(x1 * scaleX))
        sy1 = int(round(y1 * scaleY))
        sx2 = int(round(x2 * scaleX))
        sy2 = int(round(y2 * scaleY))
        sx1 = max(0, min(sx1, sourceW - 1))
        sy1 = max(0, min(sy1, sourceH - 1))
        sx2 = max(sx1 + 1, min(sx2, sourceW))
        sy2 = max(sy1 + 1, min(sy2, sourceH))
        if sx2 <= sx1 or sy2 <= sy1:
            return None
        return (sx1, sy1, sx2, sy2)

    def _prepareOverlayImage(self, image: Image.Image) -> Image.Image:
        if image.size == (imageSize, imageSize):
            return image

        box = self._scaledOverlayCropBox(image.size)
        if box is not None:
            cropped = image.crop(box)
        else:
            side = min(image.size)
            x = (image.size[0] - side) // 2
            y = (image.size[1] - side) // 2
            cropped = image.crop((x, y, x + side, y + side))
        if cropped.size != (imageSize, imageSize):
            cropped = cropped.resize((imageSize, imageSize), Image.NEAREST)
        return cropped

    def _loadOverlayImage(self) -> Optional[Image.Image]:
        overlayPath = self._resolveOverlaySourcePath()
        if not overlayPath:
            return None
        try:
            with Image.open(overlayPath) as overlay:
                prepared = self._prepareOverlayImage(overlay.convert("RGBA"))
            return prepared
        except Exception:
            return None

    def setHoverPreview(self, item: AssetItem) -> None:
        if not item.path or not os.path.exists(item.path):
            self.clearHoverPreview()
            return

        # Run the same recolor pipeline used at render time so the hover
        # preview matches what will appear on the composed output.
        try:
            preview = loadRibbonImage(item).copy()
        except Exception:
            try:
                with Image.open(item.path) as img:
                    preview = img.convert("RGBA")
            except Exception:
                self.clearHoverPreview()
                return

        preview = self._centeredPreview(preview, hoverPreviewSize, self.themeBgRgb + (255,))
        self.hoverPreviewImg = ImageTk.PhotoImage(preview, master=self.root)
        self.hoverPreviewLabel.config(image=self.hoverPreviewImg)
        self.hoverPreviewLabel.image = self.hoverPreviewImg
        self.hoverNameLabel.config(text=item.name)
        desc = ""
        if self.factionRegistry is not None:
            desc = self.factionRegistry.description_for(item.name, self.activeFactionKey) or ""
        self.hoverDescLabel.config(text=desc)

    def clearHoverPreview(self) -> None:
        blank = Image.new("RGBA", (hoverPreviewSize, hoverPreviewSize), self.themeBgRgb + (255,))
        self.hoverPreviewImg = ImageTk.PhotoImage(blank, master=self.root)
        self.hoverPreviewLabel.config(image=self.hoverPreviewImg)
        self.hoverPreviewLabel.image = self.hoverPreviewImg
        self.hoverNameLabel.config(text="")
        self.hoverDescLabel.config(text="")

    def buildImage(self, requireNameForNew: bool, errorCallback: Optional[Callable[[str], None]]):
        selectedNames = set(self.getSelectedRibbonNames())
        placements: list[dict] = []
        result = self.renderer.buildImage(
            selectedNames=selectedNames,
            nameplateText=self.entry.get().strip(),
            baseImage=self.baseImage,
            requireNameForNew=requireNameForNew,
            errorCallback=errorCallback,
            faction=self.activeFactionKey,
            customOffsets=self.customOffsets,
            placements=placements,
            manualRibbonSlots=dict(self.manualRibbonSlots) if self.manualRibbonSlots else None,
            manualSlotColors=dict(self.manualSlotColors) if self.manualSlotColors else None,
            awardSlots=[v.get() for v in getattr(self, "medalAwardVars", [])] or None,
            bonusSlots=[v.get() for v in getattr(self, "medalBonusVars", [])] or None,
            departmentBadge=(
                self.departmentBadgeVar.get()
                if getattr(self, "useDepartmentBadgeVar", None) and self.useDepartmentBadgeVar.get()
                else None
            ),
        )
        # Keep the last set of placements around for hit-testing drag events.
        self.lastPlacements = placements
        return result

    def _setPreviewImage(self, image: Image.Image) -> None:
        previewSize = int(imageSize * self.previewScale)
        self.previewImg = ImageTk.PhotoImage(
            image.resize((previewSize, previewSize), Image.NEAREST), master=self.root
        )
        self.labelPreview.config(image=self.previewImg)
        self.labelPreview.image = self.previewImg

    def updatePreview(self) -> None:
        self.previewJob = None

        def statusError(message: str) -> None:
            self.setStatus(message)

        image, _, missingAssets = self.buildImage(requireNameForNew=False, errorCallback=statusError)
        if image is None:
            blank = Image.new("RGBA", (imageSize, imageSize), (255, 255, 255, 0))
            self._setPreviewImage(blank)
            return

        if not missingAssets:
            self.setStatus("")

        if self.overlayVar.get():
            overlayImg = self._loadOverlayImage()
            if overlayImg is not None:
                composite = overlayImg.copy()
                composite.paste(image, (0, 0), image)
                image = composite

        self._setPreviewImage(image)

    def pasteFromClipboard(self) -> None:
        try:
            clip = ImageGrab.grabclipboard()
        except Exception:
            clip = None

        if clip is None and sys.platform.startswith("linux"):
            clip = _linuxClipboardImage()

        metadata = None
        loaded = False
        tempImage = None

        if isinstance(clip, Image.Image):
            metadata = getattr(clip, "info", None)
            tempImage = clip.convert("RGBA")
            loaded = True
        elif isinstance(clip, (list, tuple)) and clip:
            path = clip[0]
            if isinstance(path, str) and os.path.isfile(path):
                try:
                    with Image.open(path) as img:
                        metadata = img.info
                        tempImage = img.convert("RGBA")
                    loaded = True
                except Exception as exc:
                    messagebox.showerror("Error", str(exc))
                    return

        if not loaded:
            messagebox.showerror("Error", "No image found in clipboard.")
            return

        applied = False
        loadoutImported = False
        if isinstance(metadata, dict):
            payload = metadata.get("ribbonengine")
            if isinstance(payload, str):
                try:
                    parsed = json.loads(payload)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    # kind:"loadout" PNGs apply directly. To keep one
                    # permanently, drop the file into the `loadouts/` folder.
                    try:
                        applied = self.applyMetadata(parsed)
                    except Exception:
                        applied = False

        if applied:
            self.baseImage = None
        else:
            self.baseImage = tempImage

        self.schedulePreview()
        if applied:
            self._toast("Loaded image + ribbon metadata")
        else:
            self._toast("Loaded image (no metadata found)")

    def generateImage(self) -> None:
        missing = []
        if not os.path.isdir(assetsRoot):
            missing.append(assetsRoot)
        if not os.path.isdir(charactersDir):
            missing.append(charactersDir)
        if missing:
            messagebox.showerror("Error", "Missing folder(s):\n" + "\n".join(missing))
            return

        def showError(message: str) -> None:
            messagebox.showerror("Error", message)

        image, _, _ = self.buildImage(requireNameForNew=True, errorCallback=showError)
        if image is None:
            return

        self.schedulePreview()
        self.setStatus("")

        os.makedirs(ribbonOutputDir, exist_ok=True)
        savePath = os.path.join(ribbonOutputDir, _defaultOutputFilename(self.entry.get()))
        try:
            metadata = self.buildMetadata()
            pnginfo = PngImagePlugin.PngInfo()
            pnginfo.add_text("ribbonengine", json.dumps(metadata, separators=(",", ":")))
            _atomicSaveImage(image, savePath, pnginfo=pnginfo)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return

        self._toast(f"Saved {os.path.basename(savePath)}")
        self._rememberRecentFile(savePath)

    def copyImageToClipboard(self) -> None:
        """Render the full-size composite and copy it to the system clipboard.

        Uses the *exported* size — i.e. the same image `generateImage`
        would write — not the on-screen scaled preview. The would-be
        filename is logged to stdout/the status bar so the user has a
        record of what they copied (handy when shipping screenshots in
        a thread where filenames otherwise vanish).

        Best-effort cross-platform copy:
          - Windows: PIL BMP-to-DIB via Tk (no extra deps).
          - macOS:   osascript reading the PNG file.
          - Linux:   wl-copy (Wayland) or xclip (X11), whichever is on PATH.
        Falls back to copying just the file path if no image-clipboard
        path works, so the user can paste it in a file picker as a
        consolation prize.
        """
        if not os.path.isdir(assetsRoot) or not os.path.isdir(charactersDir):
            messagebox.showerror("Copy to clipboard", "Missing assets/ or Characters/ folder.")
            return

        def showError(message: str) -> None:
            messagebox.showerror("Copy to clipboard", message)

        image, _, _ = self.buildImage(requireNameForNew=False, errorCallback=showError)
        if image is None:
            return

        filename = _defaultOutputFilename(self.entry.get())
        size_str = f"{image.width}x{image.height}"
        log_line = f"[clipboard] {filename} ({size_str})"
        print(log_line)
        self.setStatus(log_line)

        # Write to a temp PNG (some clipboard mechanisms need a path).
        fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="re3_clip_")
        os.close(fd)
        try:
            image.save(tmp_path, format="PNG")
            copied = self._copyImageFileToClipboard(tmp_path, image)
        except Exception as exc:
            messagebox.showerror("Copy to clipboard", f"Render failed: {exc}")
            return
        finally:
            # Some clipboard backends read lazily; keep the file around
            # for a few seconds before deleting. Easiest portable trick:
            # schedule deletion on the Tk main loop.
            self.root.after(5000, lambda p=tmp_path: self._safeUnlink(p))

        if copied:
            self._toast(f"Copied image to clipboard ({size_str}) — {filename}")
        else:
            # Fall back to copying the filename as text so the user gets
            # *something* on the clipboard.
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(tmp_path)
                self.root.update()
                self._toast(f"Couldn't put image on clipboard; copied path instead ({filename})")
            except tk.TclError:
                self._toast("Couldn't copy to clipboard.")

    def _safeUnlink(self, path: str) -> None:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass

    def _copyImageFileToClipboard(self, path: str, image: Image.Image) -> bool:
        """Platform-specific image-to-clipboard. Returns True on success."""
        platform = sys.platform
        try:
            if platform.startswith("win"):
                # Windows clipboard via win32clipboard if available; else
                # PowerShell as a fallback (ships with every modern Windows).
                try:
                    import win32clipboard  # type: ignore
                    import io as _io
                    output = _io.BytesIO()
                    image.convert("RGB").save(output, "BMP")
                    data = output.getvalue()[14:]  # drop BMP file header → DIB
                    win32clipboard.OpenClipboard()
                    try:
                        win32clipboard.EmptyClipboard()
                        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
                    finally:
                        win32clipboard.CloseClipboard()
                    return True
                except Exception:
                    ps = (
                        "Add-Type -AssemblyName System.Windows.Forms; "
                        "Add-Type -AssemblyName System.Drawing; "
                        f"$img = [System.Drawing.Image]::FromFile('{path}'); "
                        "[System.Windows.Forms.Clipboard]::SetImage($img); "
                        "$img.Dispose()"
                    )
                    result = subprocess.run(
                        ["powershell", "-NoProfile", "-Command", ps],
                        capture_output=True, timeout=10,
                    )
                    return result.returncode == 0

            if platform == "darwin":
                script = (
                    f'set the clipboard to (read POSIX file "{path}" as «class PNGf»)'
                )
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True, timeout=10,
                )
                return result.returncode == 0

            # Linux / *BSD
            if shutil.which("wl-copy"):
                with open(path, "rb") as fh:
                    result = subprocess.run(
                        ["wl-copy", "--type", "image/png"],
                        stdin=fh, capture_output=True, timeout=10,
                    )
                if result.returncode == 0:
                    return True
            if shutil.which("xclip"):
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-t", "image/png", "-i", path],
                    capture_output=True, timeout=10,
                )
                if result.returncode == 0:
                    return True
            if shutil.which("xsel"):
                with open(path, "rb") as fh:
                    result = subprocess.run(
                        ["xsel", "--clipboard", "--input"],
                        stdin=fh, capture_output=True, timeout=10,
                    )
                return result.returncode == 0
        except Exception as exc:
            print(f"[clipboard] copy failed: {exc}")
        return False

    def clearAll(self) -> None:
        self._captureHistory()
        self.baseImage = None

        self.entry.delete(0, tk.END)
        for var in self.checkboxVars.values():
            var.set(0)
        self.customOffsets.clear()

        # Reset every StringVar that drives a dropdown (single + medal slots).
        for varList in (getattr(self, "medalAwardVars", []), getattr(self, "medalBonusVars", [])):
            for v in varList:
                v.set("NONE")
        for section in self.categorySections:
            if section.toggle is None:  # dropdown / slot section
                for item in section.items:
                    widget = item["widget"]
                    if isinstance(widget, ttk.Combobox):
                        widget.set("NONE")
        if hasattr(self, "useDepartmentBadgeVar"):
            self.useDepartmentBadgeVar.set(False)
        if hasattr(self, "departmentBadgeVar"):
            self.departmentBadgeVar.set("NONE")
        self._applyBadgeAwardVisibility()
        if hasattr(self, "manualRibbonSlots"):
            self.manualRibbonSlots.clear()
            if hasattr(self, "manualSlotColors"):
                self.manualSlotColors.clear()
            self._manualSelectedSlot = None
            self._redrawManualGrid()

        self.setStatus("")
        if self.showSelectedVar.get():
            self.applyFilter()
        self.schedulePreview()

    # ------------------------------------------------------------------ Asset validator
    def _runAssetValidator(self) -> None:
        """Warn about real asset problems — never about JSON allowlist gaps.

        The filesystem is the allowlist, so a PNG simply not being on disk is
        normal and is NOT flagged. We only surface genuine problems:
          1. Stray `no_recolor` / `descriptions` keys in a faction JSON that
             name an asset which doesn't exist (a likely typo).
          2. Windows-illegal characters (`<>:"/\\|?*`) in an on-disk filename —
             these break extraction and PNG save on Windows, so they're flagged
             regardless of which OS the engine runs on.
          3. Byte-identical duplicate PNGs across faction trees.
        """
        if self.factionRegistry is None:
            return
        available: dict[str, set[str]] = {}
        for category, items in self.ribbonGroups.items():
            available[category] = {item.name for item in items}
        if os.path.isdir(assetsRoot):
            for factionKey in os.listdir(assetsRoot):
                facDir = os.path.join(assetsRoot, factionKey)
                if not os.path.isdir(facDir):
                    continue
                for sub in ASSET_SUBDIRS:
                    subDir = os.path.join(facDir, sub)
                    if not os.path.isdir(subDir):
                        continue
                    stems = {
                        os.path.splitext(name)[0]
                        for name in os.listdir(subDir)
                        if name.lower().endswith(".png")
                    }
                    available.setdefault(sub, set()).update(stems)

        illegalAssets, duplicates = scanAssetTree()
        warnings = self.factionRegistry.validate_assets(available)
        for entry in illegalAssets:
            warnings.append(f"Windows-illegal filename: {entry}")
        for digest, paths in duplicates.items():
            warnings.append(
                f"Duplicate content (sha256 {digest[:10]}): {', '.join(sorted(paths))}"
            )
        if warnings:
            # Don't block startup — surface as a non-modal status message; full
            # text goes to stdout for distributors who run from a terminal.
            for line in warnings:
                print(f"[asset-validator] {line}")
            self.root.after(200, lambda: self.setStatus(
                f"Asset warnings: {len(warnings)} (see terminal for details)"
            ))

    # ------------------------------------------------------------------ Loadouts
    def _listLoadoutNames(self) -> list[str]:
        """Every loadout stem under loadouts/, JSON or PNG.

        Users can either save loadouts here through the GUI (writes JSON) or
        drop an exported loadout PNG straight into the folder. Both show up
        in the dialog; PNG names are de-duped against JSON of the same stem
        so a converted loadout doesn't appear twice.
        """
        if not os.path.isdir(loadoutsDir):
            return []
        stems: dict[str, str] = {}
        for filename in os.listdir(loadoutsDir):
            lower = filename.lower()
            if lower.endswith(".json"):
                stems[os.path.splitext(filename)[0]] = "json"
            elif lower.endswith(".png") and os.path.splitext(filename)[0] not in stems:
                stems[os.path.splitext(filename)[0]] = "png"
        return sorted(stems.keys(), key=str.lower)

    def _loadoutPath(self, name: str) -> str:
        """Path to save a NEW loadout under `name`. Always writes `.json`."""
        safe = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_", " ")).strip()
        if not safe:
            safe = "loadout"
        return os.path.join(loadoutsDir, f"{safe}.json")

    def _resolveLoadoutFile(self, name: str) -> Optional[str]:
        """Find an existing loadout file by stem, JSON preferred over PNG.

        Names come from `_listLoadoutNames`, which returns *raw* file stems —
        including characters like the `[` `]` in exported `[name]_[stamp].png`
        loadouts. So we match the exact on-disk stem first; only if that misses
        do we fall back to the sanitized stem (loadouts saved through the GUI's
        Name field, whose filenames were sanitized by `_loadoutPath`). Without
        the exact pass, any bracketed export would be unloadable even though the
        file is sitting right there in loadouts/.
        """
        # Guard against path traversal — `name` may originate from a text field.
        if not (os.sep in name or (os.altsep and os.altsep in name) or name in (".", "..")):
            for ext in (".json", ".png"):
                exact = os.path.join(loadoutsDir, f"{name}{ext}")
                if os.path.exists(exact):
                    return exact
        json_path = self._loadoutPath(name)
        if os.path.exists(json_path):
            return json_path
        safe = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_", " ")).strip() or "loadout"
        png_path = os.path.join(loadoutsDir, f"{safe}.png")
        if os.path.exists(png_path):
            return png_path
        return None

    def _saveLoadout(self, name: str) -> bool:
        try:
            os.makedirs(loadoutsDir, exist_ok=True)
            awards = [v.get() for v in getattr(self, "medalAwardVars", [])]
            bonuses = [v.get() for v in getattr(self, "medalBonusVars", [])]
            useBadge = bool(getattr(self, "useDepartmentBadgeVar", None) and self.useDepartmentBadgeVar.get())
            badge = self.departmentBadgeVar.get() if (useBadge and hasattr(self, "departmentBadgeVar")) else ""
            payload = {
                "version": METADATA_SCHEMA_VERSION,
                "profile": self.profileName,
                "ribbons": self.getSelectedRibbonNames(),
                "nameplate": self.entry.get().strip(),
                "faction": self.activeFactionKey,
                "awards": awards,
                "bonuses": bonuses,
                "department_badge": badge,
                "manual_slots": {str(k): v for k, v in self.manualRibbonSlots.items()},
                "manual_slot_colors": {str(k): dict(v) for k, v in self.manualSlotColors.items()},
                "custom_offsets": {n: list(v) for n, v in self.customOffsets.items()},
            }
            _atomicWriteText(self._loadoutPath(name), json.dumps(payload, indent=2))
            self.currentLoadoutName = name
            self._updateLoadoutLabel()
            return True
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return False

    def _loadLoadout(self, name: str) -> bool:
        path = self._resolveLoadoutFile(name)
        if path is None:
            messagebox.showerror("Load failed", f"No loadout named {name!r} in {loadoutsDir}")
            return False
        payload: Optional[dict] = None
        try:
            if path.lower().endswith(".png"):
                # Shared loadout PNG dropped directly into loadouts/. Extract
                # the embedded ribbonengine metadata and treat it as a JSON
                # payload.
                payload = self._readRibbonengineMetadata(path)
                if payload is None:
                    messagebox.showerror(
                        "Load failed",
                        f"{os.path.basename(path)} has no Ribbon Engine metadata.\n"
                        "Only PNGs exported by the engine can be used as loadouts.",
                    )
                    return False
            else:
                with open(path, "r", encoding="utf-8-sig") as handle:
                    payload = json.load(handle)
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            return False
        if not isinstance(payload, dict):
            return False

        self._captureHistory()
        self._suppressHistory = True
        try:
            self.applyMetadata(payload)
            offsets_raw = payload.get("custom_offsets", {})
            self.customOffsets = {}
            if isinstance(offsets_raw, dict):
                for n, v in offsets_raw.items():
                    if isinstance(n, str) and isinstance(v, (list, tuple)) and len(v) == 2:
                        try:
                            self.customOffsets[n] = (int(v[0]), int(v[1]))
                        except Exception:
                            continue
            self.currentLoadoutName = name
        finally:
            self._suppressHistory = False
        self.schedulePreview()
        return True

    def _deleteLoadout(self, name: str) -> bool:
        path = self._resolveLoadoutFile(name)
        if path is None:
            return False
        try:
            os.remove(path)
            if self.currentLoadoutName == name:
                self.currentLoadoutName = ""
            self.loadoutFavorites.discard(name)
            self.saveCurrentSettings()
            self._updateLoadoutLabel()
            return True
        except Exception as exc:
            messagebox.showerror("Delete failed", str(exc))
            return False

    def _renderLoadoutThumbnail(self, name: str, size: int = 96) -> Optional[ImageTk.PhotoImage]:
        """Render a small preview image for a saved loadout JSON.

        Thumbnails are lazy — only built when the loadout dialog is open —
        and cached on `self._loadoutThumbCache` so reopening the dialog is
        instant. Failures fall back to None and the row renders without
        an image.
        """
        cache = getattr(self, "_loadoutThumbCache", None)
        if cache is None:
            cache = {}
            self._loadoutThumbCache = cache
        if name in cache:
            return cache[name]
        path = self._resolveLoadoutFile(name)
        if path is None:
            cache[name] = None
            return None
        # PNG loadouts are already rendered images — just open and downscale,
        # no need to re-run the renderer.
        if path.lower().endswith(".png"):
            try:
                with Image.open(path) as src:
                    image = src.convert("RGBA")
            except Exception:
                cache[name] = None
                return None
            scaled = self._centeredPreview(image, size, (0, 0, 0, 0))
            photo = ImageTk.PhotoImage(scaled, master=self.root)
            cache[name] = photo
            return photo
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        except Exception:
            cache[name] = None
            return None
        if not isinstance(payload, dict):
            cache[name] = None
            return None
        ribbonNames = set(payload.get("ribbons", []) or [])
        nameplate = str(payload.get("nameplate", "") or "")
        faction = str(payload.get("faction", "") or self.activeFactionKey)
        try:
            image, _, _ = self.renderer.buildImage(
                selectedNames=ribbonNames,
                nameplateText=nameplate,
                baseImage=None,
                requireNameForNew=False,
                errorCallback=None,
                faction=faction,
                customOffsets={},
                placements=None,
                manualRibbonSlots=None,
                awardSlots=None,
                bonusSlots=None,
                departmentBadge=None,
            )
        except Exception:
            cache[name] = None
            return None
        if image is None:
            cache[name] = None
            return None
        thumb = image.resize((size, size), Image.NEAREST)
        photo = ImageTk.PhotoImage(thumb, master=self.root)
        cache[name] = photo
        return photo

    def _openImportUrlDialog(self) -> None:
        """Prompt for a URL and import a loadout PNG from it.

        Downloads to a temp file, then runs it through the regular
        `_loadImageFromPath` path so embedded `ribbonengine` metadata
        applies just like a drag-and-dropped PNG. Only http(s) is
        accepted; the response is capped at 8 MB to keep a hostile
        URL from filling the disk.
        """
        url = simpledialog.askstring("Import from URL", "Paste a loadout PNG URL:", parent=self.root)
        if not url:
            return
        url = url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            messagebox.showerror("Import from URL", "URL must start with http:// or https://")
            return
        tmp_path = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RibbonEngine/3"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                messagebox.showerror("Import from URL", "Download exceeded 8 MB; aborting.")
                return
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                messagebox.showerror("Import from URL", "That URL didn't return a PNG file.")
                return
            fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="re3_url_")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            self._loadImageFromPath(tmp_path)
        except Exception as exc:
            messagebox.showerror("Import from URL", f"Couldn't fetch URL:\n{exc}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # ---- In-app ribbon editor (sidecar defaults) ------------------------
    def _openRibbonEditorDialog(self) -> None:
        """Edit a ribbon's default sidecar — recolor toggles + base palette.

        This isn't a pixel editor; it edits `<ribbon>.meta.json` for the
        chosen PNG. Use it when a ribbon needs a different *default* look
        than the active faction's palette would produce, or to mark it
        `no_recolor` (bespoke art). Per-slot overrides set in the
        Ribbon Placement panel take precedence over these defaults.
        """
        if not os.path.isdir(assetsRoot):
            messagebox.showerror("Ribbon editor", "No assets/ directory.")
            return
        factions = sorted(
            f for f in os.listdir(assetsRoot)
            if os.path.isdir(os.path.join(assetsRoot, f, "ribbons"))
        )
        if not factions:
            messagebox.showerror("Ribbon editor", "No factions with ribbons under assets/.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Ribbon Editor")
        dialog.transient(self.root)
        dialog.configure(background=self.theme["bg"])
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)

        factionVar = tk.StringVar(value=self.activeFactionKey if self.activeFactionKey in factions else factions[0])
        ribbonVar = tk.StringVar()

        ttk.Label(frame, text="Faction:").grid(row=0, column=0, sticky="w")
        factionCombo = ttk.Combobox(frame, textvariable=factionVar, values=factions, state="readonly", width=18)
        factionCombo.grid(row=0, column=1, columnspan=2, sticky="ew", pady=2)

        ttk.Label(frame, text="Ribbon:").grid(row=1, column=0, sticky="w")
        ribbonCombo = ttk.Combobox(frame, textvariable=ribbonVar, state="readonly", width=28)
        ribbonCombo.grid(row=1, column=1, columnspan=2, sticky="ew", pady=2)

        noRecolorVar = tk.IntVar(value=0)
        ttk.Checkbutton(frame, text="Never recolor this ribbon (bespoke art)", variable=noRecolorVar).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 4)
        )

        toggleVars: dict[str, tk.IntVar] = {}
        colorVars: dict[str, tk.StringVar] = {}
        for i, region in enumerate(("border", "stripe", "base")):
            row = 3 + i
            tv = tk.IntVar(value=1)
            cv = tk.StringVar()
            toggleVars[region] = tv
            colorVars[region] = cv
            ttk.Checkbutton(frame, text=f"Recolor {region}", variable=tv).grid(row=row, column=0, sticky="w")
            ttk.Label(frame, text="#").grid(row=row, column=1, sticky="e")
            vcmd = (self.root.register(self._validateHexEntry), "%P")
            ttk.Entry(frame, textvariable=cv, width=8, validate="key", validatecommand=vcmd).grid(row=row, column=2, sticky="w")

        previewLabel = ttk.Label(frame, text="", foreground=self.theme.get("status", "#888"))
        previewLabel.grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))

        def refresh_ribbons(*_):
            fac = factionVar.get()
            rdir = os.path.join(assetsRoot, fac, "ribbons")
            choices = sorted(
                os.path.splitext(n)[0]
                for n in (os.listdir(rdir) if os.path.isdir(rdir) else [])
                if n.lower().endswith(".png")
            )
            ribbonCombo["values"] = choices
            if choices:
                ribbonVar.set(choices[0])
            else:
                ribbonVar.set("")

        def load_current(*_):
            fac, stem = factionVar.get(), ribbonVar.get().strip()
            if not fac or not stem:
                return
            path = os.path.join(assetsRoot, fac, "ribbons", f"{stem}.png")
            sidecar = _readRibbonSidecar(path)
            noRecolorVar.set(1 if sidecar.get("no_recolor") else 0)
            recolor = sidecar.get("recolor") if isinstance(sidecar.get("recolor"), dict) else {}
            colors = sidecar.get("colors") if isinstance(sidecar.get("colors"), dict) else {}
            for region in ("border", "stripe", "base"):
                toggleVars[region].set(1 if recolor.get(region, True) else 0)
                hexv = str(colors.get(region, "") or "").lstrip("#")
                colorVars[region].set(hexv[:6])
            previewLabel.config(text=f"Editing {os.path.relpath(path, baseDir)}")

        factionVar.trace_add("write", refresh_ribbons)
        ribbonVar.trace_add("write", load_current)
        refresh_ribbons()
        load_current()

        def do_save():
            fac, stem = factionVar.get(), ribbonVar.get().strip()
            if not fac or not stem:
                return
            path = os.path.join(assetsRoot, fac, "ribbons", f"{stem}.png")
            data: dict = {}
            if noRecolorVar.get():
                data["no_recolor"] = True
            else:
                recolor = {r: bool(toggleVars[r].get()) for r in ("border", "stripe", "base")}
                # Only write a `recolor` block if it overrides defaults.
                if not all(recolor.values()):
                    data["recolor"] = recolor
                colors: dict[str, str] = {}
                for region in ("border", "stripe", "base"):
                    hv = colorVars[region].get().strip().lstrip("#")
                    if len(hv) == 6 and all(c in "0123456789abcdefABCDEF" for c in hv):
                        colors[region] = f"#{hv.lower()}"
                if colors:
                    data["colors"] = colors
            try:
                writeRibbonSidecar(path, data)
                invalidateRibbonCache(path)
            except Exception as exc:
                messagebox.showerror("Ribbon editor", f"Save failed: {exc}", parent=dialog)
                return
            self._toast(f"Saved sidecar for {stem}")
            self.schedulePreview()

        def do_clear():
            fac, stem = factionVar.get(), ribbonVar.get().strip()
            if not fac or not stem:
                return
            path = os.path.join(assetsRoot, fac, "ribbons", f"{stem}.png")
            try:
                writeRibbonSidecar(path, {})
                invalidateRibbonCache(path)
            except Exception as exc:
                messagebox.showerror("Ribbon editor", f"Clear failed: {exc}", parent=dialog)
                return
            load_current()
            self._toast(f"Cleared sidecar for {stem}")
            self.schedulePreview()

        btnRow = ttk.Frame(frame)
        btnRow.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Button(btnRow, text="Save", command=do_save).pack(side="left", padx=2)
        ttk.Button(btnRow, text="Clear sidecar", command=do_clear).pack(side="left", padx=2)
        ttk.Button(btnRow, text="Close", command=dialog.destroy).pack(side="right", padx=2)

        frame.columnconfigure(2, weight=1)
        dialog.grab_set()
        dialog.focus_set()

    # ---- Faction pack export / import -----------------------------------
    def _exportFactionPack(self) -> None:
        """Bundle one faction's JSON + asset tree into a portable ZIP.

        Contents:
          - `factions/<KEY>.json` (or extracted from `factions.json` if
            this project still uses the legacy aggregated file)
          - `assets/<KEY>/**` (all subdirs: ribbons, awards, etc., plus
            any `.meta.json` sidecars next to ribbons)
          - `pack.json` — a small manifest with the faction key + version
        """
        if not self.factionRegistry:
            messagebox.showerror("Faction pack", "No factions loaded.")
            return
        keys = list(self.factionRegistry.names())
        if not keys:
            messagebox.showerror("Faction pack", "No factions to export.")
            return
        default_key = self.activeFactionKey if self.activeFactionKey in keys else keys[0]
        key = simpledialog.askstring(
            "Export faction pack",
            f"Faction key to export ({', '.join(keys)}):",
            initialvalue=default_key,
            parent=self.root,
        )
        if not key:
            return
        key = key.strip()
        if key not in keys:
            messagebox.showerror("Faction pack", f"Unknown faction {key!r}")
            return
        out_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save faction pack",
            initialfile=f"{key}_pack.zip",
            defaultextension=".zip",
            filetypes=[("Faction pack (ZIP)", "*.zip"), ("All files", "*.*")],
        )
        if not out_path:
            return

        # Resolve the faction's JSON: prefer per-faction file, fall back
        # to slicing it out of an aggregated factions.json.
        faction_json_bytes: Optional[bytes] = None
        per_path = os.path.join(baseDir, "factions", f"{key}.json")
        if os.path.isfile(per_path):
            with open(per_path, "rb") as fh:
                faction_json_bytes = fh.read()
        else:
            agg_path = os.path.join(baseDir, "factions.json")
            if os.path.isfile(agg_path):
                try:
                    with open(agg_path, "r", encoding="utf-8-sig") as fh:
                        agg = json.load(fh)
                    spec = (agg.get("factions") or {}).get(key)
                    if spec is not None:
                        faction_json_bytes = json.dumps(spec, indent=2).encode("utf-8")
                except Exception as exc:
                    messagebox.showerror("Faction pack", f"Couldn't read factions.json: {exc}")
                    return

        asset_dir = os.path.join(assetsRoot, key)
        if faction_json_bytes is None and not os.path.isdir(asset_dir):
            messagebox.showerror("Faction pack", f"No config or assets found for {key!r}.")
            return

        manifest = {
            "kind": "ribbonengine-faction-pack",
            "version": 1,
            "faction_key": key,
            "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
        tmp_path = out_path + ".tmp"
        try:
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("pack.json", json.dumps(manifest, indent=2))
                if faction_json_bytes is not None:
                    zf.writestr(f"factions/{key}.json", faction_json_bytes)
                if os.path.isdir(asset_dir):
                    for root, _dirs, files in os.walk(asset_dir):
                        for fname in files:
                            full = os.path.join(root, fname)
                            rel = os.path.relpath(full, baseDir).replace(os.sep, "/")
                            zf.write(full, rel)
            os.replace(tmp_path, out_path)
        except Exception as exc:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            messagebox.showerror("Faction pack", f"Export failed: {exc}")
            return
        self._toast(f"Exported {key} pack")

    def _importFactionPack(self) -> None:
        """Install a faction pack ZIP into this project.

        Validates the manifest, refuses paths that would escape the
        project (zip-slip guard), warns before overwriting existing
        files, and reloads assets when done.
        """
        zip_path = filedialog.askopenfilename(
            parent=self.root,
            title="Import faction pack",
            filetypes=[("Faction pack (ZIP)", "*.zip"), ("All files", "*.*")],
        )
        if not zip_path:
            return
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                try:
                    manifest = json.loads(zf.read("pack.json").decode("utf-8"))
                except KeyError:
                    messagebox.showerror("Faction pack", "Missing pack.json in archive.")
                    return
                if manifest.get("kind") != "ribbonengine-faction-pack":
                    messagebox.showerror("Faction pack", "Not a Ribbon Engine faction pack.")
                    return
                key = str(manifest.get("faction_key") or "").strip()
                if not key:
                    messagebox.showerror("Faction pack", "Pack manifest is missing faction_key.")
                    return

                # Zip-slip guard: every entry must resolve inside baseDir
                # and live under either factions/ or assets/<KEY>/.
                base_real = os.path.realpath(baseDir)
                planned: list[tuple[str, str]] = []
                for info in zf.infolist():
                    name = info.filename
                    if name.endswith("/") or name == "pack.json":
                        continue
                    norm = name.replace("\\", "/")
                    if norm.startswith("/") or ".." in norm.split("/"):
                        messagebox.showerror("Faction pack", f"Unsafe path in archive: {name}")
                        return
                    allowed = (
                        norm == f"factions/{key}.json"
                        or norm.startswith(f"assets/{key}/")
                    )
                    if not allowed:
                        messagebox.showerror(
                            "Faction pack",
                            f"Archive contains unexpected path: {name}\n"
                            f"(expected only factions/{key}.json or assets/{key}/…)",
                        )
                        return
                    target = os.path.realpath(os.path.join(baseDir, norm))
                    if not target.startswith(base_real + os.sep) and target != base_real:
                        messagebox.showerror("Faction pack", f"Unsafe path in archive: {name}")
                        return
                    planned.append((name, target))

                overwrites = [t for _n, t in planned if os.path.exists(t)]
                if overwrites:
                    if not messagebox.askyesno(
                        "Faction pack",
                        f"This pack will overwrite {len(overwrites)} existing file(s) for "
                        f"faction {key!r}. Continue?",
                        parent=self.root,
                    ):
                        return

                for name, target in planned:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    data = zf.read(name)
                    tmp = target + ".tmp"
                    with open(tmp, "wb") as fh:
                        fh.write(data)
                    os.replace(tmp, target)
        except zipfile.BadZipFile:
            messagebox.showerror("Faction pack", "That file isn't a valid ZIP.")
            return
        except Exception as exc:
            messagebox.showerror("Faction pack", f"Import failed: {exc}")
            return

        self._toast(f"Imported {key} pack")
        try:
            self.reloadAssets()
        except Exception as exc:
            messagebox.showerror("Faction pack", f"Reload failed: {exc}")

    def _openLoadoutDialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Loadouts")
        dialog.transient(self.root)
        dialog.configure(background=self.theme["bg"])

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Saved loadouts", style="Section.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6)
        )

        ttk.Label(frame, text="Search:").grid(row=1, column=0, sticky="w")
        searchVar = tk.StringVar()
        searchEntry = ttk.Entry(frame, textvariable=searchVar, width=22)
        searchEntry.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(0, 6))

        # Scrollable list of loadout rows (thumbnail + name + load/delete).
        listFrame = ttk.Frame(frame)
        listFrame.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(0, 8))
        canvas = tk.Canvas(
            listFrame,
            highlightthickness=0,
            bg=self.theme["panel_bg"],
            height=320,
        )
        vsb = ttk.Scrollbar(listFrame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        rowsFrame = ttk.Frame(canvas)
        rowsFrame.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=rowsFrame, anchor="nw")
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        ttk.Label(frame, text="Name:").grid(row=3, column=0, sticky="w")
        nameVar = tk.StringVar(value=self.currentLoadoutName)
        nameEntry = ttk.Entry(frame, textvariable=nameVar, width=22)
        nameEntry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(0, 8))

        selectedVar = tk.StringVar(value=self.currentLoadoutName)

        def do_load_named(loadoutName: str) -> None:
            if not loadoutName:
                return
            self._loadLoadout(loadoutName)
            dialog.destroy()

        def do_delete_named(loadoutName: str) -> None:
            if messagebox.askyesno(
                "Delete loadout", f"Delete {loadoutName!r}?", parent=dialog
            ):
                self._deleteLoadout(loadoutName)
                refresh()

        def refresh():
            for child in rowsFrame.winfo_children():
                child.destroy()
            query = searchVar.get().strip().lower()
            all_names = [n for n in self._listLoadoutNames() if query in n.lower()]
            # Favorites first, then alphabetical within each group.
            favs = sorted([n for n in all_names if n in self.loadoutFavorites], key=str.lower)
            rest = sorted([n for n in all_names if n not in self.loadoutFavorites], key=str.lower)
            names = favs + rest
            if not names:
                ttk.Label(
                    rowsFrame, text="(no loadouts match)", foreground=self.theme.get("status", "#888")
                ).pack(anchor="w", padx=6, pady=8)
                return
            for n in names:
                row = ttk.Frame(rowsFrame)
                row.pack(fill="x", pady=2, padx=2)
                thumb = self._renderLoadoutThumbnail(n)
                if thumb is not None:
                    thumbLabel = tk.Label(
                        row,
                        image=thumb,
                        bg=self.theme["panel_bg"],
                        borderwidth=0,
                    )
                    thumbLabel.image = thumb  # keep Tk's GC away
                    thumbLabel.pack(side="left", padx=(0, 8))
                starText = "★" if n in self.loadoutFavorites else "☆"
                def toggle_fav(nm=n):
                    if nm in self.loadoutFavorites:
                        self.loadoutFavorites.discard(nm)
                    else:
                        self.loadoutFavorites.add(nm)
                    self.saveCurrentSettings()
                    refresh()
                ttk.Button(row, text=starText, width=3, command=toggle_fav).pack(side="left", padx=(0, 4))
                ttk.Label(row, text=n, width=18, anchor="w").pack(side="left", fill="x", expand=True)
                ttk.Button(row, text="Load", width=6, command=lambda nm=n: do_load_named(nm)).pack(side="left", padx=2)
                ttk.Button(row, text="Del", width=4, command=lambda nm=n: do_delete_named(nm)).pack(side="left", padx=2)

                def on_row_click(_evt, nm=n):
                    nameVar.set(nm)
                    selectedVar.set(nm)

                row.bind("<Button-1>", on_row_click)
                for child in row.winfo_children():
                    if not isinstance(child, ttk.Button):
                        child.bind("<Button-1>", on_row_click)

        searchVar.trace_add("write", lambda *_: refresh())
        refresh()

        def do_save():
            name = nameVar.get().strip()
            if not name:
                return
            if self._saveLoadout(name):
                getattr(self, "_loadoutThumbCache", {}).pop(name, None)
                refresh()
                self._toast(f"Saved loadout {name!r}")

        def do_load():
            name = nameVar.get().strip() or selectedVar.get().strip()
            if name:
                do_load_named(name)

        def do_delete():
            name = nameVar.get().strip()
            if name:
                do_delete_named(name)

        ttk.Button(frame, text="Save", command=do_save).grid(row=4, column=0, sticky="ew", padx=2)
        ttk.Button(frame, text="Load", command=do_load).grid(row=4, column=1, sticky="ew", padx=2)
        ttk.Button(frame, text="Delete", command=do_delete).grid(row=4, column=2, sticky="ew", padx=2)

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)
        dialog.grab_set()
        dialog.focus_set()
        searchEntry.focus_set()

    # ------------------------------------------------------------------ Move/rename ribbon
    def _openMoveRibbonDialog(self) -> None:
        """Move or rename a ribbon PNG (and its sidecar) between factions.

        UI: pick source faction → pick ribbon → pick target faction → optional
        new stem. Performs the rename on disk, invalidates caches, reloads
        assets so the change shows up immediately.
        """
        if not os.path.isdir(assetsRoot):
            messagebox.showerror("Move ribbon", "No assets/ directory.")
            return

        factions = sorted(
            f for f in os.listdir(assetsRoot)
            if os.path.isdir(os.path.join(assetsRoot, f))
        )
        if not factions:
            messagebox.showerror("Move ribbon", "No faction folders under assets/.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Move/Rename Ribbon")
        dialog.transient(self.root)
        dialog.configure(background=self.theme["bg"])
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)

        srcFactionVar = tk.StringVar(value=self.activeFactionKey if self.activeFactionKey in factions else factions[0])
        ribbonVar = tk.StringVar()
        dstFactionVar = tk.StringVar(value=srcFactionVar.get())
        renameVar = tk.StringVar()

        ttk.Label(frame, text="From faction:").grid(row=0, column=0, sticky="w")
        srcCombo = ttk.Combobox(frame, textvariable=srcFactionVar, values=factions, state="readonly", width=18)
        srcCombo.grid(row=0, column=1, sticky="ew", pady=2)

        ttk.Label(frame, text="Ribbon:").grid(row=1, column=0, sticky="w")
        ribbonCombo = ttk.Combobox(frame, textvariable=ribbonVar, state="readonly", width=28)
        ribbonCombo.grid(row=1, column=1, sticky="ew", pady=2)

        ttk.Label(frame, text="To faction:").grid(row=2, column=0, sticky="w")
        dstCombo = ttk.Combobox(frame, textvariable=dstFactionVar, values=factions, state="readonly", width=18)
        dstCombo.grid(row=2, column=1, sticky="ew", pady=2)

        ttk.Label(frame, text="New name (optional):").grid(row=3, column=0, sticky="w")
        ttk.Entry(frame, textvariable=renameVar, width=28).grid(row=3, column=1, sticky="ew", pady=2)

        def refresh_ribbons(*_):
            src = os.path.join(assetsRoot, srcFactionVar.get(), "ribbons")
            choices = sorted(
                os.path.splitext(n)[0]
                for n in (os.listdir(src) if os.path.isdir(src) else [])
                if n.lower().endswith(".png")
            )
            ribbonCombo["values"] = choices
            if choices:
                ribbonVar.set(choices[0])
            else:
                ribbonVar.set("")

        srcFactionVar.trace_add("write", refresh_ribbons)
        refresh_ribbons()

        def do_move():
            stem = ribbonVar.get().strip()
            srcF, dstF = srcFactionVar.get(), dstFactionVar.get()
            if not stem:
                messagebox.showerror("Move ribbon", "Pick a ribbon.", parent=dialog)
                return
            new_stem = (renameVar.get().strip() or stem)
            if WINDOWS_ILLEGAL_CHARS.intersection(new_stem):
                messagebox.showerror(
                    "Move ribbon",
                    f"New name contains Windows-illegal chars: "
                    f"{''.join(sorted(WINDOWS_ILLEGAL_CHARS.intersection(new_stem)))}",
                    parent=dialog,
                )
                return
            srcPath = os.path.join(assetsRoot, srcF, "ribbons", f"{stem}.png")
            dstDir = os.path.join(assetsRoot, dstF, "ribbons")
            dstPath = os.path.join(dstDir, f"{new_stem}.png")
            if not os.path.exists(srcPath):
                messagebox.showerror("Move ribbon", f"Missing source: {srcPath}", parent=dialog)
                return
            if os.path.abspath(srcPath) == os.path.abspath(dstPath):
                messagebox.showinfo("Move ribbon", "Source and destination are the same.", parent=dialog)
                return
            if os.path.exists(dstPath) and not messagebox.askyesno(
                "Overwrite?",
                f"{dstF}/ribbons/{new_stem}.png already exists. Overwrite?",
                parent=dialog,
            ):
                return
            os.makedirs(dstDir, exist_ok=True)
            try:
                os.replace(srcPath, dstPath)
                # Move sidecar too if it exists.
                srcSide = srcPath + ".meta.json"
                if os.path.exists(srcSide):
                    os.replace(srcSide, dstPath + ".meta.json")
            except OSError as exc:
                messagebox.showerror("Move failed", str(exc), parent=dialog)
                return
            self.reloadAssets()
            self._toast(f"Moved {stem} → {dstF}/{new_stem}")
            dialog.destroy()

        btnRow = ttk.Frame(frame)
        btnRow.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(btnRow, text="Move", command=do_move).pack(side="left", padx=2)
        ttk.Button(btnRow, text="Cancel", command=dialog.destroy).pack(side="left", padx=2)

        frame.columnconfigure(1, weight=1)
        dialog.grab_set()
        dialog.focus_set()

    # ------------------------------------------------------------------ Profile editor
    def _openProfileEditorDialog(self) -> None:
        """Edit the active engine profile's numeric fields with live preview.

        Exposes the most-edited fields (canvas geometry, part coords,
        nameplate, offsets, ribbon row capacities) as labeled spinboxes,
        plus a raw-JSON tab for fields not in the visual editor. Save
        writes the JSON file and reloads the profile so the change shows
        immediately on the preview.
        """
        try:
            profile = deepcopy(self.profileData)
        except Exception as exc:
            messagebox.showerror("Profile editor", f"Couldn't read profile: {exc}")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title(f"Profile Editor — {self.profileName}")
        dialog.transient(self.root)
        dialog.configure(background=self.theme["bg"])

        notebook = ttk.Notebook(dialog)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        intVars: dict[tuple, tk.IntVar] = {}

        def addIntRow(parent, row, label, path, default=0):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=2)
            val = profile
            for key in path:
                if isinstance(val, dict):
                    val = val.get(key, default)
                else:
                    val = default
                    break
            v = tk.IntVar(value=int(val) if isinstance(val, (int, float)) else default)
            intVars[tuple(path)] = v
            tk.Spinbox(parent, from_=-9999, to=9999, textvariable=v, width=8).grid(
                row=row, column=1, sticky="w", padx=4, pady=2
            )

        # Geometry tab
        geomTab = ttk.Frame(notebook, padding=10)
        notebook.add(geomTab, text="Canvas")
        for i, (label, key) in enumerate([
            ("Image size", "image_size"),
            ("Ribbon area width", "ribbon_area_width"),
            ("Max medals per side", "max_medals_per_side"),
            ("Default nameplate width", "default_nameplate_width"),
            ("Nameplate letter spacing", "nameplate_letter_spacing"),
            ("Hover preview size", "hover_preview_size"),
        ]):
            addIntRow(geomTab, i, label, [key])

        # Part coords tab
        coordsTab = ttk.Frame(notebook, padding=10)
        notebook.add(coordsTab, text="Part coords")
        ttk.Label(coordsTab, text="Part").grid(row=0, column=0, sticky="w")
        ttk.Label(coordsTab, text="X").grid(row=0, column=1, sticky="w")
        ttk.Label(coordsTab, text="Y").grid(row=0, column=2, sticky="w")
        partCoordVars: dict[str, tuple[tk.IntVar, tk.IntVar]] = {}
        for i, partKey in enumerate(partCoordsKeys, start=1):
            ttk.Label(coordsTab, text=partKey).grid(row=i, column=0, sticky="w", padx=4, pady=2)
            coords = (profile.get("part_coords") or {}).get(partKey, [0, 0])
            vx = tk.IntVar(value=int(coords[0]) if isinstance(coords, (list, tuple)) and coords else 0)
            vy = tk.IntVar(value=int(coords[1]) if isinstance(coords, (list, tuple)) and len(coords) > 1 else 0)
            intVars[("part_coords", partKey, 0)] = vx
            intVars[("part_coords", partKey, 1)] = vy
            partCoordVars[partKey] = (vx, vy)
            tk.Spinbox(coordsTab, from_=-999, to=999, textvariable=vx, width=6).grid(row=i, column=1, padx=4)
            tk.Spinbox(coordsTab, from_=-999, to=999, textvariable=vy, width=6).grid(row=i, column=2, padx=4)

        def _resetPartCoordsToDefaults() -> None:
            # Rewrites every Part-coord spinbox to the canonical 128x128 layout.
            # The user still has to click Save (or Save raw) for it to persist.
            for key, (vx_, vy_) in partCoordVars.items():
                dx, dy = DEFAULT_PART_COORDS[key]
                vx_.set(dx)
                vy_.set(dy)

        ttk.Button(
            coordsTab,
            text="Reset to defaults",
            command=_resetPartCoordsToDefaults,
        ).grid(row=len(partCoordsKeys) + 1, column=0, columnspan=3, pady=(10, 0), sticky="w", padx=4)

        # Offsets tab
        offsetsTab = ttk.Frame(notebook, padding=10)
        notebook.add(offsetsTab, text="Offsets")
        for i, (label, key) in enumerate([
            ("Pocket col spacing", "pocket_col_spacing"),
            ("Pocket right offset", "pocket_right_offset"),
            ("Pocket X offset", "pocket_x_offset"),
            ("Corpus X offset", "corpus_x_offset"),
            ("Ribbons right-align offset", "ribbons_right_align_offset"),
        ]):
            addIntRow(offsetsTab, i, label, ["offsets", key])

        # Ribbon rows tab
        rowsTab = ttk.Frame(notebook, padding=10)
        notebook.add(rowsTab, text="Ribbon rows")
        for i, (label, key) in enumerate([
            ("Centered row capacity", "centered_row_capacity"),
            ("Right-align start row", "right_start_row"),
            ("First right row capacity", "first_right_row_capacity"),
            ("Subsequent right row capacity", "subsequent_right_row_capacity"),
        ]):
            addIntRow(rowsTab, i, label, ["ribbon_rows", key])

        # Medal offsets tab — per-slot (x, y) nudges added to each medal's
        # auto-computed pocket position. 0 leaves the auto layout unchanged.
        medalTab = ttk.Frame(notebook, padding=10)
        notebook.add(medalTab, text="Medal offsets")
        ttk.Label(medalTab, text="Slot").grid(row=0, column=0, sticky="w")
        ttk.Label(medalTab, text="X").grid(row=0, column=1, sticky="w")
        ttk.Label(medalTab, text="Y").grid(row=0, column=2, sticky="w")
        medalSlotRows = [
            ("Award 1", "award_1"),
            ("Award 2", "award_2"),
            ("Award 3", "award_3"),
            ("Bonus 1", "bonus_1"),
            ("Bonus 2", "bonus_2"),
            ("Bonus 3", "bonus_3"),
        ]
        for i, (label, slot) in enumerate(medalSlotRows, start=1):
            ttk.Label(medalTab, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=2)
            for col, axis in ((1, "x"), (2, "y")):
                key = f"{slot}_{axis}"
                val = (profile.get("medal_slot_offsets") or {}).get(key, 0)
                v = tk.IntVar(value=int(val) if isinstance(val, (int, float)) else 0)
                intVars[("medal_slot_offsets", key)] = v
                tk.Spinbox(medalTab, from_=-999, to=999, textvariable=v, width=6).grid(
                    row=i, column=col, padx=4
                )

        # Per-row medal spacing (center-to-center, px). 0 = auto (medal width).
        spacingHeaderRow = len(medalSlotRows) + 1
        ttk.Label(medalTab, text="Spacing (0 = auto)").grid(
            row=spacingHeaderRow, column=0, columnspan=3, sticky="w", padx=4, pady=(10, 2)
        )
        for j, (label, key) in enumerate(
            [("Award row spacing", "award_spacing"), ("Bonus row spacing", "bonus_spacing")]
        ):
            r = spacingHeaderRow + 1 + j
            ttk.Label(medalTab, text=label).grid(row=r, column=0, sticky="w", padx=4, pady=2)
            val = (profile.get("medal_slot_offsets") or {}).get(key, 0)
            v = tk.IntVar(value=int(val) if isinstance(val, (int, float)) else 0)
            intVars[("medal_slot_offsets", key)] = v
            tk.Spinbox(medalTab, from_=0, to=999, textvariable=v, width=6).grid(
                row=r, column=1, padx=4
            )

        # Raw JSON tab
        rawTab = ttk.Frame(notebook, padding=10)
        notebook.add(rawTab, text="Raw JSON")
        ttk.Label(
            rawTab,
            text="Edit any field. Saving from this tab overrides the visual editor.",
            foreground=self.theme.get("status", "#888"),
        ).pack(anchor="w", pady=(0, 4))
        rawText = tk.Text(
            rawTab,
            bg=self.theme["panel_bg"],
            fg=self.theme["text"],
            relief="flat",
            highlightthickness=0,
            wrap="none",
            height=18,
        )
        rawText.pack(fill="both", expand=True)
        rawText.insert("1.0", json.dumps(profile, indent=2))

        # Buttons
        buttonRow = ttk.Frame(dialog)
        buttonRow.pack(fill="x", padx=8, pady=(0, 8))

        def collectFromVars() -> dict:
            out = deepcopy(profile)
            for path, var in intVars.items():
                node = out
                *parents, leaf = path
                for key in parents:
                    if isinstance(key, int):
                        # part_coords leaf — node should be a list of two ints.
                        node[parents[-1] if len(parents) > 1 else key] = node.get(parents[-1], [0, 0])
                    if isinstance(node, dict):
                        node = node.setdefault(key, {} if not isinstance(key, int) else [])
                # Walk again for the actual write — simpler recursion:
            # Easier: rebuild from scratch using known paths.
            out = deepcopy(profile)
            for path, var in intVars.items():
                value = int(var.get())
                if path[0] == "part_coords":
                    part, idx = path[1], path[2]
                    pc = out.setdefault("part_coords", {})
                    coords = pc.get(part)
                    if not isinstance(coords, list) or len(coords) < 2:
                        coords = [0, 0]
                    coords[idx] = value
                    pc[part] = coords
                elif path[0] == "offsets":
                    out.setdefault("offsets", {})[path[1]] = value
                elif path[0] == "ribbon_rows":
                    out.setdefault("ribbon_rows", {})[path[1]] = value
                elif path[0] == "medal_slot_offsets":
                    out.setdefault("medal_slot_offsets", {})[path[1]] = value
                else:
                    out[path[0]] = value
            return out

        def do_save(useRaw: bool):
            try:
                if useRaw:
                    new_profile = json.loads(rawText.get("1.0", "end"))
                    if not isinstance(new_profile, dict):
                        raise ValueError("Top level must be an object")
                else:
                    new_profile = collectFromVars()
            except Exception as exc:
                messagebox.showerror("Save failed", f"Invalid JSON: {exc}", parent=dialog)
                return
            try:
                saveProfile(new_profile, self.profileName)
            except Exception as exc:
                messagebox.showerror("Save failed", str(exc), parent=dialog)
                return
            dialog.destroy()
            self._applySelectedProfile(self.profileName)
            self._toast("Profile saved")

        ttk.Button(buttonRow, text="Cancel", command=dialog.destroy).pack(side="right", padx=2)
        ttk.Button(buttonRow, text="Save (raw)", command=lambda: do_save(True)).pack(side="right", padx=2)
        ttk.Button(buttonRow, text="Save", command=lambda: do_save(False)).pack(side="right", padx=2)

        dialog.grab_set()
        dialog.focus_set()

    # ------------------------------------------------------------------ Loadout sharing
    def _exportLoadoutImage(self) -> None:
        """Render the current setup as a PNG tagged kind=loadout for sharing.

        The image looks identical to a normal Generate Image output, but the
        embedded `ribbonengine` JSON carries `kind:"loadout"` and a loadout
        name so recipients are prompted to save it into their loadouts/
        folder instead of replacing their live state.
        """
        defaultName = self.currentLoadoutName or self.entry.get().strip() or "loadout"
        name = _askString(self.root, "Export Loadout", "Loadout name to share:", defaultName)
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror("Error", "Loadout name cannot be empty.")
            return

        def showError(message: str) -> None:
            messagebox.showerror("Error", message)

        image, _, _ = self.buildImage(requireNameForNew=False, errorCallback=showError)
        if image is None:
            return

        os.makedirs(ribbonOutputDir, exist_ok=True)
        safeName = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_", " ")).strip() or "loadout"
        stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        savePath = os.path.join(ribbonOutputDir, f"loadout_{safeName.replace(' ', '_')}_{stamp}.png")

        metadata = self.buildMetadata()
        metadata["kind"] = "loadout"
        metadata["loadout_name"] = name
        metadata["custom_offsets"] = {n: list(v) for n, v in self.customOffsets.items()}

        try:
            pnginfo = PngImagePlugin.PngInfo()
            pnginfo.add_text("ribbonengine", json.dumps(metadata, separators=(",", ":")))
            _atomicSaveImage(image, savePath, pnginfo=pnginfo)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return

        self._toast(f"Exported loadout {name!r}", duration_ms=2400)
        self._rememberRecentFile(savePath)

    # ------------------------------------------------------------------ Diff view
    def _readRibbonengineMetadata(self, path: str) -> Optional[dict]:
        try:
            with Image.open(path) as img:
                info = dict(img.info)
        except Exception as exc:
            messagebox.showerror("Error", f"Couldn't read {os.path.basename(path)}: {exc}")
            return None
        raw = info.get("ribbonengine")
        if not isinstance(raw, str):
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _openDiffDialog(self) -> None:
        # Pick 1 PNG to diff against the current live setup, or 2+ to diff
        # the files against each other.
        paths = filedialog.askopenfilenames(
            title="Pick 1 PNG (diff vs current) or 2+ PNGs (diff each other)",
            filetypes=[("PNG", "*.png"), ("All files", "*.*")],
        )
        if not paths:
            return

        metas: list[tuple[Optional[str], dict]] = []
        skipped: list[str] = []
        for p in paths:
            meta = self._readRibbonengineMetadata(p)
            if meta is None:
                skipped.append(os.path.basename(p))
            else:
                metas.append((p, meta))

        if len(metas) == 1:
            # Compare picked file against in-memory state.
            metas.append((None, self.buildMetadata()))
        elif len(metas) < 2:
            messagebox.showerror(
                "Diff failed",
                "Need at least 1 PNG with Ribbon Engine metadata. "
                f"Skipped (no metadata): {', '.join(skipped) or '—'}",
            )
            return

        self._renderDiffDialog(metas, skipped)

    def _renderDiffDialog(
        self,
        metas: list[tuple[str, dict]],
        skipped: list[str],
    ) -> None:
        """Show a Toplevel comparing N loadouts.

        Two-mode dialog: a List/Text view (every ribbon × every loadout as a
        matrix) and an Overlay view (rendered images side-by-side with the
        loadout name beneath each).
        """
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Loadout Diff ({len(metas)} files)")
        dialog.transient(self.root)
        dialog.configure(background=self.theme["bg"])

        notebook = ttk.Notebook(dialog)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        # ---- Tab 1: matrix view
        listTab = ttk.Frame(notebook, padding=8)
        notebook.add(listTab, text="Ribbons")

        labels = [
            "(current)" if p is None else os.path.splitext(os.path.basename(p))[0]
            for p, _ in metas
        ]
        every_ribbon: set[str] = set()
        for _, m in metas:
            every_ribbon.update(m.get("ribbons", []) or [])
            manual = m.get("manual_slots") or {}
            if isinstance(manual, dict):
                every_ribbon.update(v for v in manual.values() if isinstance(v, str) and v)
        sortedRibbons = sorted(every_ribbon)

        # Width is recomputed once col_w is known; seed a sensible default here.
        text = tk.Text(
            listTab,
            width=max(60, 30 + len(metas) * 14),
            height=max(8, min(28, len(sortedRibbons) + 6)),
            bg=self.theme["panel_bg"],
            fg=self.theme["text"],
            relief="flat",
            highlightthickness=0,
            wrap="none",
        )
        text.pack(fill="both", expand=True)
        text.tag_configure("header", font=("TkDefaultFont", 9, "bold"))
        text.tag_configure("present", foreground="#5fbf5f")
        text.tag_configure("absent", foreground=self.theme.get("status", "#888"))
        text.tag_configure("meta", foreground=self.theme.get("status", "#888"))

        text.insert("end", "Files:\n", "header")
        for label, (_, m) in zip(labels, metas):
            text.insert(
                "end",
                f"  {label}  faction={m.get('faction', '')}  nametape={m.get('nameplate', '')!r}\n",
                "meta",
            )
        text.insert("end", "\n")
        if skipped:
            text.insert("end", f"Skipped (no metadata): {', '.join(skipped)}\n\n", "meta")

        def _slotCount(meta: dict, item: str) -> int:
            """How many times `item` appears across this loadout's slots."""
            manual = meta.get("manual_slots") or {}
            manual_n = sum(1 for v in manual.values() if v == item) if isinstance(manual, dict) else 0
            checkbox_n = sum(1 for r in (meta.get("ribbons", []) or []) if r == item)
            return manual_n + checkbox_n

        # Column width must fit the widest value any column will print
        # (labels, ribbon names, slot values like medal names).
        widest = max(
            [len(l) for l in labels]
            + [len(r) for r in sortedRibbons]
            + [
                len(str(v))
                for _, m in metas
                for key in ("awards", "bonuses")
                for v in (m.get(key, []) or [])
                if v
            ]
            + [len(m.get("department_badge") or "") for _, m in metas]
            + [4],
            default=4,
        )
        col_w = min(28, widest + 2)
        header_line = f"{'Ribbon':<28}" + "".join(f"{l[:col_w-1]:<{col_w}}" for l in labels) + "\n"

        text.insert("end", "Ribbons\n", "header")
        text.insert("end", header_line)
        text.insert("end", "-" * len(header_line) + "\n", "meta")
        for ribbon in sortedRibbons:
            line = f"{ribbon[:27]:<28}"
            text.insert("end", line)
            for _, m in metas:
                count = _slotCount(m, ribbon)
                if count == 0:
                    mark = "   ."
                    tag = "absent"
                elif count == 1:
                    mark = "   X"
                    tag = "present"
                else:
                    mark = f"  x{count}"
                    tag = "present"
                text.insert("end", f"{mark:<{col_w}}", tag)
            text.insert("end", "\n")
        text.insert("end", "\n")

        # Awards / medal slots
        award_slots = max((len(m.get("awards", []) or []) for _, m in metas), default=0)
        bonus_slots = max((len(m.get("bonuses", []) or []) for _, m in metas), default=0)

        def writeSlots(title: str, key: str, count: int) -> None:
            if count == 0:
                return
            text.insert("end", f"\n{title}\n", "header")
            text.insert("end", header_line)
            text.insert("end", "-" * len(header_line) + "\n", "meta")
            for i in range(count):
                row = f"  slot {i + 1:<22}"
                text.insert("end", row)
                for _, m in metas:
                    slots = m.get(key, []) or []
                    val = slots[i] if i < len(slots) else "NONE"
                    val = val if val and val != "NONE" else "—"
                    tag = "present" if val != "—" else "absent"
                    text.insert("end", f"{val[:col_w-1]:<{col_w}}", tag)
                text.insert("end", "\n")

        writeSlots("Awards", "awards", award_slots)
        writeSlots("Bonuses", "bonuses", bonus_slots)

        # Department badge
        if any(m.get("department_badge") for _, m in metas):
            text.insert("end", "\nDepartment badge\n", "header")
            text.insert("end", header_line)
            text.insert("end", "-" * len(header_line) + "\n", "meta")
            text.insert("end", f"{'badge':<28}")
            for _, m in metas:
                badge = m.get("department_badge") or "—"
                tag = "present" if badge != "—" else "absent"
                text.insert("end", f"{badge[:col_w-1]:<{col_w}}", tag)
            text.insert("end", "\n")

        # Totals
        text.insert("end", "\nTotals\n", "header")
        text.insert("end", header_line)
        text.insert("end", "-" * len(header_line) + "\n", "meta")
        for label_name, getter in [
            ("ribbons", lambda m: sum(_slotCount(m, r) for r in sortedRibbons)),
            ("awards", lambda m: sum(1 for v in (m.get("awards", []) or []) if v and v != "NONE")),
            ("bonuses", lambda m: sum(1 for v in (m.get("bonuses", []) or []) if v and v != "NONE")),
        ]:
            text.insert("end", f"  {label_name:<26}")
            for _, m in metas:
                text.insert("end", f"{str(getter(m)):<{col_w}}", "present")
            text.insert("end", "\n")

        text.config(state="disabled")

        # ---- Tab 2: side-by-side (the actual PNG files, not a re-render)
        overlayTab = ttk.Frame(notebook, padding=8)
        notebook.add(overlayTab, text="Side-by-side")

        canvas = tk.Canvas(
            overlayTab,
            bg=self.theme["panel_bg"],
            highlightthickness=0,
            height=320,
        )
        hbar = ttk.Scrollbar(overlayTab, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=hbar.set)
        canvas.pack(fill="both", expand=True)
        hbar.pack(fill="x")

        thumbStore: list[ImageTk.PhotoImage] = []
        x = 12
        scale = 2
        for label, (path, _m) in zip(labels, metas):
            try:
                if path is None:
                    rendered, _, _ = self.buildImage(requireNameForNew=False, errorCallback=lambda _m: None)
                    if rendered is None:
                        continue
                    img = rendered.convert("RGBA")
                else:
                    with Image.open(path) as src:
                        img = src.convert("RGBA")
            except Exception:
                continue
            w, h = img.size
            big = img.resize((w * scale, h * scale), Image.NEAREST)
            photo = ImageTk.PhotoImage(big, master=self.root)
            thumbStore.append(photo)
            canvas.create_image(x, 12, image=photo, anchor="nw")
            canvas.create_text(
                x + (w * scale) // 2, 12 + h * scale + 6,
                text=label, fill=self.theme["text"], anchor="n",
            )
            x += w * scale + 24
        canvas.images = thumbStore  # keep refs alive
        canvas.configure(scrollregion=canvas.bbox("all"))

        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=(0, 8))
        dialog.grab_set()
        dialog.focus_set()

    # ------------------------------------------------------------------ Undo/redo
    def _snapshotState(self) -> dict:
        return {
            "ribbons": self.getSelectedRibbonNames(),
            "nameplate": self.entry.get(),
            "offsets": dict(self.customOffsets),
            "manual_slots": dict(getattr(self, "manualRibbonSlots", {})),
            "manual_slot_colors": {k: dict(v) for k, v in getattr(self, "manualSlotColors", {}).items()},
        }

    def _captureHistory(self) -> None:
        if self._suppressHistory:
            return
        snapshot = self._snapshotState()
        if self._historyPast and self._historyPast[-1] == snapshot:
            return
        self._historyPast.append(snapshot)
        if len(self._historyPast) > self._historyMax:
            self._historyPast.pop(0)
        # Any new action invalidates the redo stack.
        self._historyFuture.clear()

    def _restoreSnapshot(self, snapshot: dict) -> None:
        self._suppressHistory = True
        try:
            selected = set(snapshot.get("ribbons", []))
            for name, var in self.checkboxVars.items():
                var.set(1 if name in selected else 0)
            self.entry.delete(0, tk.END)
            self.entry.insert(0, snapshot.get("nameplate", ""))
            self.customOffsets = dict(snapshot.get("offsets", {}))
            if hasattr(self, "manualRibbonSlots"):
                self.manualRibbonSlots = {int(k): v for k, v in snapshot.get("manual_slots", {}).items()}
                self.manualSlotColors = {
                    int(k): dict(v) for k, v in (snapshot.get("manual_slot_colors") or {}).items() if isinstance(v, dict)
                }
                self._manualSelectedSlot = None
                self._redrawManualGrid()
        finally:
            self._suppressHistory = False
        if self.showSelectedVar.get():
            self.applyFilter()
        self.schedulePreview()

    def undo(self, _event=None) -> Optional[str]:
        if not self._historyPast:
            return "break"
        current = self._snapshotState()
        previous = self._historyPast.pop()
        self._historyFuture.append(current)
        self._restoreSnapshot(previous)
        return "break"

    def redo(self, _event=None) -> Optional[str]:
        if not self._historyFuture:
            return "break"
        current = self._snapshotState()
        nxt = self._historyFuture.pop()
        self._historyPast.append(current)
        self._restoreSnapshot(nxt)
        return "break"

    # ------------------------------------------------------------------ Drag-to-place
    def _previewToImageCoords(self, event_x: int, event_y: int) -> Optional[tuple[int, int]]:
        """Convert a click on `labelPreview` into 128×128 source-image coords."""
        if not isinstance(self.previewImg, ImageTk.PhotoImage):
            return None
        scale = max(0.001, self.previewScale)
        return (int(event_x / scale), int(event_y / scale))

    def _hitTestPlacement(self, ix: int, iy: int) -> Optional[dict]:
        # Iterate in reverse so the topmost (last-pasted) ribbon wins.
        for placement in reversed(self.lastPlacements):
            x, y, w, h = placement["x"], placement["y"], placement["w"], placement["h"]
            if x <= ix < x + w and y <= iy < y + h:
                return placement
        return None

    def _onPreviewPress(self, event) -> None:
        coords = self._previewToImageCoords(event.x, event.y)
        if coords is None:
            return
        placement = self._hitTestPlacement(*coords)
        if placement is None:
            self._dragState = None
            return
        self._captureHistory()
        self._dragState = {
            "name": placement["name"],
            "start_image": coords,
            "start_offset": self.customOffsets.get(placement["name"], (0, 0)),
        }
        self.setStatus(f"Dragging: {placement['name']}")

    def _onPreviewDrag(self, event) -> None:
        if not self._dragState:
            return
        coords = self._previewToImageCoords(event.x, event.y)
        if coords is None:
            return
        sx, sy = self._dragState["start_image"]
        ox, oy = self._dragState["start_offset"]
        new_offset = (ox + (coords[0] - sx), oy + (coords[1] - sy))
        self.customOffsets[self._dragState["name"]] = new_offset
        self.schedulePreview()

    def _onPreviewRelease(self, _event) -> None:
        if not self._dragState:
            return
        name = self._dragState["name"]
        self._dragState = None
        # If the offset ended up at (0, 0), drop it to keep loadouts tidy.
        if self.customOffsets.get(name) == (0, 0):
            self.customOffsets.pop(name, None)
        self.setStatus(f"Moved {name}. Right-click a ribbon to reset its position.")

    def _onPreviewRightClick(self, event) -> None:
        coords = self._previewToImageCoords(event.x, event.y)
        if coords is None:
            return
        placement = self._hitTestPlacement(*coords)
        if placement and placement["name"] in self.customOffsets:
            self._captureHistory()
            self.customOffsets.pop(placement["name"], None)
            self.schedulePreview()
            self.setStatus(f"Reset position: {placement['name']}")

    # ------------------------------------------------------------------ Manual ribbon placement
    _MANUAL_CELL_W = 32
    _MANUAL_CELL_H = 14
    _MANUAL_CELL_GAP = 2
    _MANUAL_GRID_ROWS = 8

    def _buildRibbonPlacementPanel(self, parent) -> None:
        """Build the snap-to-slot ribbon-placement panel.

        Lays out:
          * a `Ribbon:` combobox (source: same `ribbons` group the
            sidebar shows for the active faction),
          * a `Remove` button (deletes the currently selected slot),
          * a `Canvas` grid mirroring the engine's ribbon row geometry,
            each cell sized `_MANUAL_CELL_W × _MANUAL_CELL_H`,
          * a hint label and `Reset` button.

        Clicking the canvas drives `_onManualGridClick`. State lives in
        `self.manualRibbonSlots` (dict[slot_idx, ribbon_name]), which
        the renderer consumes whenever non-empty (implicit manual mode).
        Duplicates across slots are allowed.
        """
        frame = ttk.LabelFrame(parent, text="Ribbon Placement", padding=6)
        frame.pack(fill="x", pady=(6, 4))

        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Label(top, text="Ribbon:").pack(side="left")
        self.manualRibbonComboVar = tk.StringVar()
        # `state="normal"` makes the combobox editable so typing filters the
        # dropdown. KeyRelease re-filters; click on a result populates the var.
        self.manualRibbonCombo = ttk.Combobox(top, textvariable=self.manualRibbonComboVar, state="normal")
        self.manualRibbonCombo.pack(side="left", padx=(4, 4), fill="x", expand=True)
        self.manualRibbonCombo.bind("<KeyRelease>", self._onManualRibbonFilter)
        self._manualRibbonAllChoices: list[str] = []
        ttk.Button(top, text="Remove", width=8, command=self._onManualRemove).pack(side="right")

        body = ttk.Frame(frame)
        body.pack(fill="x", pady=(6, 0))
        gw, gh = self._manualGridSize()
        self.manualGridCanvas = tk.Canvas(
            body,
            width=gw,
            height=gh,
            highlightthickness=0,
            bg=self.theme.get("entry_bg", self.theme.get("panel_bg", "#2a2a2a")),
        )
        self.manualGridCanvas.pack(side="left")
        self.manualGridCanvas.bind("<Button-1>", self._onManualGridClick)
        self.manualGridCanvas.bind("<Button-3>", self._onManualGridRightClick)
        # Keyboard shortcuts: focus the canvas first (click) and these fire.
        self.manualGridCanvas.configure(takefocus=True)
        self.manualGridCanvas.bind("<FocusIn>", lambda _e: self._redrawManualGrid())
        self.manualGridCanvas.bind("<Button-1>", lambda e: (self.manualGridCanvas.focus_set(), self._onManualGridClick(e))[1], add="+")
        for key in ("<Delete>", "<BackSpace>"):
            self.manualGridCanvas.bind(key, lambda _e: self._onManualKeyDelete())
        self.manualGridCanvas.bind("<Left>",  lambda _e: self._moveManualSelection(-1, 0))
        self.manualGridCanvas.bind("<Right>", lambda _e: self._moveManualSelection(1, 0))
        self.manualGridCanvas.bind("<Up>",    lambda _e: self._moveManualSelection(0, -1))
        self.manualGridCanvas.bind("<Down>",  lambda _e: self._moveManualSelection(0, 1))
        self.manualGridCanvas.bind("<Return>", lambda _e: self._onManualKeyPlace())
        self.manualGridCanvas.bind("r", lambda _e: self._focusRecolorEntry())
        self.manualGridCanvas.bind("R", lambda _e: self._focusRecolorEntry())

        side = ttk.Frame(body)
        side.pack(side="left", padx=(8, 0), fill="both", expand=True)
        self.manualHintLabel = ttk.Label(side, text="Pick a ribbon, then click an empty slot. Click a placed ribbon to recolor it.", wraplength=180, justify="left")
        self.manualHintLabel.pack(anchor="w")
        ttk.Button(side, text="Reset", command=self._onManualReset).pack(anchor="w", pady=(8, 0))

        # Per-ribbon recolor overrides (writes to <ribbon>.png.meta.json).
        recolorBox = ttk.LabelFrame(side, text="Recolor selected ribbon (hex)", padding=4)
        recolorBox.pack(anchor="w", fill="x", pady=(10, 0))
        self.manualRecolorVars: dict[str, tk.StringVar] = {}
        self.manualRecolorSwatches: dict[str, tk.Label] = {}
        vcmd = (self.root.register(self._validateHexEntry), "%P")
        for label in ("border", "stripe", "base"):
            row = ttk.Frame(recolorBox)
            row.pack(fill="x", pady=1)
            region_label = ttk.Label(row, text=label.capitalize(), width=7)
            region_label.pack(side="left")
            ttk.Label(row, text="#").pack(side="left")
            var = tk.StringVar()
            entry = ttk.Entry(
                row, textvariable=var, width=7,
                validate="key", validatecommand=vcmd,
            )
            entry.pack(side="left", padx=(1, 0))
            self.manualRecolorVars[label] = var
            if label == "border":
                self._manualRecolorBorderEntry = entry
            # Swatch button — clicking opens a color picker; the bg reflects
            # the current hex so the user can eyeball the color at a glance.
            swatch = tk.Label(row, text="  ", relief="solid", borderwidth=1, cursor="hand2", bg="#888888")
            swatch.pack(side="left", padx=(4, 0))
            swatch.bind("<Button-1>", lambda _e, r=label: self._openRecolorPicker(r))
            self.manualRecolorSwatches[label] = swatch
            # Right-click any entry → copy menu (#RRGGBB / 0xRRGGBB).
            entry.bind("<Button-3>", lambda e, r=label: self._showHexContextMenu(e, r))
            # Live swatch sync as user types.
            var.trace_add("write", lambda *_a, r=label: self._syncSwatchColor(r))
            # Tooltip — shows the current effective source for this region.
            _Tooltip(region_label, lambda r=label: self._recolorRegionTooltip(r))
            _Tooltip(swatch, "Click to pick a color")
        btnRow = ttk.Frame(recolorBox)
        btnRow.pack(fill="x", pady=(4, 0))
        ttk.Button(btnRow, text="Apply", width=7, command=self._onApplyRibbonColors).pack(side="left")
        ttk.Button(btnRow, text="Clear", width=7, command=self._onClearRibbonColors).pack(side="left", padx=(4, 0))
        ttk.Button(btnRow, text="Reset", width=7, command=self._onResetAllRibbonColors).pack(side="left", padx=(4, 0))

        self._manualSelectedSlot: Optional[int] = None
        self._manualThumbCache: dict[int, ImageTk.PhotoImage] = {}
        self.manualRibbonCombo.bind("<<ComboboxSelected>>", lambda _e: self._loadRibbonColorsIntoFields())
        self._refreshManualRibbonChoices()
        self._loadRibbonColorsIntoFields()
        self._redrawManualGrid()

    def _manualGridLayout(self) -> tuple[list[tuple[int, int, int, int, int]], int, int]:
        """Return canvas-space rects for every manual placement slot.

        Returns `(rects, total_w, total_h)` where each rect is
        `(slot_idx, x1, y1, x2, y2)` in pixels relative to the canvas
        origin. Row capacities come from the active engine profile
        (`ribbonCenteredRowCapacity`, `ribbonRightFirstRowCapacity`,
        `ribbonRightSubsequentRowCapacity`) so adding rows or capacity
        in the profile JSON expands the panel with zero code changes.

        Row 1 (the bottom-most ribbon row in the rendered ribbon stack)
        is drawn at the *bottom* of the panel, matching how ribbons
        physically stack upward on the final canvas. `slot_idx` is
        assigned in the same row-then-column order used by
        `_computeRibbonSlotGrid` so the two stay 1:1.
        """
        cw, ch, gap = self._MANUAL_CELL_W, self._MANUAL_CELL_H, self._MANUAL_CELL_GAP
        max_cap = max(ribbonCenteredRowCapacity, ribbonRightFirstRowCapacity, ribbonRightSubsequentRowCapacity)
        total_w = max_cap * cw + (max_cap - 1) * gap
        total_h = self._MANUAL_GRID_ROWS * ch + (self._MANUAL_GRID_ROWS - 1) * gap
        rects: list[tuple[int, int, int, int, int]] = []
        slot_idx = 0
        for row_num in range(1, self._MANUAL_GRID_ROWS + 1):
            if row_num < ribbonRightStartRow:
                cap, align_right = ribbonCenteredRowCapacity, False
            elif row_num == ribbonRightStartRow:
                cap, align_right = ribbonRightFirstRowCapacity, True
            else:
                cap, align_right = ribbonRightSubsequentRowCapacity, True
            row_w = cap * cw + (cap - 1) * gap
            x_start = (total_w - row_w) if align_right else (total_w - row_w) // 2
            # Row 1 is the bottom-most ribbon row; mirror that in the panel.
            y = (self._MANUAL_GRID_ROWS - row_num) * (ch + gap)
            for i in range(cap):
                x1 = x_start + i * (cw + gap)
                rects.append((slot_idx, x1, y, x1 + cw, y + ch))
                slot_idx += 1
        return rects, total_w, total_h

    def _manualGridSize(self) -> tuple[int, int]:
        _, w, h = self._manualGridLayout()
        return w, h

    def _refreshManualRibbonChoices(self) -> None:
        if not hasattr(self, "manualRibbonCombo"):
            return
        names = sorted(item.name for item in self.ribbonGroups.get("ribbons", []))
        self._manualRibbonAllChoices = names
        try:
            self.manualRibbonCombo["values"] = names
            if names and self.manualRibbonComboVar.get() not in names:
                self.manualRibbonComboVar.set(names[0])
        except (tk.TclError, AttributeError):
            pass

    def _onManualRibbonFilter(self, event) -> None:
        """KeyRelease handler: typing in the dropdown filters its values list.

        Skips navigation keys (arrows, Return, Escape) so they pass through
        to the combobox's own listbox behavior.
        """
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        typed = self.manualRibbonComboVar.get().lower().strip()
        all_choices = getattr(self, "_manualRibbonAllChoices", []) or []
        if not typed:
            filtered = all_choices
        else:
            filtered = [n for n in all_choices if typed in n.lower()]
        try:
            self.manualRibbonCombo["values"] = filtered or all_choices
        except tk.TclError:
            pass

    def _manualThumbnail(self, ribbon_name: str, slot_idx: Optional[int] = None) -> Optional[ImageTk.PhotoImage]:
        """Recolored, cell-sized thumbnail for a ribbon name.

        Goes through `loadRibbonImage` so the thumbnail matches whatever
        the renderer will draw at composite time (same faction palette,
        same recolor toggles). Uses `Image.NEAREST` to preserve the
        pixel-art look when upscaling from the source PNG to the
        manual-grid cell. Returns `None` if the ribbon isn't in the
        active faction's allowlist or loading fails — callers should
        treat that as "leave the cell empty."
        """
        item = next((i for i in self.ribbonGroups.get("ribbons", []) if i.name == ribbon_name), None)
        if item is None:
            return None
        slot_override = self.manualSlotColors.get(slot_idx) if slot_idx is not None else None
        try:
            if slot_override:
                img = renderRibbonWithColors(item, self.activeFactionKey, slot_override).copy()
            else:
                img = loadRibbonImage(item, factionKey=self.activeFactionKey).copy()
        except Exception:
            return None
        img = img.resize((self._MANUAL_CELL_W, self._MANUAL_CELL_H), Image.NEAREST)
        return ImageTk.PhotoImage(img, master=self.root)

    def _redrawManualGrid(self) -> None:
        """Repaint the manual placement canvas from scratch.

        Draws an empty cell for every slot, overlays a recolored
        thumbnail wherever `manualRibbonSlots` has an entry, and draws
        an accent outline around `_manualSelectedSlot` (if any).
        Thumbnail PhotoImages are stashed on `self._manualThumbCache`
        so Tk's garbage collector doesn't reap them mid-frame.

        Safe to call before the panel has been built (no-op).
        """
        if not hasattr(self, "manualGridCanvas"):
            return
        canvas = self.manualGridCanvas
        canvas.delete("all")
        self._manualThumbCache.clear()
        rects, _, _ = self._manualGridLayout()
        empty_bg = self.theme.get("panel_bg", "#1f1f1f")
        border = self.theme.get("border", "#666")
        accent = self.theme.get("accent", "#ffcc00")
        for slot_idx, x1, y1, x2, y2 in rects:
            canvas.create_rectangle(x1, y1, x2, y2, fill=empty_bg, outline=border)
            name = self.manualRibbonSlots.get(slot_idx)
            if name:
                thumb = self._manualThumbnail(name, slot_idx)
                if thumb is not None:
                    self._manualThumbCache[slot_idx] = thumb
                    canvas.create_image(x1, y1, image=thumb, anchor="nw")
            if slot_idx == self._manualSelectedSlot:
                canvas.create_rectangle(x1, y1, x2, y2, outline=accent, width=2)
            if slot_idx == getattr(self, "_dragHoverSlot", None):
                canvas.create_rectangle(x1, y1, x2, y2, outline=accent, width=3, dash=(3, 2))

    # ---- Sidebar → manual-grid drag-and-drop -----------------------------
    _DRAG_THRESHOLD_PX = 6

    def _onSidebarDragStart(self, event, ribbonName: str) -> None:
        """Record press position; drag only starts after a small threshold."""
        self._dragData = {
            "name": ribbonName,
            "start_x": event.x_root,
            "start_y": event.y_root,
            "active": False,
            "tip": None,
        }

    def _onSidebarDragMotion(self, event) -> None:
        data = getattr(self, "_dragData", None)
        if not data:
            return
        dx = abs(event.x_root - data["start_x"])
        dy = abs(event.y_root - data["start_y"])
        if not data["active"] and max(dx, dy) < self._DRAG_THRESHOLD_PX:
            return
        # Activate drag once threshold is crossed.
        if not data["active"]:
            data["active"] = True
            try:
                tip = tk.Toplevel(self.root)
                tip.wm_overrideredirect(True)
                tip.attributes("-topmost", True)
                ttk.Label(
                    tip,
                    text=f"→ {data['name']}",
                    background="#222222",
                    foreground="#ffffff",
                    padding=(6, 2),
                ).pack()
                data["tip"] = tip
            except tk.TclError:
                data["tip"] = None
        tip = data.get("tip")
        if tip is not None:
            try:
                tip.geometry(f"+{event.x_root + 12}+{event.y_root + 12}")
            except tk.TclError:
                pass
        # Highlight the slot under cursor by tracking it on the canvas.
        slot = self._slotAtRootXY(event.x_root, event.y_root)
        self._dragHoverSlot = slot
        if hasattr(self, "manualGridCanvas"):
            self._redrawManualGrid()

    def _onSidebarDragEnd(self, event) -> None:
        data = getattr(self, "_dragData", None)
        if not data:
            return
        tip = data.get("tip")
        if tip is not None:
            try:
                tip.destroy()
            except tk.TclError:
                pass
        active = data.get("active", False)
        name = data.get("name", "")
        self._dragData = None
        self._dragHoverSlot = None
        if not active:
            # Below threshold → let the checkbox toggle normally.
            return
        slot = self._slotAtRootXY(event.x_root, event.y_root)
        if slot is None or not name:
            if hasattr(self, "manualGridCanvas"):
                self._redrawManualGrid()
            return
        # Place ribbon in slot (overwriting any existing occupant).
        self._captureHistory()
        self.manualRibbonSlots[slot] = name
        # If we overwrote a different ribbon, drop the old per-slot color override.
        self.manualSlotColors.pop(slot, None)
        self._manualSelectedSlot = slot
        if hasattr(self, "manualRibbonCombo"):
            try:
                values = self.manualRibbonCombo["values"] or ()
                if name in values:
                    self.manualRibbonComboVar.set(name)
            except tk.TclError:
                pass
        if hasattr(self, "manualHintLabel"):
            self.manualHintLabel.config(text=f"Placed {name} in slot {slot + 1}.")
        self._loadRibbonColorsIntoFields()
        self._redrawManualGrid()
        self.saveCurrentSettings()
        self.schedulePreview()

    def _slotAtRootXY(self, root_x: int, root_y: int) -> Optional[int]:
        """Translate screen coords to a manual-grid slot index, or None."""
        canvas = getattr(self, "manualGridCanvas", None)
        if canvas is None:
            return None
        try:
            if not canvas.winfo_ismapped():
                return None
            cx = root_x - canvas.winfo_rootx()
            cy = root_y - canvas.winfo_rooty()
        except tk.TclError:
            return None
        rects, _, _ = self._manualGridLayout()
        for slot_idx, x1, y1, x2, y2 in rects:
            if x1 <= cx < x2 and y1 <= cy < y2:
                return slot_idx
        return None

    def _onManualGridClick(self, event) -> None:
        """Handle a left-click anywhere on the manual placement canvas.

        Behavior depends on what the user clicked and what's pending:
          * Empty cell + pending ribbon in dropdown → place that ribbon
            into the slot, mark the slot selected, save+repreview.
          * Filled cell (no pending, or pending matches what's there)
            → select that slot for removal; status label updates so
            the user knows what `Remove` would delete.
          * Click outside any cell → clear the selection.

        A history snapshot is captured before any placement so the
        action is undoable. Duplicates are intentionally allowed; the
        renderer reads slots positionally.
        """
        rects, _, _ = self._manualGridLayout()
        for slot_idx, x1, y1, x2, y2 in rects:
            if x1 <= event.x < x2 and y1 <= event.y < y2:
                pending = self.manualRibbonComboVar.get()
                existing = self.manualRibbonSlots.get(slot_idx)
                if existing and (not pending or existing == pending):
                    # Tap a filled slot → select for removal AND surface it in
                    # the dropdown so the recolor fields target this ribbon.
                    self._manualSelectedSlot = slot_idx
                    if existing in (self.manualRibbonCombo["values"] or ()):
                        self.manualRibbonComboVar.set(existing)
                    self._loadRibbonColorsIntoFields()
                    self.manualHintLabel.config(text=f"Selected: {existing}. Click Remove to delete.")
                elif pending:
                    self._captureHistory()
                    self.manualRibbonSlots[slot_idx] = pending
                    self._manualSelectedSlot = slot_idx
                    self.manualHintLabel.config(text=f"Placed {pending} in slot {slot_idx + 1}.")
                    self.saveCurrentSettings()
                    self.schedulePreview()
                self._redrawManualGrid()
                return
        # Clicked outside any cell → clear selection.
        self._manualSelectedSlot = None
        self._redrawManualGrid()

    def _onManualGridRightClick(self, event) -> None:
        """Right-click a filled slot to remove it directly (no selection step)."""
        rects, _, _ = self._manualGridLayout()
        for slot_idx, x1, y1, x2, y2 in rects:
            if x1 <= event.x < x2 and y1 <= event.y < y2:
                if slot_idx not in self.manualRibbonSlots:
                    return
                removed = self.manualRibbonSlots[slot_idx]
                self._captureHistory()
                self.manualRibbonSlots.pop(slot_idx)
                self.manualSlotColors.pop(slot_idx, None)
                if self._manualSelectedSlot == slot_idx:
                    self._manualSelectedSlot = None
                self.manualHintLabel.config(text=f"Removed {removed} from slot {slot_idx + 1}.")
                self._redrawManualGrid()
                self.saveCurrentSettings()
                self.schedulePreview()
                return

    def _syncSwatchColor(self, region: str) -> None:
        """Update the small color swatch next to a hex entry."""
        swatch = self.manualRecolorSwatches.get(region) if hasattr(self, "manualRecolorSwatches") else None
        if swatch is None:
            return
        text = self.manualRecolorVars[region].get().strip().lstrip("#")
        if len(text) == 6 and all(c in "0123456789abcdefABCDEF" for c in text):
            try:
                swatch.configure(bg=f"#{text.lower()}")
                return
            except tk.TclError:
                pass
        swatch.configure(bg="#888888")

    def _openRecolorPicker(self, region: str) -> None:
        """Open the system color picker; selection writes into the entry."""
        current = self.manualRecolorVars[region].get().strip().lstrip("#")
        initial = f"#{current.lower()}" if len(current) == 6 else "#888888"
        try:
            chosen = colorchooser.askcolor(color=initial, parent=self.root, title=f"Pick {region} color")
        except tk.TclError:
            return
        if not chosen or not chosen[1]:
            return
        self.manualRecolorVars[region].set(chosen[1].lstrip("#").lower())

    def _showHexContextMenu(self, event, region: str) -> None:
        """Right-click menu on a hex entry: copy as #RRGGBB or 0xRRGGBB."""
        text = self.manualRecolorVars[region].get().strip().lstrip("#")
        if len(text) != 6:
            return
        menu = tk.Menu(self.root, tearoff=False)
        hex_form = f"#{text.lower()}"
        ox_form = f"0x{text.lower()}"
        menu.add_command(label=f"Copy  {hex_form}", command=lambda: self._copyToClipboard(hex_form))
        menu.add_command(label=f"Copy  {ox_form}", command=lambda: self._copyToClipboard(ox_form))
        menu.add_command(label=f"Copy  {text.lower()}", command=lambda: self._copyToClipboard(text.lower()))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copyToClipboard(self, text: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._toast(f"Copied {text}")
        except tk.TclError:
            pass

    def _recolorRegionTooltip(self, region: str) -> str:
        """Describe where the field's value is currently coming from."""
        slot = getattr(self, "_manualSelectedSlot", None)
        text = self.manualRecolorVars[region].get().strip()
        sources = []
        if slot is not None and slot in self.manualSlotColors:
            if region in self.manualSlotColors[slot]:
                sources.append("per-slot override")
        path = self._currentRibbonAssetPath()
        if path:
            sidecar = _readRibbonSidecar(path)
            if isinstance(sidecar.get("colors"), dict) and sidecar["colors"].get(region):
                sources.append("sidecar")
        if not sources:
            sources.append("faction palette")
        return f"{region.capitalize()}: #{text}\nsource: {sources[0]}"

    def _validateHexEntry(self, proposed: str) -> bool:
        """Tk validatecommand: allow only up to 6 hex digits (no leading #)."""
        if len(proposed) > 6:
            return False
        return all(c in "0123456789abcdefABCDEF" for c in proposed)

    def _currentRibbonAssetPath(self) -> Optional[str]:
        name = self.manualRibbonComboVar.get() if hasattr(self, "manualRibbonComboVar") else ""
        if not name:
            return None
        item = next((i for i in self.ribbonGroups.get("ribbons", []) if i.name == name), None)
        return item.path if item else None

    def _loadRibbonColorsIntoFields(self) -> None:
        """Populate the three hex Entry fields.

        Order of preference per region:
          1. Per-slot override for the currently selected manual slot.
          2. Sidecar `colors.<region>` for the selected ribbon's PNG.
          3. Active faction's palette color (so the user can see the current
             effective color and tweak from there).
        """
        if not hasattr(self, "manualRecolorVars"):
            return

        # Layer 1: per-slot override.
        slot = getattr(self, "_manualSelectedSlot", None)
        slot_colors: dict = {}
        if slot is not None:
            slot_colors = dict(self.manualSlotColors.get(slot, {}))

        # Layer 2: sidecar.
        path = self._currentRibbonAssetPath()
        sidecar_colors: dict = {}
        if path:
            sidecar = _readRibbonSidecar(path)
            raw = sidecar.get("colors")
            if isinstance(raw, dict):
                sidecar_colors = raw

        # Layer 3: faction palette.
        try:
            registry = getFactionRegistry()
            faction = registry.factions.get(self.activeFactionKey)
            palette = faction.palette if faction else None
        except Exception:
            palette = None

        def _rgb_to_hex6(rgb: tuple[int, int, int]) -> str:
            return f"{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

        defaults = {}
        if palette is not None:
            defaults = {
                "border": _rgb_to_hex6(palette.border_color),
                "stripe": _rgb_to_hex6(palette.stripe_color),
                "base": _rgb_to_hex6(palette.base_color),
            }
        for region, var in self.manualRecolorVars.items():
            value = slot_colors.get(region) or sidecar_colors.get(region) or defaults.get(region, "")
            if isinstance(value, str) and value.strip():
                var.set(value.strip().lstrip("#").lower()[:6])
            else:
                var.set("")
            self._syncSwatchColor(region)

    def _onApplyRibbonColors(self) -> None:
        slot = getattr(self, "_manualSelectedSlot", None)
        if slot is None or slot not in self.manualRibbonSlots:
            messagebox.showinfo(
                "Recolor",
                "Click a placed ribbon in the grid first — recolor is per-slot, so "
                "duplicates can be tinted independently.",
            )
            return
        new_colors: dict[str, str] = {}
        for region, var in self.manualRecolorVars.items():
            text = var.get().strip().lstrip("#")
            if not text:
                continue
            if len(text) != 6 or any(c not in "0123456789abcdefABCDEF" for c in text):
                messagebox.showerror("Invalid hex", f"{region.capitalize()} color {text!r} must be 6 hex digits.")
                return
            new_colors[region] = f"#{text.lower()}"
        self._captureHistory()
        if new_colors:
            self.manualSlotColors[slot] = new_colors
        else:
            self.manualSlotColors.pop(slot, None)
        self._manualThumbCache.clear()
        self._redrawManualGrid()
        self.saveCurrentSettings()
        self.schedulePreview()
        ribbon_name = self.manualRibbonSlots.get(slot, "")
        self._toast(f"Recolored slot {slot + 1} ({ribbon_name!r})")

    def _onClearRibbonColors(self) -> None:
        """Drop any per-slot color override on the selected slot and refresh."""
        slot = getattr(self, "_manualSelectedSlot", None)
        if slot is None or slot not in self.manualRibbonSlots:
            for var in self.manualRecolorVars.values():
                var.set("")
            return
        self._captureHistory()
        self.manualSlotColors.pop(slot, None)
        self._manualThumbCache.clear()
        self._redrawManualGrid()
        self.saveCurrentSettings()
        self.schedulePreview()
        self._loadRibbonColorsIntoFields()
        self._toast(f"Cleared colors for slot {slot + 1}")

    def _onResetAllRibbonColors(self) -> None:
        """Wipe every per-slot color override across the whole grid.

        After Reset, the manual-recolor framework effectively isn't in
        play: every placed ribbon falls back to the active faction's
        palette. The moment the user types into a hex field and clicks
        Apply again, that slot reacquires its override and the loop
        starts over. No-op (with a quiet toast) when there are no
        overrides to clear.
        """
        if not self.manualSlotColors:
            for var in self.manualRecolorVars.values():
                var.set("")
            self._toast("No per-ribbon colors to reset.")
            return
        if not messagebox.askyesno(
            "Reset ribbon colors",
            f"Clear per-ribbon color overrides on all "
            f"{len(self.manualSlotColors)} slot(s)? "
            f"Ribbons will revert to the faction palette.",
            parent=self.root,
        ):
            return
        self._captureHistory()
        self.manualSlotColors.clear()
        self._manualThumbCache.clear()
        self._redrawManualGrid()
        self.saveCurrentSettings()
        self.schedulePreview()
        # Refresh entry fields against whatever slot is currently selected
        # (now showing palette defaults since the override is gone).
        self._loadRibbonColorsIntoFields()
        self._toast("Reset all ribbon colors.")

    def _onManualRemove(self) -> None:
        """Delete the currently selected manual slot (or the last-placed one).

        If the user has explicitly selected a filled cell, removes that
        slot. Otherwise falls back to the highest-indexed occupied slot
        — convenient for repeatedly undoing the most-recent placement
        without having to click each cell first. No-op when the grid
        is empty.
        """
        target = self._manualSelectedSlot
        if target is None and self.manualRibbonSlots:
            target = max(self.manualRibbonSlots)
        if target is None or target not in self.manualRibbonSlots:
            return
        self._captureHistory()
        self.manualRibbonSlots.pop(target)
        self.manualSlotColors.pop(target, None)
        self._manualSelectedSlot = None
        self.manualHintLabel.config(text="Pick a ribbon, then click an empty slot.")
        self._redrawManualGrid()
        self.saveCurrentSettings()
        self.schedulePreview()

    def _onManualKeyDelete(self) -> str:
        """Delete key: drop the currently selected slot."""
        slot = self._manualSelectedSlot
        if slot is None or slot not in self.manualRibbonSlots:
            return "break"
        removed = self.manualRibbonSlots[slot]
        self._captureHistory()
        self.manualRibbonSlots.pop(slot)
        self.manualSlotColors.pop(slot, None)
        self.manualHintLabel.config(text=f"Removed {removed} from slot {slot + 1}.")
        self._redrawManualGrid()
        self.saveCurrentSettings()
        self.schedulePreview()
        return "break"

    def _onManualKeyPlace(self) -> str:
        """Enter key: place the dropdown ribbon into the selected empty slot."""
        slot = self._manualSelectedSlot
        pending = self.manualRibbonComboVar.get() if hasattr(self, "manualRibbonComboVar") else ""
        if slot is None or not pending or slot in self.manualRibbonSlots:
            return "break"
        self._captureHistory()
        self.manualRibbonSlots[slot] = pending
        self.manualHintLabel.config(text=f"Placed {pending} in slot {slot + 1}.")
        self._redrawManualGrid()
        self.saveCurrentSettings()
        self.schedulePreview()
        return "break"

    def _moveManualSelection(self, dx: int, dy: int) -> str:
        """Arrow keys: move the selection within the slot grid."""
        rects, _, _ = self._manualGridLayout()
        if not rects:
            return "break"
        # Build a lookup from slot_idx to (row, col) using rect order.
        # Rects are emitted row-by-row (top to bottom) but row 1 is at the
        # bottom, so build the grid array directly from positions.
        cells: dict[int, tuple[int, int]] = {}
        rows: dict[int, list[int]] = {}
        for slot_idx, x1, y1, _, _ in rects:
            rows.setdefault(y1, []).append(slot_idx)
        sorted_ys = sorted(rows.keys())
        for r, y in enumerate(sorted_ys):
            row_slots = sorted(rows[y], key=lambda s: next(rc[1] for rc in rects if rc[0] == s))
            for c, s in enumerate(row_slots):
                cells[s] = (r, c)
        if self._manualSelectedSlot is None or self._manualSelectedSlot not in cells:
            self._manualSelectedSlot = rects[0][0]
        else:
            r, c = cells[self._manualSelectedSlot]
            nr, nc = r + dy, c + dx
            target = None
            for s, (rr, cc) in cells.items():
                if rr == nr and cc == nc:
                    target = s
                    break
            if target is not None:
                self._manualSelectedSlot = target
        # Surface the selection into the recolor box.
        existing = self.manualRibbonSlots.get(self._manualSelectedSlot)
        if existing and existing in (self.manualRibbonCombo["values"] or ()):
            self.manualRibbonComboVar.set(existing)
        self._loadRibbonColorsIntoFields()
        self._redrawManualGrid()
        return "break"

    def _focusRecolorEntry(self) -> str:
        """R key: jump focus to the Border hex entry."""
        if not hasattr(self, "manualRecolorVars"):
            return "break"
        # Find the Entry widget for border and focus it.
        for child in self.root.winfo_children():
            pass  # Walk happens via tk.focus_set on the var's binder; simpler:
        # The Entry's textvariable is manualRecolorVars["border"] — grab its widget.
        for w in self.root.tk.call("info", "commands") if False else []:  # no-op
            pass
        # Simplest: focus the root and rely on Tab — but that's clunky. Instead,
        # remember the Border entry directly when we built it.
        widget = getattr(self, "_manualRecolorBorderEntry", None)
        if widget is not None:
            try:
                widget.focus_set()
                widget.select_range(0, "end")
            except tk.TclError:
                pass
        return "break"

    def _onManualReset(self) -> None:
        """Clear every manual slot in one shot.

        Captures history first (so the wipe is undoable), then drops all
        slot assignments, clears the selection, repaints the grid, and
        triggers a save + preview re-render. No-op when nothing is
        placed and no cell is selected.
        """
        if not self.manualRibbonSlots and self._manualSelectedSlot is None and not self.manualSlotColors:
            return
        self._captureHistory()
        self.manualRibbonSlots.clear()
        self.manualSlotColors.clear()
        self._manualSelectedSlot = None
        self.manualHintLabel.config(text="Pick a ribbon, then click an empty slot.")
        self._redrawManualGrid()
        self.saveCurrentSettings()
        self.schedulePreview()

    def _toggleBiggerPreview(self) -> None:
        if self.previewVisible:
            self.labelPreview.pack_forget()
            self.togglePreviewBtn.config(text="Show")
            self.previewVisible = False
        else:
            self.labelPreview.pack(pady=10)
            self.togglePreviewBtn.config(text="Hide")
            self.previewVisible = True

    # ----- Self-update --------------------------------------------------
    def _maybeAutoCheckUpdates(self) -> None:
        """On a packaged build, kick off a silent background update check.

        Only frozen (PyInstaller) builds can self-update — running from source
        has no .exe to replace — so the check is gated on ``sys.frozen``. The
        user can also turn it off in settings. Scheduled via ``after`` so it
        never blocks the first paint.
        """
        if not getattr(sys, "frozen", False):
            return
        if not self.settingsData.get("check_updates_on_startup", True):
            return
        self.root.after(1500, lambda: self._checkForUpdates(silent=True))

    def _checkForUpdates(self, silent: bool = False) -> None:
        """Hit GitHub Releases off the UI thread, then marshal the result back.

        ``silent`` suppresses the "you're up to date" / error dialogs for the
        automatic startup check, so a flaky network never nags the user.
        """
        def worker():
            try:
                info = updater.check_for_update(APP_VERSION)
                self.root.after(0, lambda: self._onUpdateCheckResult(info, silent))
            except Exception as exc:  # network/parse failure — non-fatal
                self.root.after(0, lambda e=exc: self._onUpdateCheckError(e, silent))

        if not silent:
            self.setStatus("Checking for updates…")
        threading.Thread(target=worker, daemon=True).start()

    def _onUpdateCheckError(self, exc: Exception, silent: bool) -> None:
        if silent:
            return
        self.setStatus("")
        messagebox.showerror(
            "Check for Updates",
            f"Could not check for updates:\n{exc}",
            parent=self.root,
        )

    def _onUpdateCheckResult(self, info: "updater.UpdateInfo", silent: bool) -> None:
        if not info.available:
            if not silent:
                self.setStatus("")
                messagebox.showinfo(
                    "Check for Updates",
                    f"You're up to date (v{APP_VERSION}).",
                    parent=self.root,
                )
            return
        # On the silent startup check, honor a version the user chose to skip.
        if silent and info.tag and info.tag == self.settingsData.get("skip_update_version"):
            return
        self.setStatus("")
        self._promptUpdate(info, silent)

    def _promptUpdate(self, info: "updater.UpdateInfo", silent: bool) -> None:
        notes = (info.notes or "").strip()
        if len(notes) > 800:
            notes = notes[:800].rstrip() + "…"
        body = (
            f"A new version is available.\n\n"
            f"Installed:  v{APP_VERSION}\n"
            f"Latest:      v{info.latest_version}\n"
        )
        if notes:
            body += f"\nWhat's new:\n{notes}\n"
        body += "\nDownload and install it now?"

        win = tk.Toplevel(self.root)
        win.title("Update Available")
        win.transient(self.root)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=body, justify="left", wraplength=420).pack(anchor="w")

        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(14, 0))

        def do_update():
            win.destroy()
            self._applyUpdate(info)

        def open_page():
            webbrowser.open(info.html_url or updater.RELEASES_PAGE)

        def skip():
            if info.tag:
                self.settingsData["skip_update_version"] = info.tag
                self.saveCurrentSettings()
            win.destroy()

        ttk.Button(btns, text="Update Now", command=do_update).pack(side="right")
        ttk.Button(btns, text="Later", command=win.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="Release Page", command=open_page).pack(side="left")
        if silent:
            ttk.Button(btns, text="Skip This Version", command=skip).pack(
                side="left", padx=(8, 0)
            )

    def _applyUpdate(self, info: "updater.UpdateInfo") -> None:
        """Download + stage the new build, then hand off to the .bat and quit."""
        if not info.asset_url:
            messagebox.showerror(
                "Update",
                "The release has no Windows download attached. "
                "Please update from the Release page.",
                parent=self.root,
            )
            return

        progress = tk.Toplevel(self.root)
        progress.title("Updating")
        progress.transient(self.root)
        progress.resizable(False, False)
        pframe = ttk.Frame(progress, padding=16)
        pframe.pack(fill="both", expand=True)
        status = ttk.Label(pframe, text="Starting download…", wraplength=360)
        status.pack(anchor="w")
        bar = ttk.Progressbar(pframe, length=360, mode="determinate", maximum=100)
        bar.pack(fill="x", pady=(10, 0))

        def set_progress(read: int, total: int):
            if total > 0:
                pct = int(read * 100 / total)
                self.root.after(0, lambda: (bar.config(value=pct),
                                            status.config(text=f"Downloading… {pct}%")))
            else:
                mb = read / (1024 * 1024)
                self.root.after(0, lambda: status.config(text=f"Downloading… {mb:.1f} MB"))

        def worker():
            try:
                tmp = tempfile.mkdtemp(prefix="aoer_update_")
                zip_path = updater.download_asset(
                    info.asset_url, tmp, progress_cb=set_progress
                )
                self.root.after(0, lambda: status.config(text="Extracting…"))
                staged = os.path.join(tmp, "staged")
                os.makedirs(staged, exist_ok=True)
                updater.stage_update(zip_path, staged)
                self.root.after(0, lambda: self._finishUpdate(staged, progress))
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._onUpdateApplyError(e, progress))

        threading.Thread(target=worker, daemon=True).start()

    def _onUpdateApplyError(self, exc: Exception, progress: "tk.Toplevel") -> None:
        try:
            progress.destroy()
        except Exception:
            pass
        messagebox.showerror(
            "Update", f"The update could not be installed:\n{exc}", parent=self.root
        )

    def _finishUpdate(self, staged_dir: str, progress: "tk.Toplevel") -> None:
        """Spawn the detached installer and exit so it can replace the .exe."""
        install_dir = baseDir
        relaunch_exe = sys.executable
        # The PyInstaller layout nests the .exe one level down inside the zip
        # (dist/AOER-Ribbon-engine/*). If a single top-level folder was staged,
        # copy from inside it so files land directly in install_dir.
        entries = [e for e in os.listdir(staged_dir)]
        if len(entries) == 1:
            only = os.path.join(staged_dir, entries[0])
            if os.path.isdir(only):
                staged_dir = only
        try:
            updater.apply_update_windows(staged_dir, install_dir, relaunch_exe)
        except Exception as exc:
            self._onUpdateApplyError(exc, progress)
            return
        try:
            progress.destroy()
        except Exception:
            pass
        # Hand control to the .bat: it waits for this PID to vanish before it can
        # overwrite the locked .exe, so we must exit now.
        self.root.destroy()

    def _showAbout(self) -> None:
        messagebox.showinfo(
            "About AOER Ribbon Engine",
            f"AOER Ribbon Engine\nVersion {APP_VERSION}\n\n"
            f"{updater.RELEASES_PAGE}",
            parent=self.root,
        )

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = RibbonEngineApp()
    app.run()


if __name__ == "__main__":
    main()
