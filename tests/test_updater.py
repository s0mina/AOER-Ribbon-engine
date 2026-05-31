"""Headless tests for the self-update logic (no tkinter, no network).

Everything in ``updater.py`` is pure stdlib and injectable, so these run on a
CI box with no display: version comparison, asset selection, the update-check
decision (via a canned release dict), the .bat generator's data-exclusion
guarantees, and zip-slip rejection during staging.
"""

import os
import ssl
import tempfile
import unittest
import zipfile

import updater


class SslContextTests(unittest.TestCase):
    def test_uses_certifi_bundle_when_available(self):
        # The frozen exe ships no OS trust store, so the update check needs a
        # certifi-backed context or it dies with CERTIFICATE_VERIFY_FAILED.
        ctx = updater._ssl_context()
        self.assertIsInstance(ctx, ssl.SSLContext)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)


class ParseVersionTests(unittest.TestCase):
    def test_strips_leading_v(self):
        self.assertEqual(updater.parse_version("v1.2"), (1, 2))
        self.assertEqual(updater.parse_version("V1.2.3"), (1, 2, 3))

    def test_plain_numeric(self):
        self.assertEqual(updater.parse_version("1.1.1"), (1, 1, 1))

    def test_suffix_is_truncated(self):
        self.assertEqual(updater.parse_version("1.2-beta"), (1, 2))

    def test_garbage_is_empty(self):
        self.assertEqual(updater.parse_version(""), ())
        self.assertEqual(updater.parse_version(None), ())
        self.assertEqual(updater.parse_version("beta"), ())


class IsNewerTests(unittest.TestCase):
    def test_higher_is_newer(self):
        self.assertTrue(updater.is_newer("v1.3", "1.2"))
        self.assertTrue(updater.is_newer("2.0", "1.9"))

    def test_equal_is_not_newer(self):
        self.assertFalse(updater.is_newer("1.2", "1.2"))
        self.assertFalse(updater.is_newer("v1.2", "1.2"))

    def test_lower_is_not_newer(self):
        self.assertFalse(updater.is_newer("1.1", "1.2"))

    def test_differing_widths_zero_pad(self):
        # 1.2 == 1.2.0, so 1.2.1 is newer but 1.2 is not.
        self.assertTrue(updater.is_newer("1.2.1", "1.2"))
        self.assertFalse(updater.is_newer("1.2", "1.2.0"))


class AssetUrlTests(unittest.TestCase):
    def test_picks_matching_asset(self):
        release = {
            "assets": [
                {"name": "other.txt", "browser_download_url": "http://x/other"},
                {"name": updater.ASSET_NAME, "browser_download_url": "http://x/win"},
            ]
        }
        self.assertEqual(updater.asset_download_url(release), "http://x/win")

    def test_picks_versioned_asset(self):
        # Releases now bake the version into the filename; the prefix/suffix
        # match must still find it.
        release = {
            "assets": [
                {"name": "notes.txt", "browser_download_url": "http://x/notes"},
                {
                    "name": "AOER-Ribbon-engine-windowsv1.4.zip",
                    "browser_download_url": "http://x/v14",
                },
            ]
        }
        self.assertEqual(updater.asset_download_url(release), "http://x/v14")

    def test_missing_asset_returns_empty(self):
        self.assertEqual(updater.asset_download_url({"assets": []}), "")
        self.assertEqual(updater.asset_download_url({}), "")
        # A zip that isn't our Windows asset must not be matched.
        other = {"assets": [{"name": "something-else.zip", "browser_download_url": "u"}]}
        self.assertEqual(updater.asset_download_url(other), "")


class CheckForUpdateTests(unittest.TestCase):
    def _release(self, tag):
        return {
            "tag_name": tag,
            "body": "notes here",
            "html_url": "http://example/releases/latest",
            "assets": [
                {"name": updater.ASSET_NAME, "browser_download_url": "http://x/win"}
            ],
        }

    def test_available_when_newer(self):
        info = updater.check_for_update("1.2", fetch=lambda repo: self._release("v1.3"))
        self.assertTrue(info.available)
        self.assertEqual(info.latest_version, "1.3")
        self.assertEqual(info.tag, "v1.3")
        self.assertEqual(info.asset_url, "http://x/win")
        self.assertEqual(info.notes, "notes here")

    def test_not_available_when_same(self):
        info = updater.check_for_update("1.2", fetch=lambda repo: self._release("v1.2"))
        self.assertFalse(info.available)

    def test_not_available_when_no_tag(self):
        info = updater.check_for_update("1.2", fetch=lambda repo: {"assets": []})
        self.assertFalse(info.available)


