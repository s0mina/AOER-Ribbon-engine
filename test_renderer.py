"""Headless regression tests for renderer.RibbonRenderer.

The whole point of extracting ``renderer.py`` was that the pixel core no longer
needs a display: it imports only PIL + ``profiles`` and takes its asset loaders
by injection. These tests exploit that — they feed in tiny stub ribbon images
and assert on the ``placements`` the renderer reports, with no Tkinter and no
real asset tree. The headline test is the "northwest" regression: a healthy
profile must place ribbons at their configured coordinates, and a wiped
(all-zero) profile must be distinguishable from it.

Run via: ``python -m unittest test_renderer``.
"""

import tempfile
import types
import unittest

from PIL import Image

import profiles
from profiles import DEFAULT_PART_COORDS, LayoutProfile
from renderer import RibbonRenderer

CATEGORIES = ("corpus", "nametape", "sacks", "commendations", "ribbons", "gorget", "spbadge")


def _item(name: str):
    """A stand-in for ribbonengine.AssetItem — the renderer only reads .name/.path."""
    return types.SimpleNamespace(name=name, path=f"/nonexistent/{name}.png")


def _make_renderer(layout: LayoutProfile, ribbon_names=()):
    """Build a renderer whose only assets are the named ribbons, each an 11x5 tile."""
    groups = {cat: [] for cat in CATEGORIES}
    groups["ribbons"] = [_item(n) for n in ribbon_names]

    tiles = {n: Image.new("RGBA", (11, 5), (200, 10, 10, 255)) for n in ribbon_names}

    def load_ribbon_image(item, factionKey=None):
        return tiles[item.name]

    def render_ribbon_with_colors(item, factionKey, colors):
        return tiles[item.name]

    def load_character_image(ch):
        raise FileNotFoundError(ch)

    return RibbonRenderer(
        groups,
        layout,
        load_ribbon_image=load_ribbon_image,
        render_ribbon_with_colors=render_ribbon_with_colors,
        load_character_image=load_character_image,
        # An empty temp dir => no Nameplate.png, so the nametape branch is inert.
        characters_dir=tempfile.gettempdir() + "/__renderer_test_no_such_dir__",
        award_medal_names=set(),
        bonus_medal_names=set(),
    )


def _render(layout: LayoutProfile, ribbon_names):
    renderer = _make_renderer(layout, ribbon_names)
    placements: list[dict] = []
    image, used, missing = renderer.buildImage(
        selectedNames=set(ribbon_names),
        nameplateText="",
        baseImage=None,
        requireNameForNew=False,
        errorCallback=None,
        faction=None,
        customOffsets={},
        placements=placements,
    )
    return image, placements


class NorthwestRegressionTests(unittest.TestCase):
    """The bug this whole refactor exists to make catchable."""

    def test_healthy_profile_places_ribbon_at_configured_coords(self):
        layout = LayoutProfile()  # canonical defaults: ribbons at (80, 33)
        image, placements = _render(layout, ["Alpha"])
        self.assertIsNotNone(image)
        self.assertEqual(len(placements), 1)
        p = placements[0]
        # A single centered ribbon: y is exactly the ribbons origin-y, x sits to
        # the right of origin-x. The cardinal sin would be (0, 0).
        self.assertEqual(p["y"], DEFAULT_PART_COORDS["ribbons"][1])
        self.assertGreaterEqual(p["x"], DEFAULT_PART_COORDS["ribbons"][0])
        self.assertNotEqual((p["x"], p["y"]), (0, 0))

    def test_zeroed_profile_renders_at_origin_and_is_flagged(self):
        zeroed = {k: (0, 0) for k in DEFAULT_PART_COORDS}
        layout = LayoutProfile.from_dict({"part_coords": zeroed})
        # The validator can tell this is a wiped profile up front...
        self.assertTrue(layout.looks_blank())
        image, placements = _render(layout, ["Alpha"])
        # ...and the actual render proves the failure mode: ribbon y at the top.
        self.assertEqual(placements[0]["y"], 0)

    def test_healthy_vs_zeroed_are_distinguishable(self):
        good = LayoutProfile()
        bad = LayoutProfile.from_dict({"part_coords": {k: (0, 0) for k in DEFAULT_PART_COORDS}})
        _, good_p = _render(good, ["Alpha"])
        _, bad_p = _render(bad, ["Alpha"])
        self.assertNotEqual(good_p[0]["y"], bad_p[0]["y"])
        self.assertFalse(good.looks_blank())
        self.assertTrue(bad.looks_blank())


class RibbonRowLayoutTests(unittest.TestCase):
    def test_multiple_ribbons_share_origin_row_y(self):
        layout = LayoutProfile()
        image, placements = _render(layout, ["A", "B", "C"])
        self.assertEqual(len(placements), 3)
        ys = {p["y"] for p in placements}
        # All three fit in the first (centered) row, so they share one y.
        self.assertEqual(ys, {DEFAULT_PART_COORDS["ribbons"][1]})
        xs = [p["x"] for p in placements]
        # Laid out left-to-right with 1px overlap (tile width 11).
        self.assertEqual(xs, sorted(xs))
        self.assertEqual(xs[1] - xs[0], 10)

    def test_custom_offset_shifts_placement(self):
        layout = LayoutProfile()
        renderer = _make_renderer(layout, ["A"])
        placements: list[dict] = []
        renderer.buildImage(
            selectedNames={"A"},
            nameplateText="",
            baseImage=None,
            requireNameForNew=False,
            errorCallback=None,
            customOffsets={"A": (5, 7)},
            placements=placements,
        )
        base = LayoutProfile()
        _, unshifted = _render(base, ["A"])
        self.assertEqual(placements[0]["x"], unshifted[0]["x"] + 5)
        self.assertEqual(placements[0]["y"], unshifted[0]["y"] + 7)


if __name__ == "__main__":
    unittest.main()
