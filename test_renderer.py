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


class MedalSlotOffsetTests(unittest.TestCase):
    """Per-slot (x, y) nudges added to a medal's auto-computed pocket spot."""

    def _render_award(self, layout):
        groups = {cat: [] for cat in CATEGORIES}
        groups["sacks"] = [_item("Star")]
        tile = Image.new("RGBA", (11, 18), (10, 200, 10, 255))

        def load_ribbon_image(item, factionKey=None):
            return tile

        renderer = RibbonRenderer(
            groups,
            layout,
            load_ribbon_image=load_ribbon_image,
            render_ribbon_with_colors=lambda item, fk, c: tile,
            load_character_image=lambda ch: (_ for _ in ()).throw(FileNotFoundError(ch)),
            characters_dir=tempfile.gettempdir() + "/__renderer_test_no_such_dir__",
            award_medal_names={"Star"},
            bonus_medal_names=set(),
        )
        placements: list[dict] = []
        renderer.buildImage(
            selectedNames={"Star"},
            nameplateText="",
            baseImage=None,
            requireNameForNew=False,
            errorCallback=None,
            customOffsets={},
            placements=placements,
        )
        return placements

    def test_award_slot_offset_shifts_placement(self):
        base = self._render_award(LayoutProfile())
        shifted = self._render_award(
            LayoutProfile.from_dict(
                {"medal_slot_offsets": {"award_1_x": 4, "award_1_y": -3}}
            )
        )
        self.assertEqual(len(base), 1)
        self.assertEqual(len(shifted), 1)
        self.assertEqual(shifted[0]["x"], base[0]["x"] + 4)
        self.assertEqual(shifted[0]["y"], base[0]["y"] - 3)

    def test_zero_offsets_leave_placement_unchanged(self):
        base = self._render_award(LayoutProfile())
        same = self._render_award(
            LayoutProfile.from_dict({"medal_slot_offsets": {"award_1_x": 0, "award_1_y": 0}})
        )
        self.assertEqual(base[0]["x"], same[0]["x"])
        self.assertEqual(base[0]["y"], same[0]["y"])


class MedalRowLayoutTests(unittest.TestCase):
    """Award row under the ribbons (right), bonus row under the nametape (left),
    auto-spaced so duplicates never stack, badge replaces the bonus row."""

    def _make(self, layout, medal_names, badge_names=()):
        groups = {cat: [] for cat in CATEGORIES}
        groups["sacks"] = [_item(n) for n in medal_names]
        groups["spbadge"] = [_item(n) for n in badge_names]
        tiles = {n: Image.new("RGBA", (11, 18), (10, 200, 10, 255)) for n in medal_names}
        tiles.update({n: Image.new("RGBA", (11, 18), (10, 10, 200, 255)) for n in badge_names})

        def load_ribbon_image(item, factionKey=None):
            return tiles[item.name]

        return RibbonRenderer(
            groups,
            layout,
            load_ribbon_image=load_ribbon_image,
            render_ribbon_with_colors=lambda item, fk, c: tiles[item.name],
            load_character_image=lambda ch: (_ for _ in ()).throw(FileNotFoundError(ch)),
            characters_dir=tempfile.gettempdir() + "/__renderer_test_no_such_dir__",
            award_medal_names=set(),
            bonus_medal_names=set(),
        )

    def _render(self, renderer, **kw):
        placements: list[dict] = []
        renderer.buildImage(
            selectedNames=set(),
            nameplateText="",
            baseImage=None,
            requireNameForNew=False,
            errorCallback=None,
            customOffsets={},
            placements=placements,
            **kw,
        )
        return placements

    def test_three_identical_award_medals_do_not_stack(self):
        # The reported bug: 3 of the same medal collapsed onto one spot because
        # pocket_col_spacing defaulted to 0. Auto width-spacing must spread them.
        renderer = self._make(LayoutProfile(), ["Star"])
        placements = self._render(renderer, awardSlots=["Star", "Star", "Star"])
        self.assertEqual(len(placements), 3)
        xs = sorted(p["x"] for p in placements)
        self.assertEqual(len(set(xs)), 3)  # three distinct x positions
        # Neighbours separated by at least the tile width (11) so they don't overlap.
        self.assertGreaterEqual(xs[1] - xs[0], 11)
        self.assertGreaterEqual(xs[2] - xs[1], 11)

    def test_award_row_is_right_of_bonus_row_and_shares_y(self):
        renderer = self._make(LayoutProfile(), ["A", "B"])
        placements = self._render(renderer, awardSlots=["A"], bonusSlots=["B"])
        byname = {p["name"]: p for p in placements}
        # Award (under ribbons) sits to the right of bonus (under nametape)...
        self.assertGreater(byname["A"]["x"], byname["B"]["x"])
        # ...and both rows share the same Y anchor.
        self.assertEqual(byname["A"]["y"], byname["B"]["y"])

    def test_explicit_award_spacing_overrides_auto(self):
        # award_spacing forces an exact center-to-center gap, ignoring the
        # width-based auto spacing (tiles are 11px wide).
        layout = LayoutProfile.from_dict({"medal_slot_offsets": {"award_spacing": 20}})
        renderer = self._make(layout, ["A"])
        placements = self._render(renderer, awardSlots=["A", "A", "A"])
        xs = sorted(p["x"] for p in placements)
        self.assertEqual(len(xs), 3)
        self.assertEqual(xs[1] - xs[0], 20)
        self.assertEqual(xs[2] - xs[1], 20)

    def test_department_badge_replaces_bonus_row(self):
        renderer = self._make(LayoutProfile(), ["A", "B"], badge_names=["DeptX"])
        placements = self._render(
            renderer,
            awardSlots=["A"],
            bonusSlots=["B"],
            departmentBadge="DeptX",
        )
        names = {p["name"] for p in placements}
        # Award stays, bonus is gone, badge takes the bonus row.
        self.assertIn("A", names)
        self.assertIn("DeptX", names)
        self.assertNotIn("B", names)

    def test_bonus_slot_offset_shifts_placement(self):
        # The bonus row is the mirror of the award row: per-slot offsets apply
        # identically. Verifies bonus_1 nudge moves the first bonus medal.
        base = self._render(self._make(LayoutProfile(), ["B"]), bonusSlots=["B"])
        shifted = self._render(
            self._make(
                LayoutProfile.from_dict(
                    {"medal_slot_offsets": {"bonus_1_x": 3, "bonus_1_y": -2}}
                ),
                ["B"],
            ),
            bonusSlots=["B"],
        )
        self.assertEqual(shifted[0]["x"], base[0]["x"] + 3)
        self.assertEqual(shifted[0]["y"], base[0]["y"] - 2)

    def test_bonus_spacing_override(self):
        layout = LayoutProfile.from_dict({"medal_slot_offsets": {"bonus_spacing": 18}})
        placements = self._render(self._make(layout, ["B"]), bonusSlots=["B", "B", "B"])
        xs = sorted(p["x"] for p in placements)
        self.assertEqual(len(xs), 3)
        self.assertEqual(xs[1] - xs[0], 18)
        self.assertEqual(xs[2] - xs[1], 18)

    def test_award_row_capped_at_max_per_side(self):
        # A 4th award slot beyond max_medals_per_side (default 3) is dropped.
        renderer = self._make(LayoutProfile(), ["A"])
        placements = self._render(renderer, awardSlots=["A", "A", "A", "A"])
        self.assertEqual(len(placements), 3)


if __name__ == "__main__":
    unittest.main()
