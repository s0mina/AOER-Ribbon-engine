"""Layout configuration for the ribbon renderer.

This module owns the *render-relevant* slice of an Engine Profile: the part
coordinates, pocket/offset values, canvas size, and ribbon-row capacities that
``RibbonRenderer.buildImage`` needs to place things on the 128x128 canvas.

Historically all of this lived as ~dozen module-level globals in
``ribbonengine.py`` that ``applyProfile`` mutated and the renderer read directly.
That coupling is what made the renderer impossible to import or test without a
display, and it's why the "everything renders in the top-left" bug (a profile
with all-zero ``part_coords``) shipped with no test able to catch it.

``LayoutProfile`` bundles that state into one object you can build from a profile
dict, pass to the renderer, and assert against in a headless test. It imports
nothing but the standard library — no Tkinter, no PIL — so it's display-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# Order matters only for display; the renderer looks parts up by name.
PART_COORDS_KEYS: tuple[str, ...] = (
    "corpus",
    "nametape",
    "sacks",
    "commendations",
    "ribbons",
    "gorget",
    "spbadge",
)

# Canonical defaults for the stock 128x128 layout. This is the single source of
# truth for the Profile Editor's "Reset to defaults" button, the ``New…``
# profile template, and the fallback when a profile omits a coordinate.
DEFAULT_PART_COORDS: dict[str, tuple[int, int]] = {
    "corpus": (8, 16),
    "nametape": (13, 31),
    "sacks": (14, 62),
    "commendations": (8, 25),
    "ribbons": (80, 33),
    "gorget": (43, 0),
    "spbadge": (90, 59),
}


def _safe_int(value, default: int) -> int:
    """Coerce to int, falling back to ``default`` on garbage. Mirrors the
    ``_safeInt`` helper in ribbonengine.py so parsing semantics stay identical."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_part_coords(
    raw,
    base: dict[str, tuple[int, int]],
) -> dict[str, tuple[int, int]]:
    """Overlay a profile's ``part_coords`` block onto ``base``.

    Only keys we know about are accepted, and only well-formed ``[x, y]`` pairs
    of ints are applied — anything malformed leaves that part at its base value.
    This is the exact behavior of the original ``applyProfile`` coord loop, kept
    so behavior is byte-identical after the extraction.
    """
    out = dict(base)
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if key not in out:
            continue
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                out[key] = (int(value[0]), int(value[1]))
            except (TypeError, ValueError):
                continue
    return out


