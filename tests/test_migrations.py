"""Regression tests for the startup filesystem migrations (migrations.py).

These functions MOVE USER FILES, so the invariants below are load-bearing:
new layout is canonical, old layout still works, nothing is overwritten or
deleted, and a read-only install degrades gracefully instead of crashing.
"""

import os
import tempfile
import unittest

import migrations


def _touch(path: str, data: bytes = b"x") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


class IsGorgetNameTests(unittest.TestCase):
    def test_matches_substring_case_insensitively(self):
        self.assertTrue(migrations.is_gorget_name("Gold Gorget.png"))
        self.assertTrue(migrations.is_gorget_name("FANCY_GORGET.PNG"))
        self.assertTrue(migrations.is_gorget_name("officers gorget"))

    def test_rejects_non_gorgets(self):
        self.assertFalse(migrations.is_gorget_name("Valor.png"))
        self.assertFalse(migrations.is_gorget_name(""))
        self.assertFalse(migrations.is_gorget_name("MR Service.png"))


class RelocateFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_moves_when_src_exists_and_dst_free(self):
        src = os.path.join(self.root, "a", "f.png")
        dst = os.path.join(self.root, "b", "f.png")
        _touch(src, b"hello")
        moved = migrations.relocate_file(src, dst, log=lambda m: None)
        self.assertTrue(moved)
        self.assertFalse(os.path.exists(src))
        with open(dst, "rb") as fh:
            self.assertEqual(fh.read(), b"hello")

    def test_never_overwrites_existing_dst(self):
        src = os.path.join(self.root, "a", "f.png")
        dst = os.path.join(self.root, "b", "f.png")
        _touch(src, b"new")
        _touch(dst, b"original")
        moved = migrations.relocate_file(src, dst, log=lambda m: None)
        self.assertFalse(moved)
        # Both files survive; the destination is untouched (non-destructive).
        self.assertTrue(os.path.exists(src))
        with open(dst, "rb") as fh:
            self.assertEqual(fh.read(), b"original")

    def test_missing_src_is_noop(self):
        dst = os.path.join(self.root, "b", "f.png")
        self.assertFalse(migrations.relocate_file(os.path.join(self.root, "nope.png"), dst, log=lambda m: None))
        self.assertFalse(os.path.exists(dst))

    def test_move_failure_is_swallowed_and_logged(self):
        src = os.path.join(self.root, "a", "f.png")
        dst = os.path.join(self.root, "b", "f.png")
        _touch(src)
        logs = []
        original = migrations.shutil.move

        def boom(*a, **k):
            raise OSError("read-only filesystem")

        migrations.shutil.move = boom
        try:
            moved = migrations.relocate_file(src, dst, label="X/f.png", log=logs.append)
        finally:
            migrations.shutil.move = original
        self.assertFalse(moved)
        self.assertTrue(os.path.exists(src))  # left in place, not lost
        self.assertTrue(any("X/f.png" in m for m in logs))


class ResolveCharactersDirTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = self._tmp.name
        self.assets = os.path.join(self.base, "assets")
        self.addCleanup(self._tmp.cleanup)

    def test_migrates_legacy_root_into_assets(self):
        _touch(os.path.join(self.base, "Characters", "A.png"), b"tile")
        resolved = migrations.resolve_characters_dir(self.base, self.assets, log=lambda m: None)
        self.assertEqual(resolved, os.path.join(self.assets, "Characters"))
        self.assertTrue(os.path.isfile(os.path.join(resolved, "A.png")))
        self.assertFalse(os.path.isdir(os.path.join(self.base, "Characters")))

    def test_prefers_new_location_when_present(self):
        _touch(os.path.join(self.assets, "Characters", "A.png"))
        _touch(os.path.join(self.base, "Characters", "STALE.png"))
        resolved = migrations.resolve_characters_dir(self.base, self.assets, log=lambda m: None)
        self.assertEqual(resolved, os.path.join(self.assets, "Characters"))
        # Existing new dir wins; legacy is left untouched (non-destructive).
        self.assertTrue(os.path.isdir(os.path.join(self.base, "Characters")))

    def test_falls_back_to_legacy_when_move_fails(self):
        _touch(os.path.join(self.base, "Characters", "A.png"), b"tile")
        logs = []
        original = migrations.shutil.move
        migrations.shutil.move = lambda *a, **k: (_ for _ in ()).throw(OSError("locked"))
        try:
            resolved = migrations.resolve_characters_dir(self.base, self.assets, log=logs.append)
        finally:
            migrations.shutil.move = original
        # App still finds its tiles at the legacy location this session.
        self.assertEqual(resolved, os.path.join(self.base, "Characters"))
        self.assertTrue(os.path.isfile(os.path.join(resolved, "A.png")))
        self.assertTrue(logs)

    def test_neither_exists_returns_canonical_new_path(self):
        resolved = migrations.resolve_characters_dir(self.base, self.assets, log=lambda m: None)
        self.assertEqual(resolved, os.path.join(self.assets, "Characters"))


