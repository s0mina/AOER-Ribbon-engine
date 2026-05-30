from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Optional

from PIL import Image

_EMPTY_DESCRIPTIONS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True)
class FactionPalette:
    base_color: tuple[int, int, int]
    stripe_color: tuple[int, int, int]
    border_color: tuple[int, int, int]


@dataclass(frozen=True)
class Faction:
    key: str
    display_name: str
    ribbon_groups: tuple[str, ...]
    palette: FactionPalette
    assets: frozenset[str] = frozenset()
    hidden: bool = False
    # Asset names that should bypass recolor entirely — e.g. ribbons with
    # one-off art a designer does not want tinted by the faction palette.
    no_recolor: frozenset[str] = frozenset()
    # Per-asset description text shown in the hover preview tooltip.
    # Wrapped in MappingProxyType so it can't be mutated through a "frozen" Faction.
    descriptions: Mapping[str, str] = _EMPTY_DESCRIPTIONS
    # Optional Engine Profile name to switch to when this faction is selected.
    # If empty, the active profile is left unchanged.
    engine_profile: str = ""


@dataclass
class FactionRegistry:
    factions: dict[str, Faction] = field(default_factory=dict)
    default_key: str = "AOER"
    border_thickness: int = 1
    recolor_mode: str = "tint"
    shared_assets: frozenset[str] = frozenset()

    def visible_assets(self, faction_key: Optional[str]) -> frozenset[str]:
        """Asset names that should be selectable when the given faction is active.

        Combines the faction's own assets, global `shared_assets`, and the
        assets of every hidden faction (which act as corp-wide pools).
        Empty result means 'no allowlist configured' — callers treat that as
        'show everything' for backward compatibility.
        """
        faction = self.get(faction_key)
        return faction.assets | self.shared_assets | self._hidden_assets()

    def get(self, key: Optional[str]) -> Faction:
        if key and key in self.factions:
            return self.factions[key]
        if self.default_key in self.factions:
            return self.factions[self.default_key]
        if self.factions:
            return next(iter(self.factions.values()))
        raise KeyError("No factions defined")

    def names(self) -> list[str]:
        """Keys of user-selectable factions (hidden ones omitted)."""
        return [k for k, f in self.factions.items() if not f.hidden]

    def selectable_default_key(self) -> str:
        """Default for the UI dropdown — never returns a hidden faction."""
        if self.default_key in self.factions and not self.factions[self.default_key].hidden:
            return self.default_key
        for key, faction in self.factions.items():
            if not faction.hidden:
                return key
        return self.default_key

    def _hidden_assets(self) -> frozenset[str]:
        result: set[str] = set()
        for faction in self.factions.values():
            if faction.hidden:
                result |= faction.assets
        return frozenset(result)

    def hidden_faction_for(self, asset_name: str) -> Optional[Faction]:
        """Hidden faction that owns `asset_name`, or None if it's only in
        `shared_assets` (or unknown)."""
        for faction in self.factions.values():
            if faction.hidden and asset_name in faction.assets:
                return faction
        return None

    def is_shared_asset(self, asset_name: str) -> bool:
        """True if the asset is corp-wide (owned by `shared_assets` or any
        hidden faction). Shared assets render in their original colors and
        skip per-faction recoloring."""
        if asset_name in self.shared_assets:
            return True
        return asset_name in self._hidden_assets()

    def is_no_recolor(self, asset_name: str) -> bool:
        """True if the asset is in any faction's `no_recolor` list — meaning
        the recolor pipeline should pass it through unchanged."""
        for faction in self.factions.values():
            if asset_name in faction.no_recolor:
                return True
        return False

    def description_for(self, asset_name: str, faction_key: Optional[str] = None) -> str:
        """Look up the description text for an asset.

        Prefers the active faction's description, falls back to any other
        faction (e.g. hidden corp-wide assets), returns "" if not found.
        """
        if faction_key is not None:
            faction = self.factions.get(faction_key)
            if faction:
                text = faction.descriptions.get(asset_name)
                if text:
                    return text
        for faction in self.factions.values():
            text = faction.descriptions.get(asset_name)
            if text:
                return text
        return ""

    def engine_profile_for(self, faction_key: Optional[str]) -> str:
        """Engine Profile name bound to this faction, or "" if none."""
        faction = self.factions.get(faction_key) if faction_key else None
        if faction is None:
            return ""
        return faction.engine_profile

    def validate_assets(
        self,
        available: dict[str, set[str]],
    ) -> list[str]:
        """Return a list of human-readable warnings for asset config mistakes.

        The filesystem is the allowlist: whatever PNGs are physically present
        under ``assets/<FACTION>/{ribbons,awards,commendations}/`` show up, and
        the JSON ``assets`` / ``shared_assets`` lists are NOT consulted for
        visibility. We therefore do NOT warn about names listed in those fields
        that lack a PNG on disk — listing a file in JSON is optional and absence
        is normal (e.g. a recipient who wasn't shipped that PNG).

        We still flag genuine config typos: ``no_recolor`` and ``descriptions``
        keys that name an asset which doesn't exist, since those override the
        behavior/tooltip of a specific file and a stray name is almost always a
        mistake.

        `available` is `{category_name: {asset_name, ...}}` covering all
        directories the engine knows about. Matching is case-sensitive on the
        filename stem.
        """
        all_available: set[str] = set()
        for names in available.values():
            all_available |= names

        warnings: list[str] = []
        for key, faction in self.factions.items():
            stray_no_recolor = sorted(
                name for name in faction.no_recolor if name not in all_available
            )
            if stray_no_recolor:
                warnings.append(
                    f"Faction {key!r} no_recolor references missing asset(s): "
                    f"{', '.join(stray_no_recolor)}"
                )
            stray_descriptions = sorted(
                name for name in faction.descriptions if name not in all_available
            )
            if stray_descriptions:
                warnings.append(
                    f"Faction {key!r} descriptions reference missing asset(s): "
                    f"{', '.join(stray_descriptions)}"
                )
        return warnings


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    cleaned = value.lstrip("#")
    if len(cleaned) != 6:
        raise ValueError(f"Invalid hex color: {value!r}")
    return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))


