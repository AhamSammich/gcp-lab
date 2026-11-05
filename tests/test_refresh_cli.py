"""CLI tests for the existing-dir flow: flags, prompts, and side effects.

Each test runs ./lab-start as a subprocess (piped stdin for the
prompts, stub gcloud on PATH, HOME in a tempdir) and asserts on both
the exit code and the files left behind.
"""

import tempfile
import unittest
from pathlib import Path

from util import REPO_ROOT, build_lab_dir, run_cli, snapshot, stub_env

# every question answered by flags: a run with closed stdin and no
# prompts to answer
FULL_FLAGS = (
    "--title", "Landing Zone Challenge",
    "--tasks", "4",
    "--project", "qwiklabs-gcp-01-newlab",
    "--zone", "us-central1-a",
    "--skip-init", "--skip-auth",
)


class RefreshCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)
        self.home = self.workdir / "home"
        self.gcloud_log = self.workdir / "gcloud.log"
        self.env = stub_env(self.home, self.gcloud_log)

    def run_start(self, stdin_text, *argv):
        return run_cli(self.workdir, self.env, stdin_text, *argv)

    def assert_refresh_happened(self, result, lab):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # lab-reset ran, visibly, before the re-scaffold
        self.assertIn("LAB RESET", result.stdout)
        self.assertIn("LAB RESET COMPLETE", result.stdout)
        self.assertIn("existing - refresh", result.stdout)
        self.assertIn("keeps", result.stdout)
        # stale state gone, fresh values in
        self.assertFalse((lab / "terraform.tfstate").exists())
        tfvars = (lab / "terraform.tfvars").read_text()
        self.assertIn("qwiklabs-gcp-01-newlab", tfvars)
        self.assertNotIn("qwiklabs-gcp-00-dead", tfvars)

    def test_use_existing_flag_skips_prompt_and_refreshes(self):
        lab = build_lab_dir(self.workdir)
        before = snapshot(lab)
        result = self.run_start("", "landing-zone-challenge",
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assert_refresh_happened(result, lab)
        # the hard rule: user work byte-identical
        for name in ("task1.tf", "task2.tf", "task3.tf", "task4.tf",
                     "shared-vpc/main.tf"):
            self.assertEqual(before[name], (lab / name).read_bytes())
        # the auth revoke ran via the reset (recommendation: full reset);
        # the stub has no active account, so ADC revoke is the evidence
        self.assertIn("auth application-default revoke --quiet",
                      self.gcloud_log.read_text())

    def test_interactive_default_uses_existing(self):
        # "" at the [u]se/[n]ew prompt accepts the default; "y" proceeds
        lab = build_lab_dir(self.workdir)
        result = self.run_start("\n\ny\n", "landing-zone-challenge", *FULL_FLAGS)
        self.assert_refresh_happened(result, lab)
        # the prompt was offered (stdin fed it) and its default accepted

    def test_yes_without_use_existing_errors(self):
        lab = build_lab_dir(self.workdir)
        before = snapshot(lab)
        result = self.run_start("", "landing-zone-challenge",
                                "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--use-existing", result.stderr)
        # and nothing happened
        self.assertEqual(before, snapshot(lab))

    def test_new_path_leaves_original_untouched(self):
        lab = build_lab_dir(self.workdir)
        before = snapshot(lab)
        # "n" = new name, "" = skip url, "fresh-lab" = the new name,
        # "y" = proceed
        result = self.run_start("n\n\nfresh-lab\ny\n",
                                "landing-zone-challenge", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, snapshot(lab))  # original untouched
        fresh = self.workdir / "fresh-lab"
        self.assertTrue((fresh / "task1.tf").exists())
        self.assertTrue((fresh / "terraform.tfvars").exists())
        self.assertNotIn("LAB RESET", result.stdout)

    def test_not_a_lab_dir_refuses_use(self):
        not_a_lab = self.workdir / "random-dir"
        not_a_lab.mkdir()
        (not_a_lab / "notes.txt").write_text("not a lab\n")
        result = self.run_start("u\n\nfresh-lab\ny\n",
                                "random-dir", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("doesn't look like a lab dir", result.stdout)
        self.assertEqual((not_a_lab / "notes.txt").read_text(), "not a lab\n")
        self.assertTrue((self.workdir / "fresh-lab" / "task1.tf").exists())

    def test_fresh_dir_still_scaffolds(self):
        result = self.run_start("", "brand-new-lab", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.workdir / "brand-new-lab"
        for n in range(1, 5):
            self.assertTrue((lab / f"task{n}.tf").exists())
        self.assertIn("directory  brand-new-lab/ (new)", result.stdout)


if __name__ == "__main__":
    unittest.main()