"""Component tests: refresh = reset + re-scaffold.

Module-import style (the labkit_cli implementation loaded from the
isolated fixture repo, the way the launchers import it), temp dirs,
and a stub gcloud on PATH - the real credentials and config are never
touched. The fixture lab lives under the fixture repo's labs/ (a
managed lab, as the refresh flow now requires) and mirrors
landing-zone-challenge/: populated task files, a user module dir,
stale tfstate/tfvars. Template parity is asserted against the
relocated templates/new-terraform-lab - the type this fixture
records, per plan 005's type-aware identity.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from util import (build_lab_dir, load_lab_reset, load_lab_start, make_repo,
                  snapshot, stub_env)

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
        self.template = self.repo / "templates" / "new-terraform-lab"
        self.home = root / "home"
        self.gcloud_log = root / "gcloud.log"
        # a managed lab: the only kind the refresh flow accepts
        self.lab = build_lab_dir(self.repo / "labs", "landing-zone-challenge")

    def run_reset(self):
        """The reset half of the refresh, exactly as the start flow invokes it."""
        reset = load_lab_reset(self.repo)
        # clear=True: os.environ becomes EXACTLY the env stub_env builds
        # (the one a subprocess run gets via env=stub_env) - a merging
        # patch would carry this shell's ambient CLOUDSDK_*/GOOGLE_*/TF_*
        # overrides into the tools the reset spawns
        with mock.patch.dict(os.environ, stub_env(self.home, self.gcloud_log),
                             clear=True):
            self.assertEqual(reset.reset_lab(str(self.lab)), 0)

    def refresh(self):
        """The full composition: the reset half, then scaffold(refresh=True)."""
        self.run_reset()
        created = self.lab_start.scaffold(
            self.lab, "terraform", "Landing Zone Challenge",
            "https://example.com/lab",
            None, 4, [{"n": 1, "title": "Set up"}],
            NEW_PROJECT, "us-central1", "us-central1-a",
            extras=[{"name": "bucket_name", "description": "lab bucket",
                     "value": "bkt"}],
            refresh=True,
        )
        return created

    # --- the one hard rule: user work survives ---------------------------
    def test_task_files_and_modules_byte_identical(self):
        # task4.tf is a bare stub in the fixture: even a stub file is
        # user work and must survive byte-identical
        before = snapshot(self.lab)
        self.refresh()
        for name in ("task1.tf", "task2.tf", "task3.tf", "task4.tf",
                     "shared-vpc/main.tf"):
            self.assertEqual(before[name], (self.lab / name).read_bytes(),
                             f"{name} must survive a refresh untouched")

    def test_missing_task_stub_created(self):
        (self.lab / "task3.tf").unlink()
        created = self.refresh()
        task3 = self.lab / "task3.tf"
        self.assertTrue(task3.exists())
        # the stub is the shipped exemplar with only its number and
        # title markers filled (the title slot is read back out of
        # the stub's own heading, so this pins structure, not prose)
        exemplar = (self.template / "task1.tf").read_text()
        heading = task3.read_text().splitlines()[1]
        self.assertTrue(heading.startswith("# Task 3"), heading)
        title = heading.split(": ", 1)[1]
        expected = (exemplar.replace("__TASK_N__", "3")
                    .replace("__TASK_TITLE__", title))
        self.assertEqual(task3.read_text(), expected)
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
        # terraform template this lab's type generates from
        for name in ("backend.tf", "providers.tf"):
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

    # --- CLI-10: the in-process reset half holds the strict stub boundary --
    def test_run_reset_leaks_no_ambient_cloud_overrides(self):
        # stub_env strips CLOUDSDK_*/GOOGLE_*/TF_* for subprocess runs
        # (env=stub_env); an IN-PROCESS reset must hold the same
        # boundary - a merging environment patch would carry this
        # shell's ambient overrides into the very tools the reset
        # spawns. Synthetic stand-ins are planted around the actual
        # run_reset(); the stub gcloud intercepts every subprocess it
        # spawns (no real tool ever runs) and reports any inherited
        # override name to the log this asserts on.
        synthetic = {
            "CLOUDSDK_CORE_PROJECT": "ambient-synthetic-project",
            "GOOGLE_APPLICATION_CREDENTIALS": "ambient-synthetic-creds.json",
            "TF_CLI_CONFIG_FILE": "ambient-synthetic.tfrc",
        }
        with mock.patch.dict(os.environ, synthetic):
            self.run_reset()
        log = self.gcloud_log.read_text()
        # the reset's gcloud steps ran and the stub intercepted them -
        # otherwise this boundary assertion would prove nothing
        self.assertIn("gcloud config unset project", log)
        self.assertNotIn("leaked-override", log,
                         "ambient cloud-tool overrides reached a spawned tool")
        for key in synthetic:
            self.assertNotIn(key, log, f"{key} leaked into a spawned tool")


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
                self.lab, "terraform", "Landing Zone Challenge", None, None,
                4, [], "qwiklabs-gcp-01-newlab", "us-central1",
                "us-central1-a", refresh=True,
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

    def test_recorded_lab_type_comes_from_metadata_not_file_layout(self):
        # AC10/AC12: refresh identity is the recorded type in
        # .labkit-type, never a .tf file-layout inference - a .tf
        # directory without the record is legacy (the caller's fixed
        # standard fallback), and malformed metadata is a refusal
        # that leaves the file alone
        self.assertEqual(self.lab_start.recorded_lab_type(self.lab),
                         "terraform")
        legacy = self.root / "legacy-lab"
        legacy.mkdir()
        (legacy / "task1.tf").write_text("# a .tf file is not a type record\n")
        self.assertIsNone(self.lab_start.recorded_lab_type(legacy))
        empty = self.root / "empty-lab"
        empty.mkdir()
        self.assertIsNone(self.lab_start.recorded_lab_type(empty))
        # malformed metadata is a refusal that leaves the file alone
        (legacy / ".labkit-type").write_text("puppet\n")
        with self.assertRaises(SystemExit):
            self.lab_start.recorded_lab_type(legacy)
        self.assertEqual((legacy / ".labkit-type").read_bytes(), b"puppet\n")

    def test_refresh_keeps_lists_user_work_only(self):
        # AC12: for the recorded terraform type, keeps excludes what
        # the scaffold rewrites and the reset deletes; everything
        # else - including a legacy lab's foreign-type files - is user
        # work the preview must name
        self.assertEqual(
            self.lab_start.refresh_keeps(self.lab, "terraform"),
            ["shared-vpc/", "task1.tf", "task2.tf",
             "task3.tf", "task4.tf"])
        # the same lab refreshed as standard keeps its Terraform files:
        # only README.md and the record are generated for that type
        self.assertEqual(
            self.lab_start.refresh_keeps(self.lab, "standard"),
            ["backend.tf", "providers.tf", "shared-vpc/",
             "task1.tf", "task2.tf", "task3.tf", "task4.tf",
             "variables.tf"])

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


class ResetLabRevalidationTests(unittest.TestCase):
    """CLI-2: the reset entry the refresh flow calls revalidates its
    target immediately before cleanup.

    A refresh confirms its lab, then may spend minutes fetching the
    lab page before the reset runs. reset_lab resolves the target
    AGAIN, right before the first deletion, so a lab that was deleted
    or swapped for a symlink inside that window is refused - with the
    referent untouched and no gcloud step started.

    The class's stubbed() invocation also pins CLI-10's strict stub
    boundary: an in-process reset runs under exactly the environment
    stub_env builds, the same one a subprocess run gets.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.reset = load_lab_reset(self.repo)
        self.home = self.root / "home"
        self.gcloud_log = self.root / "gcloud.log"

    def stubbed(self):
        # clear=True: EXACTLY the env stub_env builds, never a merge
        # with this shell's ambient cloud/terraform overrides
        return mock.patch.dict(os.environ,
                               stub_env(self.home, self.gcloud_log),
                               clear=True)

    def assert_no_gcloud(self):
        log = self.gcloud_log
        self.assertFalse(log.exists() and log.read_text().strip(),
                         "gcloud was invoked for a refused reset")

    def test_target_swapped_for_a_symlink_is_refused_before_cleanup(self):
        # the refresh-shaped call: an ALREADY-RESOLVED target string -
        # and in the fetch window, the lab is replaced by a symlink
        # aimed at someone else's tree. The revalidation must refuse
        # it before the first deletion; the victim stays byte-identical.
        victim = build_lab_dir(self.root, "victim-lab")
        lab = build_lab_dir(self.repo / "labs", "revalid-lab")
        target = str(lab)
        before = snapshot(victim)
        shutil.rmtree(lab)
        (self.repo / "labs" / "revalid-lab").symlink_to(victim)
        with self.stubbed():
            with self.assertRaises(SystemExit):
                self.reset.reset_lab(target)
        self.assertEqual(before, snapshot(victim),
                         "cleanup ran through a swapped symlink target")
        self.assert_no_gcloud()

    def test_target_deleted_in_the_window_is_refused_before_cleanup(self):
        lab = build_lab_dir(self.repo / "labs", "gone-lab")
        target = str(lab)
        shutil.rmtree(lab)
        with self.stubbed():
            with self.assertRaises(SystemExit):
                self.reset.reset_lab(target)
        self.assert_no_gcloud()

    # --- CLI-10: the stubbed invocation holds the strict stub boundary --
    def test_stubbed_invocation_leaks_no_ambient_cloud_overrides(self):
        # the same boundary for THIS class's stubbed() invocation: a
        # valid reset through it spawns its gcloud steps, and they
        # must run under exactly the environment stub_env builds -
        # synthetic ambient overrides planted around the invocation
        # must not reach the stub tools that intercept every subprocess
        lab = build_lab_dir(self.repo / "labs", "boundary-lab")
        synthetic = {
            "CLOUDSDK_CORE_PROJECT": "ambient-synthetic-project",
            "GOOGLE_APPLICATION_CREDENTIALS": "ambient-synthetic-creds.json",
            "TF_CLI_CONFIG_FILE": "ambient-synthetic.tfrc",
        }
        with mock.patch.dict(os.environ, synthetic):
            with self.stubbed():
                self.assertEqual(self.reset.reset_lab(str(lab)), 0)
        log = self.gcloud_log.read_text()
        # the gcloud steps ran and the stub intercepted them
        self.assertIn("gcloud config unset project", log)
        self.assertNotIn("leaked-override", log,
                         "ambient cloud-tool overrides reached a spawned tool")
        for key in synthetic:
            self.assertNotIn(key, log, f"{key} leaked into a spawned tool")


if __name__ == "__main__":
    unittest.main()