class FactionLayoutMigrationTests(unittest.TestCase):
    SUBDIRS = ("ribbons", "awards", "commendations", "gorgets", "shirttemplates")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.assets = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _migrate(self):
        migrations.migrate_faction_asset_layout(self.assets, self.SUBDIRS, log=lambda m: None)

    def test_creates_missing_subdirs_for_each_faction(self):
        os.makedirs(os.path.join(self.assets, "MDC"))
        self._migrate()
        for sub in self.SUBDIRS:
            self.assertTrue(os.path.isdir(os.path.join(self.assets, "MDC", sub)), sub)

    def test_relocates_gorgets_and_keeps_other_commendations(self):
        comm = os.path.join(self.assets, "MDC", "commendations")
        _touch(os.path.join(comm, "Gold Gorget.png"))
        _touch(os.path.join(comm, "Valor.png"))
        self._migrate()
        self.assertTrue(os.path.isfile(os.path.join(self.assets, "MDC", "gorgets", "Gold Gorget.png")))
        self.assertFalse(os.path.isfile(os.path.join(comm, "Gold Gorget.png")))
        # Non-gorget commendation stays put.
        self.assertTrue(os.path.isfile(os.path.join(comm, "Valor.png")))

    def test_moves_legacy_top_level_shirttemplate(self):
        os.makedirs(os.path.join(self.assets, "MDC"))
        _touch(os.path.join(self.assets, "MDC", "shirttemplate.png"))
        self._migrate()
        self.assertTrue(
            os.path.isfile(os.path.join(self.assets, "MDC", "shirttemplates", "shirttemplate.png"))
        )
        self.assertFalse(os.path.isfile(os.path.join(self.assets, "MDC", "shirttemplate.png")))

    def test_skips_characters_pool(self):
        _touch(os.path.join(self.assets, "Characters", "A.png"))
        self._migrate()
        # Characters/ is not a faction tree: no gorgets/ should be created inside it.
        self.assertFalse(os.path.isdir(os.path.join(self.assets, "Characters", "gorgets")))

    def test_is_idempotent(self):
        comm = os.path.join(self.assets, "MDC", "commendations")
        _touch(os.path.join(comm, "Gold Gorget.png"))
        self._migrate()
        self._migrate()  # second run must not error or duplicate
        gorgets = os.listdir(os.path.join(self.assets, "MDC", "gorgets"))
        self.assertEqual(gorgets, ["Gold Gorget.png"])

    def test_missing_assets_root_is_noop(self):
        # Must not raise when assets/ doesn't exist yet.
        migrations.migrate_faction_asset_layout(
            os.path.join(self.assets, "does-not-exist"), self.SUBDIRS, log=lambda m: None
        )


class CleanupLegacyRootDocsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = self._tmp.name
        self.docs = os.path.join(self.base, "docs")
        self.addCleanup(self._tmp.cleanup)

    def test_removes_root_doc_when_duplicated_in_docs(self):
        _touch(os.path.join(self.base, "FACTIONS.md"))
        _touch(os.path.join(self.docs, "FACTIONS.md"))
        removed = migrations.cleanup_legacy_root_docs(self.base, self.docs, log=lambda m: None)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.isfile(os.path.join(self.base, "FACTIONS.md")))
        # The canonical copy in docs/ is untouched.
        self.assertTrue(os.path.isfile(os.path.join(self.docs, "FACTIONS.md")))

    def test_keeps_root_doc_with_no_canonical_twin(self):
        # No docs/ copy => never delete, so we can't lose the only copy.
        _touch(os.path.join(self.base, "CLI.md"))
        removed = migrations.cleanup_legacy_root_docs(self.base, self.docs, log=lambda m: None)
        self.assertEqual(removed, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.base, "CLI.md")))

    def test_never_removes_readme_even_if_requested(self):
        _touch(os.path.join(self.base, "README.md"))
        _touch(os.path.join(self.docs, "README.md"))
        removed = migrations.cleanup_legacy_root_docs(
            self.base, self.docs, names=("README.md", "FACTIONS.md"), log=lambda m: None
        )
        self.assertEqual(removed, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.base, "README.md")))

    def test_is_idempotent(self):
        _touch(os.path.join(self.base, "INSTALLATION.md"))
        _touch(os.path.join(self.docs, "INSTALLATION.md"))
        self.assertEqual(migrations.cleanup_legacy_root_docs(self.base, self.docs, log=lambda m: None), 1)
        self.assertEqual(migrations.cleanup_legacy_root_docs(self.base, self.docs, log=lambda m: None), 0)


class RelocateGorgetsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fac = os.path.join(self._tmp.name, "MDC")
        self.addCleanup(self._tmp.cleanup)

    def test_returns_count_of_moved_files(self):
        comm = os.path.join(self.fac, "commendations")
        _touch(os.path.join(comm, "A Gorget.png"))
        _touch(os.path.join(comm, "B gorget.png"))
        _touch(os.path.join(comm, "Plain.png"))
        moved = migrations.relocate_gorgets(self.fac, log=lambda m: None)
        self.assertEqual(moved, 2)

    def test_no_commendations_dir_returns_zero(self):
        os.makedirs(self.fac)
        self.assertEqual(migrations.relocate_gorgets(self.fac, log=lambda m: None), 0)


if __name__ == "__main__":
    unittest.main()
