"""CLI tests for lab-reset's 002 target contract - RED until Phase 2.

A bare slug resolves to the managed lab under labs/; an explicit path
(absolute or relative) must identify an individual managed lab; the
collection roots (the repository root, labs/, templates/, archives/,
and their ancestors), symlinked targets, and traversal targets are
refused BEFORE any file deletion or gcloud change. A refused
operation leaves the tree byte-identical with no gcloud
invocations, with or without --dry-run. A valid reset touches
exactly the named lab - siblings stay byte-identical.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from util import (build_lab_dir, make_repo, run_reset, same_file_probe,
                  snapshot, snapshot_nofollow, stub_env)


class ResetCliTests(unittest.TestCase):
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

    def run_reset_cli(self, cwd, *argv):
        return run_reset(self.repo, cwd, self.env, *argv)

    def assert_no_gcloud(self):
        log = self.gcloud_log
        self.assertFalse(log.exists() and log.read_text().strip(),
                         "gcloud was invoked during a refused reset")

    def assert_refused_untouched(self, result, before):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, snapshot(self.repo),
                         "a refused reset changed the tree")
        self.assert_no_gcloud()

    # --- the managed targets (slug / explicit path / current dir) -----
    def test_bare_slug_resets_managed_lab(self):
        lab = build_lab_dir(self.repo / "labs", "my-lab")
        result = self.run_reset_cli(self.scratch, "my-lab")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((lab / "terraform.tfstate").exists())
        self.assertFalse((lab / "terraform.tfvars").exists())
        self.assertTrue((lab / "task1.tf").is_file())  # user work kept
        self.assertIn("auth application-default revoke --quiet",
                      self.gcloud_log.read_text())

    def test_explicit_path_resets_managed_lab(self):
        lab = build_lab_dir(self.repo / "labs", "my-lab")
        result = self.run_reset_cli(self.scratch, str(lab))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((lab / "terraform.tfstate").exists())
        self.assertTrue((lab / "task1.tf").is_file())

    def test_no_argument_resets_current_managed_lab(self):
        lab = build_lab_dir(self.repo / "labs", "my-lab")
        result = self.run_reset_cli(lab)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((lab / "terraform.tfstate").exists())
        self.assertTrue((lab / "task1.tf").is_file())

    def test_relative_explicit_path_resets_managed_lab(self):
        # from the repo root, labs/<slug> is a valid explicit target
        lab = build_lab_dir(self.repo / "labs", "my-lab")
        result = self.run_reset_cli(self.repo, "labs/my-lab")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((lab / "terraform.tfstate").exists())
        self.assertTrue((lab / "task1.tf").is_file())

    # --- a valid reset touches exactly the named lab -------------------
    def test_reset_touches_only_the_target_lab(self):
        target = build_lab_dir(self.repo / "labs", "my-lab")
        sibling = build_lab_dir(self.repo / "labs", "other-lab")
        before_sibling = snapshot(sibling)
        result = self.run_reset_cli(self.scratch, "my-lab")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((target / "terraform.tfstate").exists())
        self.assertFalse((target / "terraform.tfvars").exists())
        self.assertTrue((target / "task1.tf").is_file())  # user work kept
        self.assertEqual(before_sibling, snapshot(sibling),
                         "a sibling lab must be byte-identical after reset")

    def test_reset_does_not_litter_pycache(self):
        # lab-reset loads lab-start as a module; that must not drop a
        # __pycache__/ next to the scripts - the repository tree is
        # not a side effect of a reset
        build_lab_dir(self.repo / "labs", "my-lab")
        result = self.run_reset_cli(self.scratch, "my-lab")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(self.repo.rglob("__pycache__")), [])

    # --- destructive refusal: collection roots ------------------------
    def test_repo_root_refused(self):
        build_lab_dir(self.repo / "labs", "stale-lab")  # state that must survive
        before = snapshot(self.repo)
        result = self.run_reset_cli(self.repo)  # cwd = repo root, no argument
        self.assert_refused_untouched(result, before)

    def test_collection_roots_refused(self):
        build_lab_dir(self.repo / "labs", "stale-lab")
        before = snapshot(self.repo)
        for target in ("labs", "templates", "archives", str(self.workdir)):
            result = self.run_reset_cli(self.repo, target)
            self.assertNotEqual(result.returncode, 0, f"target: {target}")
        self.assertEqual(before, snapshot(self.repo))
        log = self.gcloud_log
        self.assertFalse(log.exists() and log.read_text().strip(),
                         "gcloud was invoked during a refused reset")

    # --- destructive refusal: the old flat-root labs ------------------
    def test_old_root_lab_refused(self):
        legacy = build_lab_dir(self.repo, "legacy-lab")
        before = snapshot(self.repo)
        result = self.run_reset_cli(self.repo, "legacy-lab")
        self.assert_refused_untouched(result, before)

    def test_no_argument_in_old_root_lab_refused(self):
        legacy = build_lab_dir(self.repo, "legacy-lab")
        before = snapshot(self.repo)
        result = self.run_reset_cli(legacy)  # cwd = old-root lab, no argument
        self.assert_refused_untouched(result, before)

    # --- destructive refusal: symlinks and traversal --------------------
    def test_managed_symlink_target_refused(self):
        # labs/link-lab is a symlink to a lab outside the repo: the
        # bare slug must never reset through it, dry-run or not
        victim = build_lab_dir(self.workdir, "victim-lab")
        link = self.repo / "labs" / "link-lab"
        link.symlink_to(victim)
        before = snapshot(victim)
        for extra in ((), ("--dry-run",)):
            with self.subTest(dry_run=bool(extra)):
                result = self.run_reset_cli(self.scratch, "link-lab", *extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(before, snapshot(victim),
                                 "reset ran through a managed symlink")
        self.assert_no_gcloud()

    def test_traversal_target_refused(self):
        # explicit paths that walk out of the managed tree must be
        # refused before any deletion or credential change
        victim = build_lab_dir(self.workdir, "victim-lab")
        before = snapshot(victim)
        for target in ("../victim-lab", "labs/../../victim-lab"):
            for extra in ((), ("--dry-run",)):
                with self.subTest(target=target, dry_run=bool(extra)):
                    result = self.run_reset_cli(self.scratch, target, *extra)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(before, snapshot(victim),
                                     f"reset traversed into {target}")
        self.assert_no_gcloud()

    def test_labs_root_symlink_refused(self):
        # the labs/ collection itself replaced by a symlink: neither
        # the collection root nor a lab reached through it may be
        # reset, and nothing behind the symlink may change
        victim_root = self.workdir / "victim-tree"
        hostage = victim_root / "hostage-lab"
        hostage.mkdir(parents=True)
        (hostage / "terraform.tfstate").write_text("{}")
        (hostage / "terraform.tfvars").write_text('project_id = "x"\n')
        (hostage / "task1.tf").write_text("# user work\n")
        before = snapshot(victim_root)
        shutil.rmtree(self.repo / "labs")
        (self.repo / "labs").symlink_to(victim_root)
        for target in ("labs", "hostage-lab"):
            for extra in ((), ("--dry-run",)):
                with self.subTest(target=target, dry_run=bool(extra)):
                    result = self.run_reset_cli(self.repo, target, *extra)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(before, snapshot(victim_root),
                                     f"reset ran through the labs/ symlink "
                                     f"to {target}")
        self.assert_no_gcloud()

    # --- destructive refusal: traversal that normalizes inside labs --
    def test_dotdot_component_target_refused(self):
        # `labs/detour/../victim-lab` normalizes to a valid managed
        # lab once resolved - but a literal '..' component never
        # names a lab, whatever it normalizes to. Refused before any
        # deletion or gcloud call, dry-run or not; the victim's
        # state/tfvars stay byte-identical.
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        (self.repo / "labs" / "detour").mkdir()  # the pivot must exist
        before = snapshot(victim)
        cases = [  # (cwd, target)
            (self.repo, "labs/detour/../victim-lab"),
            (self.repo / "labs", "../labs/victim-lab"),
        ]
        for cwd, target in cases:
            for extra in ((), ("--dry-run",)):
                with self.subTest(cwd=cwd.name, target=target,
                                  dry_run=bool(extra)):
                    result = self.run_reset_cli(cwd, target, *extra)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(before, snapshot(victim),
                                     f"reset accepted {target!r}")
        self.assert_no_gcloud()

    def test_dot_component_explicit_path_still_resets(self):
        # '.' components are benign and stay valid: labs/./<slug>
        # still names the managed lab (Path drops them on its own)
        lab = build_lab_dir(self.repo / "labs", "my-lab")
        result = self.run_reset_cli(self.repo, "labs/./my-lab")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((lab / "terraform.tfstate").exists())
        self.assertTrue((lab / "task1.tf").is_file())

    # --- aliases that resolve onto a managed lab are refused ----------
    def test_root_alias_to_managed_lab_refused(self):
        # repo/legacy-alias -> labs/victim-lab: the supplied path
        # RESOLVES onto a managed lab but does not NAME it. The reset
        # must not run through the alias, dry-run or not; the victim's
        # state and tfvars stay byte-identical and no gcloud runs.
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        (self.repo / "legacy-alias").symlink_to(victim)
        before = snapshot(victim)
        for extra in ((), ("--dry-run",)):
            with self.subTest(dry_run=bool(extra)):
                result = self.run_reset_cli(self.repo, "./legacy-alias", *extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(before, snapshot(victim),
                                 "reset ran through a root-level alias")
        self.assert_no_gcloud()

    def test_nested_alias_to_managed_lab_refused(self):
        # labs/other-lab/alias -> labs/victim-lab: an alias nested in
        # another lab resolves onto the victim - refused however it
        # normalizes, dry-run or not
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = self.repo / "labs" / "other-lab"
        other.mkdir()
        (other / "alias").symlink_to(victim)
        before = snapshot(victim)
        for extra in ((), ("--dry-run",)):
            with self.subTest(dry_run=bool(extra)):
                result = self.run_reset_cli(self.repo,
                                            "labs/other-lab/alias", *extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(before, snapshot(victim),
                                 "reset ran through a nested alias")
        self.assert_no_gcloud()

    def test_deep_nested_alias_to_managed_lab_refused(self):
        # labs/other-lab/alias -> labs/, so the supplied path's FINAL
        # component is clean and resolves onto the victim - the
        # boundary must be enforced on the supplied spelling, not
        # only on the resolved path
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = self.repo / "labs" / "other-lab"
        other.mkdir()
        (other / "alias").symlink_to(self.repo / "labs")
        before = snapshot(victim)
        for extra in ((), ("--dry-run",)):
            with self.subTest(dry_run=bool(extra)):
                result = self.run_reset_cli(self.repo,
                                            "labs/other-lab/alias/victim-lab",
                                            *extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(before, snapshot(victim),
                                 "reset ran through a deep nested alias")
        self.assert_no_gcloud()

    def test_nested_labs_alias_path_refused(self):
        # labs/other-lab/labs -> labs/: the supplied path's final two
        # components are a clean labs/<slug> pair and it resolves
        # onto the victim, but the components between the repo
        # boundary and the target detour through another lab. The
        # ENTIRE supplied path below the repo root must be exactly
        # labs/<slug> - not just the suffix - so the reset is refused
        # before any deletion or credential change, dry-run or not,
        # and both the victim and the sibling stay byte-identical.
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = build_lab_dir(self.repo / "labs", "other-lab")
        (other / "labs").symlink_to(self.repo / "labs")
        before_victim = snapshot(victim)
        before_other = snapshot_nofollow(other)
        for extra in ((), ("--dry-run",)):
            with self.subTest(dry_run=bool(extra)):
                result = self.run_reset_cli(self.repo,
                                            "labs/other-lab/labs/victim-lab",
                                            *extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(before_victim, snapshot(victim),
                                 "reset deleted the victim through a "
                                 "nested labs alias")
                self.assertEqual(before_other, snapshot_nofollow(other),
                                 "a refused reset changed the sibling lab")
        self.assert_no_gcloud()

    def test_nested_repo_alias_path_refused(self):
        # labs/other-lab/repo-alias -> the repository root: the path
        # re-enters the repo AT the root and then spells a perfect
        # labs/<slug> suffix. The boundary is the FIRST entry into
        # the repo; below it only labs/<slug> itself is accepted, so
        # an alias inside the boundary cannot smuggle the suffix in.
        # Refused before any deletion or credential change, dry-run
        # or not; victim and sibling stay byte-identical.
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = build_lab_dir(self.repo / "labs", "other-lab")
        (other / "repo-alias").symlink_to(self.repo)
        before_victim = snapshot(victim)
        before_other = snapshot_nofollow(other)
        for extra in ((), ("--dry-run",)):
            with self.subTest(dry_run=bool(extra)):
                result = self.run_reset_cli(
                    self.repo, "labs/other-lab/repo-alias/labs/victim-lab",
                    *extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(before_victim, snapshot(victim),
                                 "reset deleted the victim through a "
                                 "nested repo alias")
                self.assertEqual(before_other, snapshot_nofollow(other),
                                 "a refused reset changed the sibling lab")
        self.assert_no_gcloud()

    # --- case-equivalent root spellings cannot launder a detour ------
    def _require_case_equivalent_fs(self):
        """Skip when a case-variant spelling is NOT the same file.

        The probe is a dev+ino stat comparison, so a case-sensitive
        filesystem (or a missing variant) honestly answers False -
        two genuinely distinct directories are never equated.
        """
        if not same_file_probe(self.repo, self.repo.name.upper()):
            self.skipTest(
                "filesystem treats case spellings as distinct files")

    def test_case_variant_root_detour_alias_refused(self):
        # On a case-insensitive filesystem, <workdir>/REPO IS the
        # repository root: same file, different string - and
        # Path.resolve() keeps the caller's spelling. The supplied
        # path enters the repo through that case-variant root, detours
        # through another lab, and returns to the canonical spelling
        # via repo-alias, so a STRING boundary walk finds its first
        # entry at the alias and the clean labs/<slug> suffix launders
        # the whole detour. The boundary must be matched by
        # filesystem identity, or this reset deletes the victim's
        # state/tfvars - dry-run or not, with gcloud calls. Refused:
        # victim and sibling byte-identical, zero gcloud.
        self._require_case_equivalent_fs()
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = build_lab_dir(self.repo / "labs", "other-lab")
        (other / "repo-alias").symlink_to(self.repo)
        detour = (self.repo.parent / self.repo.name.upper() / "labs"
                  / "other-lab" / "repo-alias" / "labs" / "victim-lab")
        before_victim = snapshot(victim)
        before_other = snapshot_nofollow(other)
        for extra in ((), ("--dry-run",)):
            with self.subTest(dry_run=bool(extra)):
                result = self.run_reset_cli(self.scratch, str(detour), *extra)
                self.assertNotEqual(
                    result.returncode, 0,
                    "reset ran through a case-variant root detour")
                self.assertEqual(before_victim, snapshot(victim),
                                 "reset deleted the victim's state through "
                                 "a case-variant root detour")
                self.assertEqual(before_other, snapshot_nofollow(other),
                                 "a refused reset changed the sibling lab")
        self.assert_no_gcloud()

    def test_case_variant_below_root_entry_refused(self):
        # An outside alias aimed at a case-variant spelling of a lab
        # INTERIOR (<workdir>/REPO/labs/other-lab): the prefix resolves
        # below the repo root, but its string spells REPO, so a string
        # ancestry check (is_relative_to) misses it the same way the
        # root-equality check does. The ancestor chain must be checked
        # by filesystem identity, or the repo-alias return to the
        # canonical root looks like a clean first entry and the reset
        # runs. Refused before any deletion or credential change,
        # dry-run or not; victim and sibling byte-identical.
        self._require_case_equivalent_fs()
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = build_lab_dir(self.repo / "labs", "other-lab")
        (other / "repo-alias").symlink_to(self.repo)
        inside = self.workdir / "inside-alias"
        inside.symlink_to(self.repo.parent / self.repo.name.upper()
                         / "labs" / "other-lab")
        detour = inside / "repo-alias" / "labs" / "victim-lab"
        before_victim = snapshot(victim)
        before_other = snapshot_nofollow(other)
        for extra in ((), ("--dry-run",)):
            with self.subTest(dry_run=bool(extra)):
                result = self.run_reset_cli(self.scratch, str(detour), *extra)
                self.assertNotEqual(
                    result.returncode, 0,
                    "reset ran through a case-variant below-root entry")
                self.assertEqual(before_victim, snapshot(victim),
                                 "reset deleted the victim's state through "
                                 "a case-variant below-root entry")
                self.assertEqual(before_other, snapshot_nofollow(other),
                                 "a refused reset changed the sibling lab")
        self.assert_no_gcloud()

    def test_ancestor_alias_explicit_path_still_resets(self):
        # a symlinked ANCESTOR (a platform-style alias of the repo
        # directory, like macOS /var vs /private/var) keeps explicit
        # paths valid: only the lab target itself may not be an alias
        lab = build_lab_dir(self.repo / "labs", "my-lab")
        alias = self.workdir / "repo-alias"
        alias.symlink_to(self.repo)
        result = self.run_reset_cli(self.scratch, str(alias / "labs" / "my-lab"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((lab / "terraform.tfstate").exists())
        self.assertTrue((lab / "task1.tf").is_file())


if __name__ == "__main__":
    unittest.main()