@dataclass(frozen=True)
class LayoutProfile:
    """The renderer's view of an Engine Profile.

    Built via :meth:`from_dict`. Frozen so a renderer can hold one without fear
    of it mutating underneath; use :meth:`with_part_coords` to derive a variant.
    """

    part_coords: dict[str, tuple[int, int]] = field(
        default_factory=lambda: dict(DEFAULT_PART_COORDS)
    )
    image_size: int = 128
    ribbon_area_width: int = 43
    max_medals_per_side: int = 3
    default_nameplate_width: int = 31
    nameplate_letter_spacing: int = 1
    # Pocket / alignment offsets. Canonical default.json ships these at 0;
    # real layouts (e.g. full_stack.json) override them.
    pocket_col_spacing: int = 0
    pocket_right_offset: int = 0
    pocket_x_offset: int = 0
    corpus_x_offset: int = 0
    ribbons_right_align_offset: int = 0
    # Ribbon row capacities. Defaults mirror the canonical globals in
    # ribbonengine.py so a profile that omits ribbon_rows parses identically.
    centered_row_capacity: int = 4
    right_start_row: int = 5
    right_first_row_capacity: int = 3
    right_subsequent_row_capacity: int = 2
    # Medal slot fill order ("left"/"middle"/"right"). Defaults match the
    # canonical medalSingleOrder / medalMultiOrder globals in ribbonengine.py.
    medal_single_order: tuple[str, ...] = ("middle", "left", "right")
    medal_multi_order: tuple[str, ...] = ("left", "middle", "right")

    @classmethod
    def from_dict(cls, profile: dict) -> "LayoutProfile":
        """Parse the render-relevant fields out of a full profile dict.

        Unknown / malformed fields fall back to the class defaults, matching the
        permissive parsing ``applyProfile`` has always done. Fields the renderer
        doesn't care about (themes, category labels, UI icons) are ignored here.
        """
        if not isinstance(profile, dict):
            profile = {}

        offsets = profile.get("offsets", {})
        if not isinstance(offsets, dict):
            offsets = {}
        rows = profile.get("ribbon_rows", {})
        if not isinstance(rows, dict):
            rows = {}

        defaults = cls()

        medals = profile.get("medals", {})
        single = defaults.medal_single_order
        multi = defaults.medal_multi_order
        if isinstance(medals, dict):
            single = _parse_order(medals.get("single_order"), single)
            multi = _parse_order(medals.get("multi_order"), multi)

        return cls(
            part_coords=_parse_part_coords(
                profile.get("part_coords"), dict(DEFAULT_PART_COORDS)
            ),
            image_size=max(1, _safe_int(profile.get("image_size"), defaults.image_size)),
            ribbon_area_width=max(
                1, _safe_int(profile.get("ribbon_area_width"), defaults.ribbon_area_width)
            ),
            max_medals_per_side=max(
                1, _safe_int(profile.get("max_medals_per_side"), defaults.max_medals_per_side)
            ),
            default_nameplate_width=max(
                1,
                _safe_int(
                    profile.get("default_nameplate_width"), defaults.default_nameplate_width
                ),
            ),
            nameplate_letter_spacing=max(
                0,
                _safe_int(
                    profile.get("nameplate_letter_spacing"), defaults.nameplate_letter_spacing
                ),
            ),
            pocket_col_spacing=_safe_int(
                offsets.get("pocket_col_spacing"), defaults.pocket_col_spacing
            ),
            pocket_right_offset=_safe_int(
                offsets.get("pocket_right_offset"), defaults.pocket_right_offset
            ),
            pocket_x_offset=_safe_int(offsets.get("pocket_x_offset"), defaults.pocket_x_offset),
            corpus_x_offset=_safe_int(offsets.get("corpus_x_offset"), defaults.corpus_x_offset),
            ribbons_right_align_offset=_safe_int(
                offsets.get("ribbons_right_align_offset"), defaults.ribbons_right_align_offset
            ),
            centered_row_capacity=max(
                1, _safe_int(rows.get("centered_row_capacity"), defaults.centered_row_capacity)
            ),
            right_start_row=max(
                1, _safe_int(rows.get("right_start_row"), defaults.right_start_row)
            ),
            right_first_row_capacity=max(
                1,
                _safe_int(
                    rows.get("first_right_row_capacity"), defaults.right_first_row_capacity
                ),
            ),
            right_subsequent_row_capacity=max(
                1,
                _safe_int(
                    rows.get("subsequent_right_row_capacity"),
                    defaults.right_subsequent_row_capacity,
                ),
            ),
            medal_single_order=single,
            medal_multi_order=multi,
        )

    def with_part_coords(self, coords: dict[str, tuple[int, int]]) -> "LayoutProfile":
        """Return a copy with ``part_coords`` replaced (handy in tests)."""
        return replace(self, part_coords=dict(coords))

    def degenerate_part_coords(self) -> list[str]:
        """Return the part names whose coordinate is (0, 0).

        An all-zero coordinate means that part renders in the top-left corner —
        the exact "everything went northwest" failure mode. The default layout
        legitimately has gorget *y*=0 but never a full (0, 0), so a part landing
        at (0, 0) is almost always a wiped/blank profile. Callers can warn on a
        non-empty result. ``gorget`` is excluded from being *solely* responsible
        only by virtue of its x being non-zero in the default; here we report any
        exact (0, 0), which never occurs in a healthy profile.
        """
        return [name for name, (x, y) in self.part_coords.items() if x == 0 and y == 0]

    def looks_blank(self) -> bool:
        """True if *every* part sits at (0, 0) — a profile that would render the
        whole composite in the top-left corner. This is the strong signal that a
        profile's ``part_coords`` were never populated (the northwest bug)."""
        return all(x == 0 and y == 0 for (x, y) in self.part_coords.values())


def _parse_order(raw, default: tuple[str, ...]) -> tuple[str, ...]:
    """Validate a medal fill order list against the legal slot tokens."""
    if not isinstance(raw, list):
        return default
    legal = {"left", "middle", "right"}
    cleaned = tuple(tok for tok in raw if isinstance(tok, str) and tok in legal)
    return cleaned or default
