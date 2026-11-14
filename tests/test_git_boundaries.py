"""Git boundary tests for the 002 layout - RED until Phases 2 and 3.

Disposable git repositories (git init + the real repo's current ignore
rules) pin the tracking contract:

  * lab contents under labs/ and payloads under archives/ are ignored
    - `git add -A` can never stage user lab work or an archive
  * the tracked placeholders (labs/.gitkeep, archives/.gitkeep) stay
    trackable
  * CLI-11/10: the actual Git index ships only tool files and templates,
    not completed labs, state, or archive payloads
  * cloned fixture launchers expose the public commands without cloud calls

No test requires a local lab or archive to exist.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from util import (REPO_ROOT, make_git_repo, run_git, stub_env)


class IgnoreRuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)
        self.repo = make_git_repo(self.workdir)

    def ignored(self, rel_path):
        result = run_git(self.repo, "check-ignore", "-q", rel_path, check=False)
        self.assertEqual(result.returncode, 0, f"{rel_path} is not ignored")
        self.assertEqual(result.stdout, "")

    def test_lab_contents_are_ignored(self):
        lab = self.repo / "labs" / "wip-lab"
        lab.mkdir()
        (lab / "task1.tf").write_text("# in-progress work\n")
        (lab / "terraform.tfvars").write_text('project_id = "qwiklabs-gcp-00-x"\n')
        (lab / "terraform.tfstate").write_text("{}")
        self.ignored("labs/wip-lab/task1.tf")
        self.ignored("labs/wip-lab/terraform.tfvars")
        self.ignored("labs/wip-lab/terraform.tfstate")

    def test_archive_payloads_are_ignored(self):
        (self.repo / "archives" / "done-lab.zip").write_bytes(b"PK\x05\x06fake zip")
        self.ignored("archives/done-lab.zip")

    def test_add_all_never_stages_lab_work_or_payloads(self):
        lab = self.repo / "labs" / "wip-lab"
        lab.mkdir()
        (lab / "task1.tf").write_text("# in-progress work\n")
        (self.repo / "archives" / "done-lab.zip").write_bytes(b"PK\x05\x06fake zip")
        run_git(self.repo, "add", "-A")
        status = run_git(self.repo, "status", "--porcelain").stdout
        self.assertNotIn("wip-lab", status)
        self.assertNotIn("done-lab.zip", status)

    def test_placeholders_stay_trackable(self):
        run_git(self.repo, "add", "labs/.gitkeep", "archives/.gitkeep")
        status = run_git(self.repo, "status", "--porcelain").stdout
        self.assertIn("A  labs/.gitkeep", status)
        self.assertIn("A  archives/.gitkeep", status)


class TrackedRepositoryTests(unittest.TestCase):
    def test_cli11_tracked_candidate_is_scaffolding_only(self):
        # CLI-11/10: inspect the actual index, not make_repo's curated tree.
        # Local ignored/untracked labs are permitted and are not clone contents.
        tracked = set(run_git(REPO_ROOT, "ls-files", "--cached", "-z")
                      .stdout.split("\0")) - {""}
        allowed_roots = {".devcontainer", ".flox", ".gitignore", ".plans",
                         "AGENTS.md", "README.md", "labkit", "labkit_cli",
                         "templates", "labs", "archives", "tests", "scripts"}
        unexpected = {path for path in tracked
                      if Path(path).parts[0] not in allowed_roots}
        self.assertEqual(unexpected, set(), sorted(unexpected))
        for collection in ("labs", "archives"):
            with self.subTest(collection=collection):
                self.assertEqual({path for path in tracked
                                  if path.startswith(collection + "/")},
                                 {f"{collection}/.gitkeep"})
        stray_terraform = {path for path in tracked
                           if path.endswith(".tf") and not path.startswith("templates/")}
        self.assertEqual(stray_terraform, set(), sorted(stray_terraform))
        local_state = {path for path in tracked
                       if ".tfstate" in Path(path).name or ".tfvars" in Path(path).name}
        self.assertEqual(local_state, set(), sorted(local_state))


class ClonedLauncherSmokeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)

    def test_cli11_cloned_fixture_launchers_expose_commands_and_templates(self):
        # CLI-11/10: smoke-test fixture packaging, not real repository cleanliness.
        source = make_git_repo(self.workdir)
        run_git(source, "add", ".")
        run_git(source, "commit", "-m", "candidate tool", "--no-verify")
        clone = self.workdir / "clone"
        result = run_git(source, "clone", "--quiet", str(source), str(clone))
        self.assertEqual(result.returncode, 0, result.stderr)
        env = stub_env(self.workdir / "home", self.workdir / "gcloud.log")
        # RESTORE-1/3: a fresh clone exposes restore through the sole CLI.
        for args in (("--help",), ("start", "--help"),
                     ("reset", "--help"), ("archive", "--help"),
                     ("restore", "--help")):
            result = subprocess.run([str(clone / "labkit"), *args],
                                    cwd=self.workdir, env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage: labkit", result.stdout)
        self.assertFalse((self.workdir / "gcloud.log").exists())
        self.assertFalse((self.workdir / "home" / ".cache").exists())
        # managed dirs exist via their tracked placeholders
        self.assertTrue((clone / "labs" / ".gitkeep").is_file())
        self.assertTrue((clone / "archives" / ".gitkeep").is_file())
        instructions = clone / "templates" / "new-terraform-lab" / "README.md"
        self.assertTrue(instructions.is_file())
        # every template file the repository ships reaches a fresh
        # clone, for all three lab types
        shipped = {str(p.relative_to(REPO_ROOT / "templates"))
                   for p in (REPO_ROOT / "templates").rglob("*")
                   if p.is_file()}
        cloned = {str(p.relative_to(clone / "templates"))
                  for p in (clone / "templates").rglob("*")
                  if p.is_file()}
        self.assertTrue(shipped, "no template files found to compare")
        self.assertEqual(cloned, shipped,
                         "the fresh clone's templates differ from the "
                         "repository's")


if __name__ == "__main__":
    unittest.main()
