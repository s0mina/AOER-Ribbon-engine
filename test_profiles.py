"""Headless tests for profiles.LayoutProfile.

These import only ``profiles`` (stdlib-only) so they run on a box with no
display — which is the whole point of pulling layout config out of the
Tkinter monolith. Run via: ``python -m unittest test_profiles``.
"""

import unittest

import profiles
from profiles import DEFAULT_PART_COORDS, LayoutProfile


class FromDictTests(unittest.TestCase):
    def test_empty_dict_yields_canonical_defaults(self):
        lp = LayoutProfile.from_dict({})
        self.assertEqual(lp.part_coords, dict(DEFAULT_PART_COORDS))
        self.assertEqual(lp.image_size, 128)
        self.assertEqual(lp.ribbon_area_width, 43)

    def test_part_coords_override_applied(self):
        lp = LayoutProfile.from_dict(
            {"part_coords": {"nametape": [20, 40], "ribbons": [81, 33]}}
        )
        self.assertEqual(lp.part_coords["nametape"], (20, 40))
        self.assertEqual(lp.part_coords["ribbons"], (81, 33))
        # Untouched parts keep their canonical default.
        self.assertEqual(lp.part_coords["corpus"], DEFAULT_PART_COORDS["corpus"])

    def test_malformed_coord_falls_back(self):
        lp = LayoutProfile.from_dict(
            {"part_coords": {"nametape": [1], "ribbons": "nope", "corpus": ["a", "b"]}}
        )
        # All three malformed entries should retain the default, not crash.
        self.assertEqual(lp.part_coords["nametape"], DEFAULT_PART_COORDS["nametape"])
        self.assertEqual(lp.part_coords["ribbons"], DEFAULT_PART_COORDS["ribbons"])
        self.assertEqual(lp.part_coords["corpus"], DEFAULT_PART_COORDS["corpus"])

    def test_unknown_part_key_ignored(self):
        lp = LayoutProfile.from_dict({"part_coords": {"bogus": [5, 5]}})
        self.assertNotIn("bogus", lp.part_coords)

    def test_offsets_and_rows_parsed(self):
        lp = LayoutProfile.from_dict(
            {
                "offsets": {"pocket_col_spacing": 9, "corpus_x_offset": -3},
                "ribbon_rows": {"centered_row_capacity": 6},
            }
        )
        self.assertEqual(lp.pocket_col_spacing, 9)
        self.assertEqual(lp.corpus_x_offset, -3)
        self.assertEqual(lp.centered_row_capacity, 6)

    def test_garbage_scalars_clamped_to_defaults(self):
        lp = LayoutProfile.from_dict(
            {"image_size": "huge", "max_medals_per_side": None, "ribbon_area_width": -10}
        )
        self.assertEqual(lp.image_size, 128)
        self.assertEqual(lp.max_medals_per_side, 3)
        self.assertEqual(lp.ribbon_area_width, 1)  # clamped to min 1, not negative

    def test_non_dict_profile_is_safe(self):
        lp = LayoutProfile.from_dict(None)  # type: ignore[arg-type]
        self.assertEqual(lp.part_coords, dict(DEFAULT_PART_COORDS))

    def test_medal_order_validated(self):
        lp = LayoutProfile.from_dict(
            {"medals": {"single_order": ["middle"], "multi_order": ["left", "bogus", "right"]}}
        )
        self.assertEqual(lp.medal_single_order, ("middle",))
        # "bogus" dropped, legal tokens kept.
        self.assertEqual(lp.medal_multi_order, ("left", "right"))


class NorthwestRegressionTests(unittest.TestCase):
    """Guards against the 'everything renders in the top-left' bug."""

    def test_default_profile_is_not_blank(self):
        # The canonical default must NOT be degenerate — if this ever fails,
        # a fresh/default profile would render everything at the origin.
        self.assertFalse(LayoutProfile().looks_blank())
        self.assertEqual(LayoutProfile().degenerate_part_coords(), [])

    def test_all_zero_coords_flagged_as_blank(self):
        zeroed = {k: (0, 0) for k in DEFAULT_PART_COORDS}
        lp = LayoutProfile.from_dict({"part_coords": zeroed})
        self.assertTrue(lp.looks_blank())
        # Every part is reported degenerate.
        self.assertEqual(
            sorted(lp.degenerate_part_coords()), sorted(DEFAULT_PART_COORDS.keys())
        )

    def test_partial_zero_detected_but_not_blank(self):
        lp = LayoutProfile().with_part_coords(
            {**DEFAULT_PART_COORDS, "ribbons": (0, 0)}
        )
        self.assertFalse(lp.looks_blank())
        self.assertEqual(lp.degenerate_part_coords(), ["ribbons"])


if __name__ == "__main__":
    unittest.main()
