"""TYPE-2: runtime boundaries retained while correcting static types."""

import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from util import load_cloud_lab, make_repo, stub_env


class TypeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = stub_env(self.root / "home", self.root / "gcloud.log")
        self.environment = mock.patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repo = make_repo(self.root)

    def test_type2_required_optional_and_eof_validation(self):
        start = load_cloud_lab(self.repo, "start")
        with contextlib.redirect_stdout(io.StringIO()):
            with mock.patch("builtins.input", return_value=""):
                self.assertIsNone(start.ask("Optional", optional=True))
                self.assertEqual(start.ask("Required", default="valid"), "valid")
            with mock.patch("builtins.input", side_effect=["", "bad", "valid"]):
                self.assertEqual(start.ask("Required", validate=lambda s: None if s == "valid" else "invalid"), "valid")
            with mock.patch("builtins.input", side_effect=EOFError):
                self.assertIsNone(start.ask("Optional", optional=True))
                self.assertEqual(start.ask("Default", default="valid"), "valid")
                with self.assertRaises(SystemExit):
                    start.ask("Required")
                with self.assertRaises(SystemExit):
                    start.ask("Default", default="bad", validate=lambda s: "invalid")

    def test_type2_spinner_propagates_original_exception(self):
        common = load_cloud_lab(self.repo, "common")
        error = RuntimeError("operation failed")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError) as raised:
            with common.Spinner("operation"):
                raise error
        self.assertIs(raised.exception, error)

    def test_type2_playwright_optional_and_repository_fallback(self):
        start = load_cloud_lab(self.repo, "start")
        site = self.repo / ".venv/lib/python3.10/site-packages"
        site.mkdir(parents=True)
        factory = mock.Mock()
        package = types.ModuleType("playwright")
        package.__path__ = []
        api = types.ModuleType("playwright.sync_api")
        api.sync_playwright = factory
        original_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        with mock.patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
            self.assertIsNone(start.load_playwright())
            self.assertEqual(sys.path, original_path)
        with mock.patch.dict(sys.modules, {"playwright": package, "playwright.sync_api": api}):
            self.assertIs(start.load_playwright(), factory)
        # A fake module on disk exercises the second import, not a browser.
        fake = site / "playwright"
        fake.mkdir()
        (fake / "__init__.py").write_text("")
        (fake / "sync_api.py").write_text("def sync_playwright():\n    return 'fixture factory'\n")
        import_module = start.importlib.import_module

        def import_after_repository_fallback(name):
            # TYPE-2/3: simulate an absent optional dependency even when installed.
            if str(site) not in sys.path:
                raise ModuleNotFoundError(name)
            return import_module(name)

        with mock.patch.dict(sys.modules):
            sys.modules.pop("playwright", None)
            sys.modules.pop("playwright.sync_api", None)
            with mock.patch.object(start.importlib, "import_module",
                                   side_effect=import_after_repository_fallback) as importing:
                self.assertEqual(start.load_playwright()(), "fixture factory")
            self.assertEqual(importing.call_count, 2)
            self.assertEqual(sys.path[0], str(site))
        factory.assert_not_called()

    def test_type2_playwright_missing_export_is_optional_and_cleans_failed_fallback(self):
        start = load_cloud_lab(self.repo, "start")
        package = types.ModuleType("playwright")
        package.__path__ = []
        api = types.ModuleType("playwright.sync_api")
        original_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        with mock.patch.dict(sys.modules, {"playwright": package, "playwright.sync_api": api}):
            self.assertIsNone(start.load_playwright())
            site = self.repo / ".venv/lib/python3.10/site-packages"
            site.mkdir(parents=True)
            self.assertIsNone(start.load_playwright())
            self.assertEqual(sys.path, original_path)

    def test_type2_playwright_missing_export_still_tries_repository_factory(self):
        start = load_cloud_lab(self.repo, "start")
        site = self.repo / ".venv/lib/python3.10/site-packages"
        fake = site / "playwright"
        fake.mkdir(parents=True)
        (fake / "__init__.py").write_text("")
        (fake / "sync_api.py").write_text("def sync_playwright():\n    return 'fallback factory'\n")
        original_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        import_module = start.importlib.import_module
        missing_export = types.ModuleType("playwright.sync_api")
        attempts = []

        def import_candidate(name):
            attempts.append(name)
            if len(attempts) == 1:
                return missing_export
            self.assertEqual(sys.path[0], str(site))
            return import_module(name)

        with mock.patch.dict(sys.modules):
            sys.modules.pop("playwright", None)
            sys.modules.pop("playwright.sync_api", None)
            with mock.patch.object(start.importlib, "import_module", side_effect=import_candidate):
                self.assertEqual(start.load_playwright()(), "fallback factory")
        self.assertEqual(len(attempts), 2)

    def test_type2_playwright_import_execution_errors_are_not_missing_exports(self):
        start = load_cloud_lab(self.repo, "start")
        error = AttributeError("module initialization failed")
        with mock.patch.object(start.importlib, "import_module", side_effect=error):
            with self.assertRaises(AttributeError) as raised:
                start.load_playwright()
        self.assertIs(raised.exception, error)

    def test_type2_os_xattrs_preserve_nofollow_and_fail_closed(self):
        p = load_cloud_lab(self.repo, "preservation")
        path = self.root / "link"
        with mock.patch.object(p, "_XATTR", "os"), \
                mock.patch.object(p.os, "listxattr", create=True, return_value=["user.mark"]) as listing, \
                mock.patch.object(p.os, "getxattr", create=True, return_value=b"\x00value") as reading, \
                mock.patch.object(p.os, "setxattr", create=True) as setting, \
                mock.patch.object(p.os, "removexattr", create=True) as removing:
            self.assertEqual(p._xattr_list(path, False), {"user.mark": b"\x00value".hex()})
            listing.assert_called_once_with(path, follow_symlinks=False)
            reading.assert_called_once_with(path, "user.mark", follow_symlinks=False)
            p._xattr_set(path, "user.mark", "00", False)
            setting.assert_called_once_with(path, "user.mark", b"\x00", follow_symlinks=False)
            p._xattr_remove(path, "user.mark", False)
            removing.assert_called_once_with(path, "user.mark", follow_symlinks=False)
            reading.side_effect = OSError("cannot read")
            with self.assertRaises(p.PreservationError):
                p._xattr_list(path, False)
            setting.side_effect = OSError("cannot write")
            with self.assertRaises(OSError):
                p._xattr_set(path, "user.mark", "00", False)
            removing.side_effect = OSError("cannot remove")
            with self.assertRaises(OSError):
                p._xattr_remove(path, "user.mark", False)