def _parse_faction_spec(key: str, spec: dict) -> Faction:
    palette_raw = spec.get("color_palette", {})
    try:
        palette = FactionPalette(
            base_color=_hex_to_rgb(palette_raw.get("base_color", "#000000")),
            stripe_color=_hex_to_rgb(palette_raw.get("stripe_color", "#888888")),
            border_color=_hex_to_rgb(palette_raw.get("border_color", "#ffffff")),
        )
    except ValueError as exc:
        raise ValueError(f"Faction {key!r}: {exc}") from exc

    groups_raw = spec.get("ribbon_groups", [])
    groups = tuple(str(g) for g in groups_raw if isinstance(g, str))

    assets_raw = spec.get("assets", [])
    assets = frozenset(str(a).strip() for a in assets_raw if isinstance(a, str) and a.strip())

    no_recolor_raw = spec.get("no_recolor", [])
    no_recolor = frozenset(
        str(a).strip() for a in no_recolor_raw if isinstance(a, str) and a.strip()
    )

    descriptions_raw = spec.get("descriptions", {})
    descriptions: dict[str, str] = {}
    if isinstance(descriptions_raw, dict):
        for asset_name, text in descriptions_raw.items():
            if isinstance(asset_name, str) and isinstance(text, str) and asset_name.strip():
                descriptions[asset_name.strip()] = text.strip()

    return Faction(
        key=key,
        display_name=str(spec.get("display_name", key)),
        ribbon_groups=groups,
        palette=palette,
        assets=assets,
        hidden=bool(spec.get("hidden", False)),
        no_recolor=no_recolor,
        descriptions=MappingProxyType(descriptions),
        engine_profile=str(spec.get("engine_profile", "")).strip(),
    )