class BuildScriptTests(unittest.TestCase):
    def test_excludes_every_data_dir_and_file(self):
        script = updater.build_update_script(
            staged_dir=r"C:\staged",
            install_dir=r"C:\app",
            relaunch_exe=r"C:\app\AOER-Ribbon-engine.exe",
            pid=4242,
        )
        # Each user-data dir/file must appear as a full staged path in the
        # robocopy exclusion list, or an update would clobber user content.
        for d in updater.DATA_DIRS:
            self.assertIn(os.path.join(r"C:\staged", d), script)
        for f in updater.DATA_FILES:
            self.assertIn(os.path.join(r"C:\staged", f), script)
        self.assertIn("/XD", script)
        self.assertIn("/XF", script)

    def test_waits_on_pid_and_relaunches(self):
        script = updater.build_update_script(
            r"C:\staged", r"C:\app", r"C:\app\app.exe", pid=99
        )
        self.assertIn("99", script)
        self.assertIn("tasklist", script)
        self.assertIn(r"C:\app\app.exe", script)
        # Self-deletes at the end so no stray .bat is left behind.
        self.assertIn('del "%~f0"', script)

    def test_uses_crlf_line_endings(self):
        script = updater.build_update_script(r"C:\s", r"C:\a", r"C:\a\x.exe", pid=1)
        self.assertIn("\r\n", script)

    def test_logs_to_install_dir(self):
        # A log file in the install dir makes a "does nothing" report diagnosable.
        script = updater.build_update_script(r"C:\s", r"C:\app", r"C:\app\x.exe", pid=1)
        self.assertIn(os.path.join(r"C:\app", "update_log.txt"), script)

    def test_aborts_relaunch_on_robocopy_failure(self):
        # robocopy 8+ is a real failure: don't relaunch a half-copied install.
        script = updater.build_update_script(r"C:\s", r"C:\app", r"C:\app\x.exe", pid=1)
        self.assertIn("GEQ 8", script)
        self.assertIn("ERRORLEVEL", script)

    def test_shows_user_facing_message(self):
        # The console is visible and tells the user to wait, not a blank desktop.
        script = updater.build_update_script(r"C:\s", r"C:\app", r"C:\app\x.exe", pid=1)
        self.assertIn("Updating AOER Ribbon Engine", script)

    def test_does_not_hide_the_console(self):
        # Regression: the helper must not be spawned with CREATE_NO_WINDOW, which
        # made the update look like it "did nothing" (CW-279).
        self.assertFalse(hasattr(updater, "_CREATE_NO_WINDOW"))
        self.assertTrue(hasattr(updater, "_CREATE_NEW_CONSOLE"))


class ApplyUpdateTests(unittest.TestCase):
    def test_writes_bat_without_spawning(self):
        with tempfile.TemporaryDirectory() as d:
            bat = updater.apply_update_windows(
                staged_dir=os.path.join(d, "staged"),
                install_dir=os.path.join(d, "app"),
                relaunch_exe=os.path.join(d, "app", "x.exe"),
                pid=123,
                spawn=False,
            )
            self.assertTrue(os.path.exists(bat))
            self.assertTrue(bat.endswith(".bat"))
            with open(bat, encoding="utf-8") as fh:
                self.assertIn("robocopy", fh.read())


class StageUpdateTests(unittest.TestCase):
    def test_extracts_clean_archive(self):
        with tempfile.TemporaryDirectory() as d:
            zip_path = os.path.join(d, "ok.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("AOER-Ribbon-engine.exe", b"binary")
                zf.writestr("_internal/base_library.zip", b"x")
            staging = os.path.join(d, "staging")
            os.makedirs(staging)
            updater.stage_update(zip_path, staging)
            self.assertTrue(
                os.path.exists(os.path.join(staging, "AOER-Ribbon-engine.exe"))
            )

    def test_rejects_zip_slip(self):
        with tempfile.TemporaryDirectory() as d:
            zip_path = os.path.join(d, "evil.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../escape.txt", b"pwned")
            staging = os.path.join(d, "staging")
            os.makedirs(staging)
            with self.assertRaises(RuntimeError):
                updater.stage_update(zip_path, staging)


if __name__ == "__main__":
    unittest.main()
