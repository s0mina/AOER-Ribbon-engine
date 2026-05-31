"""Unit tests for the faction system. Run: python -m unittest test_factions"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from PIL import Image

from factions import (
    FactionRecolorCache,
    RecolorOptions,
    load_faction_registry,
    recolor_ribbon,
)

FULL_RECOLOR = RecolorOptions(border=True, stripe=True, base=True)
BORDER_ONLY = RecolorOptions(border=True, stripe=False, base=False)


SAMPLE_CONFIG = {
    "factions": {
        "ANRO": {
            "ribbon_groups": ["A", "B", "C"],
            "color_palette": {
                "base_color": "#1a472a",
                "stripe_color": "#c41e3a",
                "border_color": "#ffd700",
            },
        },
        "NES": {
            "ribbon_groups": ["A", "X", "Y"],
            "color_palette": {
                "base_color": "#001a4d",
                "stripe_color": "#ff6b00",
                "border_color": "#ffffff",
            },
        },
    },
    "default_faction": "ANRO",
}


class LoadRegistryTests(unittest.TestCase):
    def _write(self, payload):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(payload, tmp)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_loads_two_factions(self):
        registry = load_faction_registry(self._write(SAMPLE_CONFIG))
        self.assertEqual(set(registry.names()), {"ANRO", "NES"})
        self.assertEqual(registry.default_key, "ANRO")

    def test_palette_parsed_to_rgb(self):
        registry = load_faction_registry(self._write(SAMPLE_CONFIG))
        self.assertEqual(registry.get("NES").palette.base_color, (0, 26, 77))
        self.assertEqual(registry.get("ANRO").palette.border_color, (255, 215, 0))

    def test_get_falls_back_to_default(self):
        registry = load_faction_registry(self._write(SAMPLE_CONFIG))
        self.assertEqual(registry.get(None).key, "ANRO")
        self.assertEqual(registry.get("bogus").key, "ANRO")

    def test_invalid_hex_raises(self):
        bad = {"factions": {"X": {"color_palette": {"base_color": "not-a-color"}}}}
        with self.assertRaises(ValueError):
            load_faction_registry(self._write(bad))

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_faction_registry("/nonexistent/factions.json")

    def test_empty_factions_raises(self):
        with self.assertRaises(ValueError):
            load_faction_registry(self._write({"factions": {}}))


class RecolorTests(unittest.TestCase):
    def _ribbon(self):
        img = Image.new("RGBA", (8, 8), (50, 50, 50, 255))
        for x in range(2, 6):
            for y in range(2, 6):
                img.putpixel((x, y), (200, 200, 200, 255))
        img.putpixel((0, 0), (0, 0, 0, 0))
        return img

    def test_output_preserves_size(self):
        registry = self._registry()
        out = recolor_ribbon(self._ribbon(), registry.get("NES"), FULL_RECOLOR)
        self.assertEqual(out.size, (8, 8))

    def test_transparent_stays_transparent(self):
        registry = self._registry()
        out = recolor_ribbon(self._ribbon(), registry.get("NES"), FULL_RECOLOR)
        self.assertEqual(out.getpixel((0, 0))[3], 0)

    def test_border_uses_border_color(self):
        registry = self._registry()
        nes = registry.get("NES")
        out = recolor_ribbon(self._ribbon(), nes, FULL_RECOLOR)
        # (7, 4) is on the right border.
        self.assertEqual(out.getpixel((7, 4))[:3], nes.palette.border_color)

    def test_bright_interior_tints_toward_stripe(self):
        # Stripe/base now apply a luminance-preserving tint: target color is
        # shifted by the source pixel's brightness, not flat-replaced.
        registry = self._registry()
        nes = registry.get("NES")
        out = recolor_ribbon(self._ribbon(), nes, FULL_RECOLOR)
        r, g, b = out.getpixel((4, 4))[:3]
        # Source (200,200,200) tinted with NES stripe (255,107,0): red dominant.
        self.assertGreater(r, g)
        self.assertGreater(r, b)
        self.assertNotEqual((r, g, b), (200, 200, 200))

    def test_dark_interior_tints_toward_base(self):
        registry = self._registry()
        nes = registry.get("NES")
        out = recolor_ribbon(self._ribbon(), nes, FULL_RECOLOR)
        r, g, b = out.getpixel((1, 1))[:3]
        # Source (50,50,50) tinted with NES base (0,26,77): blue dominant.
        self.assertGreater(b, r)
        self.assertGreater(b, g)
        self.assertNotEqual((r, g, b), (50, 50, 50))

    def _registry(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(SAMPLE_CONFIG, tmp)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return load_faction_registry(tmp.name)


class CacheTests(unittest.TestCase):
    def _registry(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(SAMPLE_CONFIG, tmp)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return load_faction_registry(tmp.name)

    def test_passthrough_options_returns_source_copy(self):
        registry = self._registry()
        cache = FactionRecolorCache(registry)
        src = Image.new("RGBA", (4, 4), (10, 20, 30, 255))
        passthrough = RecolorOptions(border=False, stripe=False, base=False)
        result = cache.get("/path/a.png", "ANRO", src, passthrough)
        self.assertEqual(result.getpixel((2, 2)), (10, 20, 30, 255))

    def test_memoizes_recolored_result(self):
        registry = self._registry()
        cache = FactionRecolorCache(registry)
        src = Image.new("RGBA", (4, 4), (100, 100, 100, 255))
        first = cache.get("/path/a.png", "NES", src, FULL_RECOLOR)
        second = cache.get("/path/a.png", "NES", src, FULL_RECOLOR)
        self.assertEqual(list(first.getdata()), list(second.getdata()))

    def test_cache_key_separates_options(self):
        registry = self._registry()
        cache = FactionRecolorCache(registry)
        src = Image.new("RGBA", (4, 4), (200, 200, 200, 255))
        border_only_pixel = cache.get("/p.png", "NES", src, BORDER_ONLY).getpixel((2, 2))
        full_pixel = cache.get("/p.png", "NES", src, FULL_RECOLOR).getpixel((2, 2))
        # Interior pixel differs between policies — proves the cache key
        # distinguishes options rather than reusing a stale entry.
        self.assertNotEqual(border_only_pixel, full_pixel)


if __name__ == "__main__":
    unittest.main()