def _load_from_directory(directory: str) -> FactionRegistry:
    """Load one faction per *.json file. `_global.json` carries shared config."""
    factions: dict[str, Faction] = {}
    global_cfg: dict = {}

    for filename in sorted(os.listdir(directory), key=str.lower):
        if not filename.lower().endswith(".json"):
            continue
        path = os.path.join(directory, filename)
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Failed to read {filename}: {exc}") from exc
        if not isinstance(data, dict):
            continue

        if filename.lower() == "_global.json":
            global_cfg = data
            continue

        key = str(data.get("key", os.path.splitext(filename)[0])).strip()
        if not key:
            continue
        factions[key] = _parse_faction_spec(key, data)

    if not factions:
        raise ValueError(f"No faction files found in {directory!r}")

    default_key = global_cfg.get("default_faction") or next(iter(factions))
    if default_key not in factions:
        default_key = next(iter(factions))

    shared_raw = global_cfg.get("shared_assets", [])
    shared = frozenset(str(a).strip() for a in shared_raw if isinstance(a, str) and a.strip())

    recolor_cfg = global_cfg.get("recolor", {})
    return FactionRegistry(
        factions=factions,
        default_key=default_key,
        border_thickness=int(recolor_cfg.get("border_thickness", 1)) if isinstance(recolor_cfg, dict) else 1,
        recolor_mode=str(recolor_cfg.get("mode", "tint")) if isinstance(recolor_cfg, dict) else "tint",
        shared_assets=shared,
    )


def load_faction_registry(path: str) -> FactionRegistry:
    """Load registry from either a `factions/` directory or a `factions.json` file.

    Directory form (preferred) — one JSON per faction, distributable per recipient.
    File form — single aggregated JSON, kept for backward compatibility.
    """
    if os.path.isdir(path):
        return _load_from_directory(path)
    # Fallback: treat `path` as the legacy single-file config. If `factions/`
    # exists as a sibling next to it, prefer the directory.
    sibling_dir = os.path.join(os.path.dirname(path), "factions")
    if os.path.isdir(sibling_dir):
        return _load_from_directory(sibling_dir)

    if not os.path.exists(path):
        raise FileNotFoundError(f"factions config not found at {path}")
    with open(path, "r", encoding="utf-8-sig") as handle:
        raw = json.load(handle)

    if not isinstance(raw, dict):
        raise ValueError("factions.json must be a JSON object")

    factions_raw = raw.get("factions", {})
    if not isinstance(factions_raw, dict) or not factions_raw:
        raise ValueError("factions.json must define at least one faction under 'factions'")

    factions: dict[str, Faction] = {}
    for key, spec in factions_raw.items():
        if not isinstance(spec, dict):
            continue
        factions[key] = _parse_faction_spec(key, spec)

    default_key = raw.get("default_faction") or next(iter(factions))
    if default_key not in factions:
        default_key = next(iter(factions))

    recolor_cfg = raw.get("recolor", {})
    shared_raw = raw.get("shared_assets", [])
    shared = frozenset(str(a).strip() for a in shared_raw if isinstance(a, str) and a.strip())

    return FactionRegistry(
        factions=factions,
        default_key=default_key,
        border_thickness=int(recolor_cfg.get("border_thickness", 1)) if isinstance(recolor_cfg, dict) else 1,
        recolor_mode=str(recolor_cfg.get("mode", "tint")) if isinstance(recolor_cfg, dict) else "tint",
        shared_assets=shared,
    )


# ----------------------------------------------------------------------------
# Recolor pipeline
# ----------------------------------------------------------------------------
#
# Each non-transparent pixel of a ribbon image is classified as one of three
# regions and remapped to a color from the active palette:
#
#   - border : pixels within `border_thickness` of the image edge
#   - stripe : interior pixels with luminance >= STRIPE_LUMINANCE_THRESHOLD
#   - base   : interior pixels darker than that threshold
#
# The classification reads only the source pixel's *position* and *luminance*,
# never its hue — so a designer who ships a ribbon with wrong colors still
# renders correctly under any faction (as long as their light/dark contrast
# survives).
#
# `RecolorOptions` flips each region on or off independently. Disabled regions
# pass the source pixel through unchanged. This lets users (via the Settings
# dialog) choose, for example, "force the border but leave the designer's
# stripe and base art alone" — useful for corp-wide ribbons where you want a
# locked-in border but creative freedom inside.

