"""Component tests: refresh = lab-reset + re-scaffold.

Module-import style (lab-start loaded from the isolated fixture repo
the way lab-reset loads it), temp dirs, and a stub gcloud on PATH -
the real credentials and config are never touched. The fixture lab
lives under the fixture repo's labs/ (a managed lab, as the refresh
flow now requires) and mirrors landing-zone-challenge/: populated
task files, a user module dir, stale tfstate/tfvars. Template parity
is asserted against the relocated templates/new-standard-lab.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from util import (build_lab_dir, load_lab_start, make_repo, snapshot,
                  stub_env)

NEW_PROJECT = "qwiklabs-gcp-01-newlab"
EXPECTED_TFVARS = (
    "# lab-specific values (*.tfvars is gitignored - stays local)\n"
    f'project_id     = "{NEW_PROJECT}"\n'
    'compute_region = "us-central1"\n'
    'compute_zone   = "us-central1-a"\n'
    "\n"
    'bucket_name = "bkt"\n'
)


class RefreshComponentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = make_repo(root)  # fixture checkout, never the real one
        self.lab_start = load_lab_start(self.repo)
        self.template = self.repo / "templates" / "new-standard-lab"
        self.home = root / "home"
        self.gcloud_log = root / "gcloud.log"
        # a managed lab: the only kind the refresh flow accepts
        self.lab = build_lab_dir(self.repo / "labs", "landing-zone-challenge")

    def run_reset(self):
        """The reset half of the refresh, exactly as lab-start invokes it."""
        result = subprocess.run(
            [sys.executable, str(self.repo / "lab-reset"), str(self.lab)],
            env=stub_env(self.home, self.gcloud_log),
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def refresh(self):
        """The full composition: lab-reset, then scaffold(refresh=True)."""
        self.run_reset()
        created = self.lab_start.scaffold(
            self.lab, "Landing Zone Challenge", "https://example.com/lab",
            None, 4, [{"n": 1, "title": "Set up"}],
            NEW_PROJECT, "us-central1", "us-central1-a",
            extras=[{"name": "bucket_name", "description": "lab bucket",
                     "value": "bkt"}],
            refresh=True,
        )
        return created

    # --- the one hard rule: user work survives ---------------------------
    def test_task_files_and_modules_byte_identical(self):
        before = snapshot(self.lab)
        self.refresh()
        for name in ("task1.tf", "task2.tf", "task3.tf", "task4.tf",
                     "shared-vpc/main.tf"):
            self.assertEqual(before[name], (self.lab / name).read_bytes(),
                             f"{name} must survive a refresh untouched")

    def test_no_task_stub_overwritten(self):
        # task4.tf is a bare stub - even so, refresh must not touch it
        before = (self.lab / "task4.tf").read_bytes()
        self.refresh()
        self.assertEqual(before, (self.lab / "task4.tf").read_bytes())

    def test_missing_task_stub_created(self):
        (self.lab / "task3.tf").unlink()
        created = self.refresh()
        task3 = self.lab / "task3.tf"
        self.assertTrue(task3.exists())
        self.assertEqual(
            task3.read_text(),
            self.lab_start.TASK_FILE_TEMPLATE.format(n=3, title=""),
        )
        self.assertIn(task3, created)

    # --- stale state goes, fresh values arrive ---------------------------
    def test_tfstate_deleted_and_tfvars_regenerated(self):
        self.refresh()
        self.assertFalse((self.lab / "terraform.tfstate").exists())
        self.assertFalse((self.lab / "terraform.tfstate.backup").exists())
        self.assertEqual((self.lab / "terraform.tfvars").read_text(),
                         EXPECTED_TFVARS)

    def test_readme_rewritten(self):
        self.refresh()
        readme = (self.lab / "README.md").read_text()
        self.assertIn("# Landing Zone Challenge", readme)
        self.assertNotIn("stale readme", readme)
        self.assertNotIn("__LAB_", readme)  # every placeholder filled

    def test_templates_and_variables_rewritten(self):
        self.refresh()
        # backend.tf/providers.tf are copied verbatim from the
        # relocated standard template
        for name in self.lab_start.TEMPLATE_FILES:
            if name in ("README.md", "variables.tf"):
                continue  # these get content added on top of the template
            self.assertEqual((self.lab / name).read_bytes(),
                             (self.template / name).read_bytes(),
                             f"{name} should match the template after refresh")
        expected_vars = (
            (self.template / "variables.tf")
            .read_text().rstrip("\n")
            + '\n\nvariable "bucket_name" {\n'
            + '    description = "lab bucket"\n'
            + "    type = string\n}\n"
        )
        self.assertEqual((self.lab / "variables.tf").read_text(), expected_vars)

    # --- the reset half actually reset gcloud ----------------------------
    def test_reset_revoked_credentials(self):
        self.refresh()
        log = self.gcloud_log.read_text()
        self.assertIn("config unset project", log)
        self.assertIn("auth application-default revoke --quiet", log)

    # --- idempotence: refreshing twice changes nothing more ---------------
    def test_double_refresh_idempotent(self):
        self.refresh()
        after_first = snapshot(self.lab)
        self.refresh()
        self.assertEqual(after_first, snapshot(self.lab))


class ScaffoldDestinationTests(unittest.TestCase):
    """A generated destination that is a directory is an error, not a
    folder to copy into.

    copy2's dest-directory semantics (copy src INTO dst) turn a lab
    whose backend.tf was replaced by a directory into a nested
    backend.tf/backend.tf write; the copy must address the exact
    output filename instead, so a hostile destination fails loudly
    instead of being reinterpreted.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.lab_start = load_lab_start(self.repo)
        self.lab = build_lab_dir(self.repo / "labs", "landing-zone-challenge")

    def test_scaffold_refuses_directory_destination(self):
        (self.lab / "backend.tf").unlink()
        (self.lab / "backend.tf").mkdir()
        with self.assertRaises(OSError):
            self.lab_start.scaffold(
                self.lab, "Landing Zone Challenge", None, None, 4, [],
                "qwiklabs-gcp-01-newlab", "us-central1", "us-central1-a",
                refresh=True,
            )
        # nothing was copied INTO the directory: no nested
        # backend.tf/backend.tf exists, through a symlink or not
        self.assertEqual(list((self.lab / "backend.tf").iterdir()), [])


