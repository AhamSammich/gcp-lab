"""Component tests for internal template plumbing - RED until Phase 2.

The CLI stays standard-only: no --template flag, no selection prompt,
no auto-detection, no alternate end-to-end workflow. Template
identity and directory lookup live behind a small internal boundary
on the lab-start module:

    DEFAULT_TEMPLATE  - "standard", the implicit choice
    TEMPLATE_IDS      - every known template ("standard", "terraform")
    template_dir(name=DEFAULT_TEMPLATE) -> Path under templates/

Exercised against the isolated fixture repo (tests/util.make_repo),
so these tests read and write neither the real scripts nor the real
template trees.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from util import (FULL_FLAGS, REPO_ROOT, TEMPLATE_FILES, build_lab_dir,
                  load_lab_start, make_repo, run_cli, snapshot, stub_env)

EXPECTED_TFVARS = (
    "# lab-specific values (*.tfvars is gitignored - stays local)\n"
    'project_id     = "qwiklabs-gcp-01-newlab"\n'
    'compute_region = "us-central1"\n'
    'compute_zone   = "us-central1-a"\n'
)


class TemplatePlumbingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)

    def load(self):
        return load_lab_start(self.repo)

    # --- internal identity and lookup --------------------------------
    def test_default_template_is_standard(self):
        self.assertEqual(self.load().DEFAULT_TEMPLATE, "standard")

    def test_registry_lists_both_templates(self):
        self.assertEqual(set(self.load().TEMPLATE_IDS), {"standard", "terraform"})

    def test_default_resolves_to_relocated_standard_dir(self):
        mod = self.load()
        expected = self.repo / "templates" / "new-standard-lab"
        self.assertEqual(mod.template_dir(), expected)
        self.assertEqual(mod.template_dir(mod.DEFAULT_TEMPLATE), expected)
        for name in TEMPLATE_FILES:
            self.assertTrue((expected / name).is_file(), name)

    def test_named_template_resolution(self):
        mod = self.load()
        self.assertEqual(mod.template_dir("standard"),
                         self.repo / "templates" / "new-standard-lab")
        terraform = mod.template_dir("terraform")
        self.assertEqual(terraform, self.repo / "templates" / "new-terraform-lab")
        self.assertTrue(terraform.is_dir())

    def test_unknown_template_name_refused(self):
        with self.assertRaises(ValueError):
            self.load().template_dir("bogus")

    # --- nothing public is exposed ------------------------------------
    def test_no_public_template_selector(self):
        env = stub_env(self.root / "home", self.root / "gcloud.log")
        result = subprocess.run(
            [sys.executable, str(self.repo / "lab-start"), "--help"],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("--template", result.stdout)

    # --- the real repo ships both template directories -----------------
    def test_terraform_instructions_template_ships(self):
        # a fresh clone must carry the instructions template, not just
        # the internal plumbing that names it
        readme = REPO_ROOT / "templates" / "new-terraform-lab" / "README.md"
        self.assertTrue(readme.is_file(),
                        "templates/new-terraform-lab/README.md is not tracked yet")
        text = readme.read_text()
        # the same title, URL, and extracted-section placeholders as
        # the standard README
        for placeholder in ("__LAB_TITLE__", "__LAB_URL__", "__LAB_SECTIONS__"):
            self.assertIn(placeholder, text)
        # it explains the deferred workflow: authenticating with the
        # student account, cloning the lab's upstream repository into
        # the working lab directory, selecting the instructed
        # subdirectory, and following that repository's own
        # init/variables/backend instructions
        self.assertRegex(text, r"(?i)student account")
        self.assertRegex(text, r"(?i)clone")
        self.assertRegex(text, r"(?i)subdirectory")
        self.assertRegex(text, r"(?i)\binit\b")
        self.assertRegex(text, r"(?i)backend")
        # ...without inventing upstream values: no concrete
        # repository URL, host, or branch - the lab page names those
        self.assertNotRegex(text, r"(?i)git\s+clone\s+(https?|git@|[\w.-]+[:/])")
        self.assertNotRegex(text, r"(?i)github\.com|gitlab\.com|bitbucket")
        self.assertNotRegex(text, r"(?i)--branch\s+\S+")

    # --- template safety -------------------------------------------------
    def run_standard(self, env, *argv):
        return run_cli(self.repo, self.repo, env, "", *argv)

    def test_missing_standard_template_fails_before_refresh_reset(self):
        # the selected template is validated BEFORE any reset or
        # rewrite: the managed lab stays byte-identical (tfstate and
        # tfvars included) and no credential is touched
        lab = build_lab_dir(self.repo / "labs", "existing-lab")
        before = snapshot(lab)
        (self.repo / "templates" / "new-standard-lab" / "providers.tf").unlink()
        env = stub_env(self.root / "tpl-home", self.root / "tpl-gcloud.log")
        result = self.run_standard(env, "existing-lab",
                                    "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, snapshot(lab),
                         "a missing template must not reset or rewrite the lab")
        log = self.root / "tpl-gcloud.log"
        self.assertFalse(log.exists() and log.read_text().strip(),
                         "gcloud was invoked for a missing template")

    def test_missing_standard_template_fails_before_new_scaffold(self):
        # no partial generated output may appear anywhere
        (self.repo / "templates" / "new-standard-lab" / "providers.tf").unlink()
        env = stub_env(self.root / "tpl-home", self.root / "tpl-gcloud.log")
        result = self.run_standard(env, "missing-lab", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.repo / "labs" / "missing-lab").exists(),
                         "a partial scaffold appeared under labs/")
        self.assertFalse((self.repo / "missing-lab").exists(),
                         "a partial scaffold appeared in the old root")

    def test_missing_terraform_template_does_not_break_standard_mode(self):
        # only the SELECTED template is validated: a broken unrelated
        # instructions template must not affect a standard run
        (self.repo / "templates" / "new-terraform-lab" / "README.md").unlink()
        env = stub_env(self.root / "tpl-home", self.root / "tpl-gcloud.log")
        result = self.run_standard(env, "standard-lab", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.repo / "labs" / "standard-lab" / "task1.tf").is_file())

    # --- standard output stays equivalent through the new plumbing -----
    def test_standard_scaffold_output_matches_template_bytes(self):
        mod = self.load()
        env = stub_env(self.root / "home", self.root / "gcloud.log")
        result = subprocess.run(
            [sys.executable, str(self.repo / "lab-start"),
             "parity-lab", "--yes", *FULL_FLAGS],
            cwd=self.repo, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "parity-lab"
        template = self.repo / "templates" / "new-standard-lab"
        # byte parity for every verbatim template file
        for name in ("backend.tf", "providers.tf", "variables.tf"):
            self.assertEqual((lab / name).read_bytes(),
                             (template / name).read_bytes(), name)
        # the README is fully rendered: exactly the template with the
        # three placeholders substituted - nothing more, nothing less
        expected_readme = (template / "README.md").read_text()
        for placeholder, value in (
            (mod.README_PLACEHOLDERS["title"], "Landing Zone Challenge"),
            (mod.README_PLACEHOLDERS["url"], "<!-- Add lab URL -->"),
            (mod.README_PLACEHOLDERS["sections"], mod.FALLBACK_SECTIONS),
        ):
            expected_readme = expected_readme.replace(placeholder, value)
        readme = (lab / "README.md").read_text()
        self.assertEqual(readme, expected_readme)
        self.assertIn("# Landing Zone Challenge", readme)
        self.assertNotIn("__LAB_", readme)  # every placeholder filled
        self.assertEqual((lab / "terraform.tfvars").read_text(), EXPECTED_TFVARS)
        for n in range(1, 5):
            self.assertEqual(
                (lab / f"task{n}.tf").read_text(),
                mod.TASK_FILE_TEMPLATE.format(n=n, title=""))


if __name__ == "__main__":
    unittest.main()
