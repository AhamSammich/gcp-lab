"""CACHE-1/2: runtime cache isolation without migrating browser credentials."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from util import FULL_FLAGS, make_repo, run_cli, run_reset, snapshot_nofollow, stub_env


class CacheLocationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.repo = make_repo(self.work)
        self.home = self.work / "home"
        self.env = stub_env(self.home, self.work / "gcloud.log")
        self.old = self.home / ".cache" / "lab-start"
        (self.old / "profile").mkdir(parents=True)
        (self.old / "profile" / "Cookies").write_bytes(b"old session")
        (self.old / ".authenticated").touch()
        (self.old / "lab-start.log").write_bytes(b"old log")
        self.before = snapshot_nofollow(self.old)
        self.cache = self.home / ".cache" / "labkit"

    def test_cache1_cache2_start_reset_logs_use_new_root_without_migration(self):
        result = run_cli(self.repo, self.work, self.env, "", "cache-lab", *FULL_FLAGS, "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = run_reset(self.repo, self.work, self.env, "cache-lab", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ("lab-start.log", "lab-reset.log"):
            self.assertTrue((self.cache / name).is_file(), name)
        self.assertFalse((self.cache / "profile").exists())
        self.assertFalse((self.cache / ".authenticated").exists())
        self.assertEqual(snapshot_nofollow(self.old), self.before)

    def test_cache1_cache2_browser_artifacts_follow_home_changed_after_import(self):
        # Fresh process avoids unrelated logger handlers; browser/network are stubs.
        script = '''
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from labkit_cli import start

os.environ["HOME"] = os.environ["RUN_HOME"]
root = Path.home() / ".cache" / "labkit"
page = MagicMock()
page.url = "https://example.invalid/lab"
page.content.return_value = "<html>fixture</html>"
page.evaluate.return_value = dict(title="Fixture", sections=[], tasks=[],
                                objectives=[], running=False, variables={})
page.screenshot.side_effect = lambda **kw: Path(kw["path"]).write_bytes(b"png")
context = MagicMock()
context.pages = [page]
def launch(profile, *, headless):
    assert Path(profile) == root / "profile", profile
    assert headless is False, "must not reuse the old auth marker"
    Path(profile).mkdir(parents=True)
    (Path(profile) / "Cookies").write_bytes(b"new session")
    return context
browser = MagicMock()
browser.return_value.__enter__.return_value.chromium.launch_persistent_context.side_effect = launch
with patch.object(start, "load_playwright", return_value=browser), \\
     patch.object(start, "_settle"):
    assert start.fetch_lab_page_browser(page.url)["title"] == "Fixture"
'''
        import_home = self.work / "import-home"
        import_home.mkdir()
        env = dict(self.env, HOME=str(import_home), RUN_HOME=str(self.home))
        result = subprocess.run([sys.executable, "-B", "-c", script],
                                cwd=self.repo, env=env, input="", text=True,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((import_home / ".cache").exists())
        self.assertEqual((self.cache / "profile" / "Cookies").read_bytes(), b"new session")
        self.assertTrue((self.cache / ".authenticated").is_file())
        self.assertTrue((self.cache / "lab-start.log").is_file())
        self.assertEqual((self.cache / "last-lab-page.html").read_text(), "<html>fixture</html>")
        self.assertEqual((self.cache / "last-lab-page.png").read_bytes(), b"png")
        self.assertEqual(snapshot_nofollow(self.old), self.before)