class RefreshHelperTests(unittest.TestCase):
    """Direct unit tests for the refresh helpers, on the fixture module."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.lab_start = load_lab_start(self.repo)
        self.lab = build_lab_dir(self.repo / "labs", "landing-zone-challenge")

    def test_looks_like_lab_dir(self):
        self.assertTrue(self.lab_start.looks_like_lab_dir(self.lab))
        empty = self.root / "empty"
        empty.mkdir()
        self.assertFalse(self.lab_start.looks_like_lab_dir(empty))

    def test_refresh_keeps_lists_user_work_only(self):
        keeps = self.lab_start.refresh_keeps(self.lab)
        self.assertEqual(
            keeps, ["shared-vpc/", "task1.tf", "task2.tf",
                    "task3.tf", "task4.tf"])
        # everything the scaffold rewrites or the reset deletes is excluded

    def test_check_fresh_slug_rejects_existing(self):
        # the collision validator maps to the managed labs/ directory,
        # never the caller's directory
        self.assertIsNone(self.lab_start.check_fresh_slug("brand-new-lab"))
        problem = self.lab_start.check_fresh_slug(self.lab.name)
        self.assertIsNotNone(problem)
        self.assertIn(f"labs/{self.lab.name}", problem)

    def test_check_use_new(self):
        for good in ("u", "U", "use", "n", "new"):
            self.assertIsNone(self.lab_start.check_use_new(good))
        self.assertIsNotNone(self.lab_start.check_use_new("x"))


if __name__ == "__main__":
    unittest.main()
