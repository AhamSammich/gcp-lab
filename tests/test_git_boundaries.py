"""Git boundary tests for the 002 layout - RED until Phases 2 and 3.

Disposable git repositories (git init + the real repo's current ignore
rules) pin the tracking contract:

  * lab contents under labs/ and payloads under archives/ are ignored
    - `git add -A` can never stage user lab work or an archive
  * the tracked placeholders (labs/.gitkeep, archives/.gitkeep) stay
    trackable
  * CLI-11/10: a fresh clone of a committed candidate fixture is a tool: the
    target layout with both template directories, no completed-lab
    trees, no archive payloads, no flat scaffold dir

No test requires a local lab or archive to exist.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from util import (TEMPLATE_FILES, make_git_repo, run_git, stub_env)


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


class FreshCloneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)

    def test_fresh_clone_is_scaffolding_only(self):
        # CLI-11/10: candidate files only, not evidence about real checkout HEAD.
        source = make_git_repo(self.workdir)
        run_git(source, "add", ".")
        run_git(source, "commit", "-m", "candidate tool", "--no-verify")
        clone = self.workdir / "clone"
        result = run_git(source, "clone", "--quiet", str(source), str(clone))
        self.assertEqual(result.returncode, 0, result.stderr)
        # the root carries the tool only - no completed-lab trees.
        # The final executable and its package, with no legacy launchers.
        allowed = {".git", ".gitignore", ".plans", "AGENTS.md", "README.md",
                   "labkit", "cloud_lab", "templates", "labs", "archives",
                   "tests", "scripts"}
        top = {p.name for p in clone.iterdir()}
        self.assertEqual(top - allowed, set(), sorted(top - allowed))
        self.assertIn("labkit", top)
        for obsolete in ("cloud-lab", "lab-start", "lab-reset"):
            self.assertNotIn(obsolete, top)
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
        self.assertEqual({p.name for p in (clone / "labs").iterdir()}, {".gitkeep"})
        self.assertEqual({p.name for p in (clone / "archives").iterdir()}, {".gitkeep"})
        self.assertFalse((clone / "scripts" / "restore_lab.py").exists())
        instructions = clone / "templates" / "new-terraform-lab" / "README.md"
        self.assertTrue(instructions.is_file())
        # the standard template moved with its history
        for name in TEMPLATE_FILES:
            self.assertTrue((clone / "templates" / "new-standard-lab" / name).is_file(),
                            name)
        # the flat scaffold dir is gone
        self.assertFalse((clone / "terraform-lab-scaffold").exists())
        # no terraform anywhere outside templates/
        stray_tf = [str(p) for p in clone.rglob("*.tf")
                    if "templates" not in p.parts]
        self.assertEqual(stray_tf, [])


if __name__ == "__main__":
    unittest.main()
