"""CLI tests for the existing-dir flow: flags, prompts, and side effects.

Retargeted to the 002 managed layout: the existing lab lives under
the fixture repo's labs/, and a run from outside the repository still
finds it. CLI-11/2: each test runs labkit start (piped stdin for
the prompts, stub gcloud on PATH, HOME in a tempdir) and asserts on
both the exit code and the files left behind.
"""

import tempfile
import unittest
from pathlib import Path

from util import FULL_FLAGS, build_lab_dir, make_repo, run_cli, snapshot, stub_env

SLUG = "landing-zone-challenge"


class RefreshCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)
        self.repo = make_repo(self.workdir)  # never the real checkout
        self.home = self.workdir / "home"
        self.gcloud_log = self.workdir / "gcloud.log"
        self.env = stub_env(self.home, self.gcloud_log)

    def run_start(self, stdin_text, *argv):
        # invoked from OUTSIDE the repository: the managed mapping
        # must not depend on the caller's directory
        return run_cli(self.repo, self.workdir, self.env, stdin_text, *argv)

    def assert_refresh_happened(self, result, lab):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The reset workflow ran, visibly, before the re-scaffold.
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
        lab = build_lab_dir(self.repo / "labs", SLUG)
        before = snapshot(lab)
        result = self.run_start("", SLUG,
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
        # CLI-2: the refresh targeted labs/<slug>, never the caller's
        # directory (consolidated from the removed duplicate smokes)
        self.assertFalse((self.workdir / SLUG).exists())

    def test_interactive_default_uses_existing(self):
        # "" at the [u]se/[n]ew prompt accepts the default; "y" proceeds
        lab = build_lab_dir(self.repo / "labs", SLUG)
        result = self.run_start("\n\ny\n", SLUG, *FULL_FLAGS)
        self.assert_refresh_happened(result, lab)
        # the prompt was offered (stdin fed it) and its default accepted

    def test_yes_without_use_existing_errors(self):
        lab = build_lab_dir(self.repo / "labs", SLUG)
        before = snapshot(lab)
        result = self.run_start("", SLUG,
                                "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--use-existing", result.stderr)
        # and nothing happened anywhere - not even a caller's-directory
        # twin (consolidated from the removed duplicate smoke)
        self.assertEqual(before, snapshot(lab))
        self.assertFalse((self.workdir / SLUG).exists())

    def test_new_path_leaves_original_untouched(self):
        lab = build_lab_dir(self.repo / "labs", SLUG)
        before = snapshot(lab)
        # "n" = new name, "" = skip url, "fresh-lab" = the new name,
        # "y" = proceed
        result = self.run_start("n\n\nfresh-lab\ny\n",
                                SLUG, *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, snapshot(lab))  # original untouched
        fresh = self.repo / "labs" / "fresh-lab"
        self.assertTrue((fresh / "task1.tf").exists())
        self.assertTrue((fresh / "terraform.tfvars").exists())
        # the new lab landed under labs/, never in the caller's directory
        self.assertFalse((self.workdir / "fresh-lab").exists())
        self.assertNotIn("LAB RESET", result.stdout)

    def test_untyped_dir_refused_for_use(self):
        # AC12 (the retained original arbitrary-directory safeguard,
        # under the legacy-fallback policy): missing metadata alone is
        # not evidence that a directory is a lab - a notes-only
        # directory with no .tf files is hard-refused, the refusal
        # explains itself, nothing is reset or rewritten, no fresh
        # scaffold appears, and no credential call runs
        not_a_lab = self.repo / "labs" / "random-dir"
        not_a_lab.mkdir()
        (not_a_lab / "notes.txt").write_text("not a lab\n")
        result = self.run_start("u\n", "random-dir", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertIn("no .tf files", result.stderr)
        self.assertEqual((not_a_lab / "notes.txt").read_text(), "not a lab\n")
        self.assertFalse((self.repo / "labs" / "fresh-lab").exists(),
                         "the refusal fell through to a fresh scaffold")
        self.assertFalse(self.gcloud_log.exists()
                         and self.gcloud_log.read_text().strip(),
                         "gcloud was invoked for a refused refresh")

    def test_dangling_task_symlink_referent_untouched(self):
        # a user's dangling task1.tf symlink is an existing entry:
        # refresh keeps the link as-is and never creates its referent
        # (exists() is False for a dangling link, so a naive
        # write_text would follow it out of the lab)
        lab = build_lab_dir(self.repo / "labs", SLUG)
        referent = self.workdir / "task1-referent.tf"
        (lab / "task1.tf").unlink()
        (lab / "task1.tf").symlink_to(referent)
        before = snapshot(lab)
        result = self.run_start("", SLUG,
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # the link survives, still dangling, referent still missing
        self.assertTrue((lab / "task1.tf").is_symlink())
        self.assertFalse((lab / "task1.tf").exists())
        self.assertFalse(referent.exists(),
                         "refresh wrote through a dangling task symlink")
        # the rest of the user work is byte-identical
        for name in ("task2.tf", "task3.tf", "task4.tf",
                     "shared-vpc/main.tf"):
            self.assertEqual(before[name], (lab / name).read_bytes())

    def test_fresh_dir_still_scaffolds(self):
        result = self.run_start("", "brand-new-lab", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "brand-new-lab"
        for n in range(1, 5):
            self.assertTrue((lab / f"task{n}.tf").exists())
        # the preview names the resolved absolute managed target
        # (spec: previews show the resolved target, even for callers
        # outside the repository)
        self.assertIn(f"directory  {lab}/ (new)", result.stdout)
        # no root-level twin: the old flat layout is not a fallback
        self.assertFalse((self.repo / "brand-new-lab").exists())

    def test_refresh_stops_when_local_reset_fails(self):
        # CLI-2: a refresh whose local cleanup fails stops before
        # anything is rewritten - no re-scaffold, no gcloud step, and
        # the stale values stay exactly as they were. (The lab dir is
        # made read-only so the first tfstate unlink fails.)
        lab = build_lab_dir(self.repo / "labs", SLUG)
        before = snapshot(lab)
        lab.chmod(0o500)  # unlink needs write permission on the lab dir
        self.addCleanup(lab.chmod, 0o755)
        result = self.run_start("", SLUG,
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing was rewritten", result.stderr)
        # nothing was rewritten: stale tfvars, stale README, user work
        self.assertEqual(before, snapshot(lab))
        log = self.gcloud_log
        self.assertFalse(log.exists() and log.read_text().strip(),
                         "gcloud ran although the reset had stopped")


if __name__ == "__main__":
    unittest.main()