STRIPE_LUMINANCE_THRESHOLD = 140


@dataclass(frozen=True)
class RecolorOptions:
    """Which palette components are applied during recolor.

    Defaults match the original AOER-style policy: border locked to the
    faction's `border_color`, interior pixels passed through. Toggle stripe
    or base on via the Settings dialog (saved to `settings.json`).
    """
    border: bool = True
    stripe: bool = False
    base: bool = False

    @property
    def is_passthrough(self) -> bool:
        return not (self.border or self.stripe or self.base)

    def cache_token(self) -> str:
        return f"b{int(self.border)}s{int(self.stripe)}B{int(self.base)}"


def _tint(target: tuple[int, int, int], luminance: int) -> tuple[int, int, int]:
    """Luminance-preserving tint of `target` by source `luminance` (0–255).

    The target color sits at midtone (luminance 128). Brighter source pixels
    lerp toward white, darker ones lerp toward black — so highlights, shadows,
    and fine detail in the source survive the recolor instead of collapsing
    to a flat swatch.
    """
    if luminance >= 128:
        t = (luminance - 128) / 127.0
        return (
            int(target[0] + (255 - target[0]) * t),
            int(target[1] + (255 - target[1]) * t),
            int(target[2] + (255 - target[2]) * t),
        )
    t = luminance / 128.0
    return (int(target[0] * t), int(target[1] * t), int(target[2] * t))


try:
    import numpy as _np  # type: ignore

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover — NumPy is in requirements, but fall back gracefully.
    _np = None
    _HAS_NUMPY = False


def _recolor_ribbon_numpy(
    image: Image.Image,
    faction: Faction,
    options: RecolorOptions,
    border_thickness: int,
) -> Image.Image:
    """Vectorized recolor — ~50× faster than the per-pixel loop on a 16×16 ribbon."""
    arr = _np.array(image, dtype=_np.int32)  # shape (h, w, 4), RGBA. int32 to avoid
                                              # int16 overflow when computing luminance.
    h, w, _ = arr.shape
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3]

    out = arr.copy()
    opaque = alpha > 0
    # Match the Python path: zero the RGB of fully-transparent pixels so the
    # two implementations produce byte-identical output.
    out[~opaque, :3] = 0

    # Per-pixel luminance (Rec. 601 weights, matches the scalar implementation).
    lum = (rgb[:, :, 0] * 299 + rgb[:, :, 1] * 587 + rgb[:, :, 2] * 114) // 1000

    # Region masks.
    yy, xx = _np.indices((h, w))
    border_mask = (
        (xx < border_thickness)
        | (yy < border_thickness)
        | (xx >= w - border_thickness)
        | (yy >= h - border_thickness)
    ) & opaque
    interior_mask = opaque & ~border_mask
    stripe_mask = interior_mask & (lum >= STRIPE_LUMINANCE_THRESHOLD)
    base_mask = interior_mask & ~stripe_mask

    if options.border and border_mask.any():
        out[border_mask, 0] = faction.palette.border_color[0]
        out[border_mask, 1] = faction.palette.border_color[1]
        out[border_mask, 2] = faction.palette.border_color[2]

    def apply_tint(mask, target):
        if not mask.any():
            return
        sub_lum = lum[mask]
        # Highlight branch: t = (lum - 128) / 127, lerp(target -> white).
        # Shadow branch:    t = lum / 128,         lerp(black -> target).
        bright = sub_lum >= 128
        dark = ~bright
        t_bright = (sub_lum[bright] - 128) / 127.0
        t_dark = sub_lum[dark] / 128.0

        # Pre-allocate per-channel output for the masked pixels.
        masked_rgb = _np.empty((sub_lum.shape[0], 3), dtype=_np.int32)
        for i, ch in enumerate(target):
            channel = _np.empty(sub_lum.shape[0], dtype=_np.float32)
            channel[bright] = ch + (255 - ch) * t_bright
            channel[dark] = ch * t_dark
            masked_rgb[:, i] = channel.astype(_np.int32)
        out[mask, 0] = masked_rgb[:, 0]
        out[mask, 1] = masked_rgb[:, 1]
        out[mask, 2] = masked_rgb[:, 2]

    if options.stripe:
        apply_tint(stripe_mask, faction.palette.stripe_color)
    if options.base:
        apply_tint(base_mask, faction.palette.base_color)

    out = _np.clip(out, 0, 255).astype(_np.uint8)
    return Image.fromarray(out, mode="RGBA")


