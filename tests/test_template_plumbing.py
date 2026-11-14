"""Behavior tests for the type-aware template workflow (plan 005).

`labkit start` is public about its three lab types: --type selects
one, and each type generates from its own shipped template. Template
identity and directory lookup stay internal implementation details
and are not frozen here; these tests verify what a user observes:

  * `labkit start --help` offers the --type selector with all three
    public types
  * the standard template ships a markdown lab only - no Terraform
    support files (AC5)
  * a missing SELECTED template fails BEFORE any reset or write
  * a SELECTED template that exists but cannot be READ as text -
    wrong permissions, or bytes no decoder accepts - fails before
    any reset or write too, for every type and for the static files
    as well as the task exemplar: unreadable contents would
    otherwise surface as a mid-write crash, after a fresh lab is
    half-written or after a refresh's reset has already deleted
    state and tfvars and called the credential cleanup (AC10)
  * a broken unrelated template cannot affect the selected run
  * a terraform scaffold matches the shipped terraform template

Exercised against the isolated fixture repo (tests/util.make_repo,
which copies the real template trees file for file), so these tests
read and write neither the real scripts nor the real template trees.
"""

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from util import (FULL_FLAGS, REPO_ROOT, build_lab_dir,
                  make_repo, noncomment_lines, run_cli, snapshot, stub_env)

EXPECTED_TFVARS = (
    "# lab-specific values (*.tfvars is gitignored - stays local)\n"
    'project_id     = "qwiklabs-gcp-01-newlab"\n'
    'compute_region = "us-central1"\n'
    'compute_zone   = "us-central1-a"\n'
)

EXPECTED_FALLBACK_SECTIONS = (
    "## Overview\n\n"
    "<!-- Add lab overview -->\n\n"
    "<!-- Include any images if applicable -->\n\n"
    "## Objectives\n\n"
    "<!-- Add list of objectives -->\n"
)

STANDARD_TEMPLATE = REPO_ROOT / "templates" / "new-standard-lab"


class TemplateBehaviorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)

    # --- the type selector is public ----------------------------------
    def test_help_offers_the_type_selector(self):
        # AC9: --help names --type and the three public types
        env = stub_env(self.root / "home", self.root / "gcloud.log")
        result = subprocess.run(
            [sys.executable, str(self.repo / "labkit"), "start", "--help"],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--type", result.stdout)
        for lab_type in ("standard", "terraform", "cli"):
            self.assertIn(lab_type, result.stdout)

    # --- the standard template ships a markdown lab --------------------
    def test_standard_template_ships_readme_and_task_exemplar_only(self):
        # AC2/AC5: the standard template is README.md plus the
        # task<N>.md exemplar generation renders from - its Terraform
        # support files are gone, so a fresh clone cannot scaffold
        # them into a standard lab
        self.assertTrue((STANDARD_TEMPLATE / "README.md").is_file())
        self.assertTrue((STANDARD_TEMPLATE / "task1.md").is_file())
        for path in sorted(STANDARD_TEMPLATE.rglob("*")):
            self.assertFalse(path.name.endswith((".tf", ".tfvars")),
                             f"{path} ships Terraform support by default")

    # --- template safety -------------------------------------------------
    def run_start(self, env, *argv):
        return run_cli(self.repo, self.repo, env, "", *argv)

    def test_missing_selected_template_fails_before_refresh_reset(self):
        # AC10: the SELECTED type's template is validated BEFORE any
        # reset or rewrite: the managed lab stays byte-identical
        # (tfstate and tfvars included) and no credential is touched
        lab = build_lab_dir(self.repo / "labs", "existing-lab")
        before = snapshot(lab)
        (self.repo / "templates" / "new-terraform-lab" / "providers.tf").unlink()
        env = stub_env(self.root / "tpl-home", self.root / "tpl-gcloud.log")
        result = self.run_start(env, "existing-lab",
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, snapshot(lab),
                         "a missing template must not reset or rewrite the lab")
        log = self.root / "tpl-gcloud.log"
        self.assertFalse(log.exists() and log.read_text().strip(),
                         "gcloud was invoked for a missing template")

    def test_missing_standard_template_fails_before_refresh_reset(self):
        # AC10, standard leg: a legacy lab refreshing as standard
        # needs the standard template - a missing one is refused
        # before the reset, and the fallback is not recorded
        lab = build_lab_dir(self.repo / "labs", "legacy-lab")
        (lab / ".labkit-type").unlink()
        before = snapshot(lab)
        (self.repo / "templates" / "new-standard-lab" / "README.md").unlink()
        env = stub_env(self.root / "tpl-home", self.root / "tpl-gcloud.log")
        result = self.run_start(env, "legacy-lab",
                                "--use-existing", "--yes",
                                "--title", "Legacy", "--tasks", "2",
                                "--skip-init", "--skip-auth")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, snapshot(lab))
        self.assertFalse((lab / ".labkit-type").exists(),
                         "a refused refresh recorded a type")
        log = self.root / "tpl-gcloud.log"
        self.assertFalse(log.exists() and log.read_text().strip(),
                         "gcloud was invoked for a missing template")

    def test_missing_selected_template_fails_before_new_scaffold(self):
        # AC10: no partial generated output may appear anywhere - the
        # task exemplar is part of the selected template too
        (self.repo / "templates" / "new-terraform-lab" / "task1.tf").unlink()
        env = stub_env(self.root / "tpl-home", self.root / "tpl-gcloud.log")
        result = self.run_start(env, "missing-lab", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.repo / "labs" / "missing-lab").exists(),
                         "a partial scaffold appeared under labs/")
        self.assertFalse((self.repo / "missing-lab").exists(),
                         "a partial scaffold appeared in the old root")

    def test_broken_unrelated_template_does_not_block_the_run(self):
        # AC10: only the SELECTED template is validated: a broken
        # template of another type must not affect this run
        (self.repo / "templates" / "new-cli-lab" / "README.md").unlink()
        env = stub_env(self.root / "tpl-home", self.root / "tpl-gcloud.log")
        result = self.run_start(env, "tf-lab", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.repo / "labs" / "tf-lab" / "task1.tf").is_file())

    # --- terraform output matches the shipped template -------------------
    def test_terraform_scaffold_output_matches_template_bytes(self):
        # AC10: a terraform scaffold is exactly the shipped terraform
        # template, rendered - byte parity for the verbatim files, a
        # fully substituted README, task stubs that are the shipped
        # exemplar with only its number and title markers filled
        env = stub_env(self.root / "home", self.root / "gcloud.log")
        # HANG-1: close input so the optional URL prompt cannot inherit open stdin.
        result = subprocess.run(
            [sys.executable, str(self.repo / "labkit"), "start",
             "parity-lab", "--yes", *FULL_FLAGS],
            cwd=self.repo, env=env, input="", capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "parity-lab"
        template = self.repo / "templates" / "new-terraform-lab"
        # byte parity for every verbatim template file
        for name in ("backend.tf", "providers.tf", "variables.tf"):
            self.assertEqual((lab / name).read_bytes(),
                             (template / name).read_bytes(), name)
        # the README is fully rendered: exactly the template with the
        # three placeholders substituted - nothing more, nothing less
        expected_readme = (template / "README.md").read_text()
        for placeholder, value in (
            ("__LAB_TITLE__", "Landing Zone Challenge"),
            ("__LAB_URL__", "<!-- Add lab URL -->"),
            ("__LAB_SECTIONS__", EXPECTED_FALLBACK_SECTIONS),
        ):
            expected_readme = expected_readme.replace(placeholder, value)
        readme = (lab / "README.md").read_text()
        self.assertEqual(readme, expected_readme)
        self.assertIn("# Landing Zone Challenge", readme)
        self.assertNotIn("__LAB_", readme)  # every placeholder filled
        self.assertEqual((lab / "terraform.tfvars").read_text(), EXPECTED_TFVARS)
        # the task stubs are the shipped exemplar with only its
        # __TASK_N__ / __TASK_TITLE__ markers substituted. AC7: without
        # page titles, ask for manual preparation rather than inventing one.
        exemplar = (template / "task1.tf").read_text()
        for n in range(1, 5):
            stub = (lab / f"task{n}.tf").read_text()
            expected = (exemplar.replace("__TASK_N__", str(n))
                        .replace("__TASK_TITLE__", "add this task's title from the lab page"))
            self.assertEqual(stub, expected, f"task{n}.tf")
            self.assertEqual(noncomment_lines(lab / f"task{n}.tf"), [],
                             f"task{n}.tf ships runnable configuration")


# AC10: template content that exists but cannot be read as text. The
# scaffold reads the selected template's files mid-write (the static
# files are copied, the README is rendered, the exemplar is rendered
# per task), so an unreadable file passes an is_file() check and then
# crashes the run late: a fresh lab is left half-written, and a
# refresh crashes only AFTER the reset has already deleted the lab's
# state and tfvars and called the credential cleanup. These pin the
# refusal to before any of that, for every type.
CORRUPT = b"\xff\n"  # decodes as no text encoding

EXEMPLARS = {
    "standard": ("new-standard-lab", "task1.md"),
    "terraform": ("new-terraform-lab", "task1.tf"),
    "cli": ("new-cli-lab", "task1.sh"),
}


class TemplateReadabilityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)

    def run_start(self, env, *argv):
        return run_cli(self.repo, self.repo, env, "", *argv)

    def fake_terraform(self, env: dict, log: Path) -> None:
        """A terraform binary on the stub PATH that logs and exits 1.

        Failing on purpose: an init that should never have run is
        loud in the log and never a real terraform.
        """
        script = Path(env["PATH"]) / "terraform"
        script.write_text(
            "#!/bin/sh\n"
            f'echo "terraform $*" >> {shlex.quote(str(log))}\n'
            "exit 1\n"
        )
        script.chmod(0o755)

    def assert_empty_log(self, log: Path, message: str):
        # the stub tools log every invocation; an empty log is the
        # proof that neither ran
        self.assertFalse(log.exists() and log.read_text().strip(), message)

    def test_ac10_undecodable_exemplar_refuses_fresh_creation_per_type(self):
        # AC10: for every type, a task exemplar that cannot be read
        # as text must refuse BEFORE the lab is written: no partial
        # directory may appear under labs/ (the late render crash
        # the old code hit left the type record, the copied static
        # files, and a rendered README behind), and the refusal is
        # a clean message naming the file, never a raw traceback
        for lab_type, (dirname, exemplar_name) in EXEMPLARS.items():
            with self.subTest(lab_type=lab_type):
                env = stub_env(self.root / f"{lab_type}-home",
                               self.root / f"{lab_type}-gcloud.log")
                flags = ["--yes", "--type", lab_type,
                         "--title", "Corrupt Template", "--tasks", "2",
                         "--skip-auth"]
                if lab_type == "terraform":
                    # no --skip-init: a run that wrongly got past
                    # validation would call the binary - and log it
                    tf_log = self.root / "terraform.log"
                    self.fake_terraform(env, tf_log)
                    flags += ["--project", "qwiklabs-gcp-01-newlab",
                              "--zone", "us-central1-a"]
                exemplar = self.repo / "templates" / dirname / exemplar_name
                exemplar.write_bytes(CORRUPT)
                slug = f"fresh-{lab_type}"
                result = self.run_start(env, slug, *flags)
                self.assertNotEqual(result.returncode, 0,
                                    result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr,
                                 "an unreadable template is a raw crash")
                self.assertIn(exemplar_name, result.stderr,
                              "the refusal does not name the unreadable file")
                self.assertFalse((self.repo / "labs" / slug).exists(),
                                 f"a partial {lab_type} lab was written")
                if lab_type == "terraform":
                    self.assert_empty_log(tf_log, "terraform init ran anyway")

    def test_ac10_undecodable_readme_refuses_fresh_creation(self):
        # AC10: static template contents are read too (the scaffold
        # renders the README for every type), so the same boundary
        # covers them: nothing is written when the selected
        # template's README cannot be read as text
        env = stub_env(self.root / "readme-home", self.root / "readme-gcloud.log")
        readme = self.repo / "templates" / "new-standard-lab" / "README.md"
        readme.write_bytes(CORRUPT)
        result = self.run_start(env, "fresh-std-readme", "--yes",
                                "--type", "standard",
                                "--title", "Corrupt Template",
                                "--tasks", "2", "--skip-auth")
        self.assertNotEqual(result.returncode, 0,
                             result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr,
                         "an unreadable template is a raw crash")
        self.assertIn("README.md", result.stderr,
                      "the refusal does not name the unreadable file")
        self.assertFalse((self.repo / "labs" / "fresh-std-readme").exists(),
                         "a partial standard lab was written")

    def test_ac10_undecodable_exemplar_refuses_refresh_before_reset(self):
        # AC10/AC13: the failure this boundary exists for. An
        # unreadable exemplar used to surface AFTER the reset had
        # already deleted the lab's state and tfvars and called the
        # credential cleanup, leaving a destroyed lab and a
        # traceback. The refusal must keep the whole tree
        # byte-identical - state, tfvars, tasks, README, the type
        # record - with no reset, no init, and no credential call
        lab = build_lab_dir(self.repo / "labs", "refresh-corrupt")
        # a MISSING stub is what makes a refresh read the exemplar:
        # preserved task files are never re-rendered, so without
        # this the corrupted exemplar is never opened at all
        (lab / "task4.tf").unlink()
        before = snapshot(lab)
        gcloud_log = self.root / "refresh-gcloud.log"
        env = stub_env(self.root / "refresh-home", gcloud_log)
        tf_log = self.root / "refresh-terraform.log"
        self.fake_terraform(env, tf_log)  # no --skip-init below
        exemplar = self.repo / "templates" / "new-terraform-lab" / "task1.tf"
        exemplar.write_bytes(CORRUPT)
        result = self.run_start(env, "refresh-corrupt", "--use-existing",
                                "--yes", "--title", "Refresh Corrupt",
                                "--tasks", "4",
                                "--project", "qwiklabs-gcp-01-newlab",
                                "--zone", "us-central1-a")
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr,
                         "an unreadable template is a raw crash")
        self.assertIn("task1.tf", result.stderr,
                      "the refusal does not name the unreadable file")
        self.assertNotIn("LAB RESET", result.stdout,
                         "an unreadable template still reset the lab")
        self.assertEqual(before, snapshot(lab),
                         "an unreadable template changed the lab tree")
        self.assertEqual((lab / ".labkit-type").read_text(), "terraform\n",
                         "the refusal lost the lab's recorded type")
        self.assert_empty_log(gcloud_log, "credentials were touched")
        self.assert_empty_log(tf_log, "terraform init ran anyway")

    def test_ac10_unreadable_permissions_refuse_refresh_before_reset(self):
        # AC10: readability is the same pre-reset boundary when the
        # failure is permissions instead of bytes. Skipped when this
        # user can read mode-000 files (root): then chmod proves
        # nothing about the tool
        exemplar = self.repo / "templates" / "new-terraform-lab" / "task1.tf"
        os.chmod(exemplar, 0o000)
        self.addCleanup(os.chmod, exemplar, 0o644)
        try:
            exemplar.read_text(encoding="utf-8")
        except OSError:
            pass  # the permission is real for this user
        else:
            self.skipTest("this user reads mode-000 files; nothing to prove")
        lab = build_lab_dir(self.repo / "labs", "refresh-perms")
        # same as the corrupt-bytes leg: only a MISSING task stub
        # makes the refresh open the exemplar
        (lab / "task4.tf").unlink()
        before = snapshot(lab)
        gcloud_log = self.root / "perms-gcloud.log"
        env = stub_env(self.root / "perms-home", gcloud_log)
        result = self.run_start(env, "refresh-perms", "--use-existing",
                                "--yes", "--title", "Refresh Perms",
                                "--tasks", "4",
                                "--project", "qwiklabs-gcp-01-newlab",
                                "--zone", "us-central1-a")
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr,
                         "an unreadable template is a raw crash")
        self.assertNotIn("LAB RESET", result.stdout,
                         "an unreadable template still reset the lab")
        self.assertEqual(before, snapshot(lab),
                         "an unreadable template changed the lab tree")
        self.assert_empty_log(gcloud_log, "credentials were touched")

    def test_ac10_legacy_fallback_records_nothing_when_template_undecodable(self):
        # AC10/AC12: the standard template is the SELECTED one for
        # a legacy refresh, so its unreadable contents refuse like
        # any other selected template's - and the fallback's own
        # safeguard holds: the absent .labkit-type stays absent, so
        # nothing is recorded for a refresh that never ran
        lab = build_lab_dir(self.repo / "labs", "legacy-corrupt")
        (lab / ".labkit-type").unlink()
        before = snapshot(lab)
        gcloud_log = self.root / "legacy-gcloud.log"
        env = stub_env(self.root / "legacy-home", gcloud_log)
        exemplar = self.repo / "templates" / "new-standard-lab" / "task1.md"
        exemplar.write_bytes(CORRUPT)
        result = self.run_start(env, "legacy-corrupt", "--use-existing",
                                "--yes", "--title", "Legacy Corrupt",
                                "--tasks", "4",
                                "--project", "qwiklabs-gcp-01-newlab",
                                "--zone", "us-central1-a")
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr,
                         "an unreadable template is a raw crash")
        self.assertNotIn("LAB RESET", result.stdout,
                         "an unreadable template still reset the lab")
        self.assertEqual(before, snapshot(lab),
                         "an unreadable template changed the lab tree")
        self.assertFalse((lab / ".labkit-type").exists(),
                         "a refused refresh recorded the fallback type")
        self.assert_empty_log(gcloud_log, "credentials were touched")

    def test_ac10_corrupted_unrelated_template_does_not_block_the_run(self):
        # AC10: only the SELECTED template is validated. A CLI
        # exemplar that cannot be read as text must not affect a
        # terraform run (the missing-file class of this boundary is
        # covered above; this is its decode class)
        env = stub_env(self.root / "home", self.root / "gcloud.log")
        (self.repo / "templates" / "new-cli-lab" / "task1.sh").write_bytes(CORRUPT)
        result = self.run_start(env, "tf-despite-cli", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)
        lab = self.repo / "labs" / "tf-despite-cli"
        self.assertTrue((lab / "task1.tf").is_file())
        self.assertTrue((lab / "terraform.tfvars").is_file())


if __name__ == "__main__":
    unittest.main()
