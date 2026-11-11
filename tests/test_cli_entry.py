"""CLI-1/CLI-11: ./labkit is the sole repository-local entry point.

These tests pin the entry contract only:

  * help - top level and per command - exits 0, and neither loads
    Playwright nor runs any external command
  * invalid arguments exit 2 BEFORE the banner, the debug log, or
    any gcloud call
  * ./labkit runs on its own shebang, with no explicit interpreter

The workflows themselves - start options and generated files, the
refresh reset/re-scaffold/preservation, and the ordinary and
credentials-only resets - are covered by the command suites that
already launch labkit: test_layout_cli.py, test_refresh_cli.py,
and test_reset_cli.py.

Every run targets the isolated fixture repo (tests/util.make_repo)
with stubbed PATH/HOME, so no real credential or lab is ever touched.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from util import (build_lab_dir, make_repo, run_cloud_lab, snapshot,
                  stub_env)


class CloudLabEntryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)
        self.repo = make_repo(self.workdir)
        self.scratch = self.workdir / "elsewhere"  # cwd outside the repo
        self.scratch.mkdir()
        self.home = self.workdir / "home"
        self.gcloud_log = self.workdir / "gcloud.log"
        self.env = stub_env(self.home, self.gcloud_log)
        self.cache = self.home / ".cache" / "lab-start"

    def run_cli(self, cwd, stdin_text, *argv):
        return run_cloud_lab(self.repo, cwd, self.env, stdin_text, *argv)

    def assert_nothing_ran(self):
        """A help or a refused run leaves no trace: no log dir, no gcloud."""
        self.assertFalse(self.cache.exists(),
                         "the run wrote the cache/log directory")
        self.assertFalse(self.gcloud_log.exists()
                         and self.gcloud_log.read_text().strip(),
                         "gcloud was invoked")


class HelpContractTests(CloudLabEntryTestCase):
    # --- CLI-1: help exits 0 and lists the commands -------------------
    def test_top_level_help_lists_the_commands(self):
        result = self.run_cli(self.scratch, "", "--help")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("usage:", result.stdout)
        for command in ("start", "reset", "archive"):
            self.assertIn(command, result.stdout)

    def test_command_local_help(self):
        # each command documents its own usage, with the options the
        # workflows already had (CLI-2: existing options preserved)
        results = {}
        for command in ("start", "reset", "archive"):
            with self.subTest(command=command):
                result = self.run_cli(self.scratch, "", command, "--help")
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                self.assertIn(f"usage: labkit {command}", result.stdout)
                results[command] = result.stdout
        self.assertIn("--use-existing", results["start"])
        self.assertIn("--credentials-only", results["reset"])

    def test_archive_help_documents_directory_and_readonly_preview(self):
        # CLI-1: archive has real command-local help, without side effects.
        result = self.run_cli(self.scratch, "", "archive", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("directory", result.stdout)
        self.assertIn("--dry-run", result.stdout)
        self.assert_nothing_ran()

    # --- CLI-1: help loads no Playwright, runs no external command ----
    def test_help_loads_no_playwright_and_runs_nothing(self):
        # a poisoned playwright module on PYTHONPATH turns any import
        # into a loud crash, so three exit-0 helps prove the import
        # never happened; the untouched cache dir and empty gcloud log
        # prove no external command ran either
        poison = self.workdir / "poison"
        poison.mkdir()
        (poison / "playwright.py").write_text(
            'raise RuntimeError("playwright must not load for help")\n')
        env = dict(self.env, PYTHONPATH=str(poison))
        for argv in (("--help",), ("start", "--help"), ("reset", "--help"),
                     ("archive", "--help")):
            with self.subTest(argv=argv):
                result = run_cloud_lab(self.repo, self.scratch, env, "", *argv)
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assert_nothing_ran()

    # --- CLI-1: invalid arguments exit 2 before operations/logging ----
    def test_invalid_arguments_exit_2_before_operations_or_logging(self):
        lab = build_lab_dir(self.repo / "labs", "my-lab")
        before = snapshot(lab)
        for argv in ((),                      # no command
                     ("bogus",),              # unknown command
                     ("reset", "--no-such-flag"),
                     # CLI-3: a positional directory with
                     # --credentials-only is the same pre-operation
                     # refusal: no cache/log directory at all
                     ("reset", "--credentials-only", "my-lab"),
                     ("archive", "--no-such-flag"),
                     ("archive", "my-lab", "other-lab"),
                     ("start", "--tasks", "many")):
            with self.subTest(argv=argv):
                result = self.run_cli(self.scratch, "", *argv)
                self.assertEqual(result.returncode, 2,
                                 result.stdout + result.stderr)
                self.assertIn("usage:", result.stderr)
                # no banner on stdout: the refusal precedes everything
                self.assertEqual(result.stdout, "")
        self.assert_nothing_ran()
        self.assertEqual(before, snapshot(lab),
                         "a rejected invocation changed the lab")


class DirectExecutionTests(CloudLabEntryTestCase):
    # --- CLI-1: a repository-local EXECUTABLE ---------------------------
    def test_shebang_execution_resets_a_lab(self):
        # CLI-11/1: ./labkit runs on its own shebang: the /usr/bin/env lookup
        # finds the python3 on PATH (here: the stub symlink), and a
        # full reset runs end to end without any explicit interpreter
        lab = build_lab_dir(self.repo / "labs", "my-lab")
        result = subprocess.run(
            [str(self.repo / "labkit"), "reset", "my-lab"],
            cwd=self.scratch, env=self.env, input="",
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((lab / "terraform.tfstate").exists())
        self.assertTrue((lab / "task1.tf").is_file())


if __name__ == "__main__":
    unittest.main()