def _recolor_ribbon_python(
    image: Image.Image,
    faction: Faction,
    options: RecolorOptions,
    border_thickness: int,
) -> Image.Image:
    """Pure-Python pixel loop. Used when NumPy isn't installed."""
    src = image.load()
    w, h = image.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dst = out.load()

    base = faction.palette.base_color
    stripe = faction.palette.stripe_color
    border = faction.palette.border_color

    for y in range(h):
        for x in range(w):
            r, g, b, a = src[x, y]
            if a == 0:
                dst[x, y] = (0, 0, 0, 0)
                continue

            on_border = (
                x < border_thickness
                or y < border_thickness
                or x >= w - border_thickness
                or y >= h - border_thickness
            )
            if on_border:
                dst[x, y] = (*border, a) if options.border else (r, g, b, a)
                continue

            luminance = (r * 299 + g * 587 + b * 114) // 1000
            if luminance >= STRIPE_LUMINANCE_THRESHOLD:
                dst[x, y] = ((*_tint(stripe, luminance), a) if options.stripe
                             else (r, g, b, a))
            else:
                dst[x, y] = ((*_tint(base, luminance), a) if options.base
                             else (r, g, b, a))

    return out


def recolor_ribbon(
    image: Image.Image,
    faction: Faction,
    options: Optional[RecolorOptions] = None,
    border_thickness: int = 1,
) -> Image.Image:
    """Apply the faction's palette to a ribbon image, gated by `options`.

    Border pixels are replaced with a flat color (a 1px ring should be uniform).
    Stripe/base pixels are *tinted* — the target color is shifted by the source
    pixel's luminance so highlights, shadows, and detail in the original art
    survive. Disabled regions pass through unmodified.

    Uses NumPy when available (vectorized, ~50× faster); falls back to a pure-
    Python pixel loop otherwise.
    """
    if options is None:
        options = RecolorOptions()

    if image.mode != "RGBA":
        image = image.convert("RGBA")

    if options.is_passthrough:
        return image.copy()

    if _HAS_NUMPY:
        return _recolor_ribbon_numpy(image, faction, options, border_thickness)
    return _recolor_ribbon_python(image, faction, options, border_thickness)


def faction_cache_key(asset_path: str, faction_key: str, options: RecolorOptions) -> str:
    return f"{asset_path}::{faction_key}::{options.cache_token()}"


class FactionRecolorCache:
    """Memoizes recolored ribbons keyed on (path, faction, options).

    Toggling Settings invalidates entries automatically because the cache key
    includes the options token — but callers can also `clear()` explicitly
    after a settings change to free memory.
    """

    def __init__(self, registry: FactionRegistry):
        self.registry = registry
        self._cache: dict[str, Image.Image] = {}

    def get(
        self,
        asset_path: str,
        faction_key: str,
        source: Image.Image,
        options: RecolorOptions,
    ) -> Image.Image:
        key = faction_cache_key(asset_path, faction_key, options)
        cached = self._cache.get(key)
        if cached is not None:
            return cached.copy()

        if options.is_passthrough:
            self._cache[key] = source.copy()
            return source.copy()

        faction = self.registry.get(faction_key)
        recolored = recolor_ribbon(source, faction, options, self.registry.border_thickness)
        self._cache[key] = recolored
        return recolored.copy()

    def clear(self) -> None:
        self._cache.clear()
