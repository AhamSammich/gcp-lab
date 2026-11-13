"""CLI tests for the 002 target layout - RED until Phase 2 (Build).

These pin the target-resolution contract from
.plans/002-directory-restructure.md:

  * a bare slug maps to REPO_ROOT/labs/<slug> from ANY working
    directory, and the preview/next-steps name that resolved target
  * prompted names and the new-name collision validator use the same
    labs/ mapping - never the caller's directory, never the old root
  * refresh (--use-existing) hits the managed lab or fails loudly on
    a missing one; it never silently scaffolds a replacement
  * old-root targets, explicit paths outside labs/, traversal, and
    symlink escapes are refused before fetch/reset/write

Every run targets the isolated fixture repo (tests/util.make_repo),
so nothing here can ever write into the real checkout.
"""

import shlex
import shutil
import tempfile
import unittest
from pathlib import Path

from util import (FULL_FLAGS, build_lab_dir, make_repo,
                  run_cli, same_file_probe, snapshot, snapshot_nofollow,
                  stub_env)

NEW_PROJECT = "qwiklabs-gcp-01-newlab"


class LayoutCliTests(unittest.TestCase):
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

    def run_start(self, cwd, stdin_text, *argv):
        return run_cli(self.repo, cwd, self.env, stdin_text, *argv)

    def assert_no_gcloud(self):
        """A refused operation must not touch credentials."""
        self.assertFalse(
            self.gcloud_log.exists() and self.gcloud_log.read_text().strip(),
            "gcloud was invoked during a refused operation")

    # --- the core mapping: bare slug -> REPO_ROOT/labs/<slug> -------
    def test_bare_slug_scaffolds_into_labs_from_repo_root(self):
        result = self.run_start(self.repo, "", "brand-new-lab", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "brand-new-lab"
        for n in range(1, 5):
            self.assertTrue((lab / f"task{n}.tf").is_file(), f"task{n}.tf")
        # the terraform template's static files all landed
        for name in ("README.md", "backend.tf", "providers.tf", "variables.tf"):
            self.assertTrue((lab / name).is_file(), name)
        self.assertTrue((lab / "terraform.tfvars").is_file())
        # no root-level lab: the old flat layout is not a fallback
        self.assertFalse((self.repo / "brand-new-lab").exists())
        # the preview names the resolved target
        self.assertIn("labs/brand-new-lab", result.stdout)

    def test_bare_slug_scaffolds_into_labs_from_outside_repo(self):
        result = self.run_start(self.scratch, "", "brand-new-lab", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "brand-new-lab"
        for n in range(1, 5):
            self.assertTrue((lab / f"task{n}.tf").is_file(), f"task{n}.tf")
        # the caller's directory stays clean: lab lookup is not cwd-based
        self.assertFalse((self.scratch / "brand-new-lab").exists())
        # preview + next steps must reference the resolved target so
        # the printed cd works from this invocation directory
        self.assertIn("labs/brand-new-lab", result.stdout)

    # --- prompted names and the collision validator use labs/ ------
    def test_new_name_prompt_maps_collisions_to_labs(self):
        managed = build_lab_dir(self.repo / "labs", "existing-lab")
        before = snapshot(managed)
        # stdin, prompt order: [u]se/[n]ew -> "n"; URL -> skip; the
        # fresh-name prompt must refuse the existing managed name, then
        # accept a new one; Proceed -> "y"
        result = self.run_start(self.scratch, "n\n\nexisting-lab\nfresh-lab\ny\n",
                                "existing-lab", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, snapshot(managed))  # untouched
        fresh = self.repo / "labs" / "fresh-lab"
        self.assertTrue((fresh / "task1.tf").is_file())
        self.assertFalse((self.scratch / "existing-lab").exists())
        self.assertFalse((self.scratch / "fresh-lab").exists())

    # --- --use-existing never scaffolds a missing positional -----------
    def test_use_existing_missing_lab_fails(self):
        # a missing target + --use-existing must fail, not scaffold a
        # replacement after a typo
        result = self.run_start(self.scratch, "", "ghost-lab",
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.scratch / "ghost-lab").exists())
        self.assertFalse((self.repo / "labs" / "ghost-lab").exists())

    # --- the old root is not a lab target anymore --------------------
    def test_old_root_lab_refused_for_refresh(self):
        legacy = build_lab_dir(self.repo, "legacy-lab")  # old flat layout
        before = snapshot(legacy)
        result = self.run_start(self.repo, "", "legacy-lab",
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, snapshot(legacy))
        self.assert_no_gcloud()

    def test_absolute_old_root_path_refused(self):
        legacy = build_lab_dir(self.repo, "legacy-lab")
        before = snapshot(legacy)
        result = self.run_start(self.scratch, "", str(legacy),
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, snapshot(legacy))
        self.assert_no_gcloud()

    # --- hostile targets: traversal, nesting, symlinks, files --------
    def test_traversal_target_refused(self):
        result = self.run_start(self.scratch, "", "../sneaky-lab",
                                "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.workdir / "sneaky-lab").exists())
        self.assertFalse((self.repo / "labs" / "sneaky-lab").exists())

    def test_new_lab_inside_existing_lab_refused(self):
        managed = build_lab_dir(self.repo / "labs", "existing-lab")
        result = self.run_start(self.repo, "", "labs/existing-lab/nested-lab",
                                "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((managed / "nested-lab").exists())

    def test_symlink_escape_refused(self):
        victim = build_lab_dir(self.workdir, "victim-lab")
        link = self.scratch / "escape-lab"
        link.symlink_to(victim)
        before = snapshot(victim)
        result = self.run_start(self.scratch, "", "escape-lab",
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, snapshot(victim))
        self.assert_no_gcloud()

    def test_file_target_refused(self):
        notes = self.scratch / "notes.txt"
        notes.write_text("not a lab\n")
        result = self.run_start(self.scratch, "", "notes.txt",
                                "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(notes.read_text(), "not a lab\n")

    # --- explicit paths resolve to the managed lab -------------------
    def test_explicit_relative_path_scaffolds_new_managed_lab(self):
        # from the repo root, labs/<slug> is a valid direct managed
        # target for a new lab
        result = self.run_start(self.repo, "", "labs/path-lab", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "path-lab"
        self.assertTrue((lab / "task1.tf").is_file())
        self.assertTrue((lab / "terraform.tfvars").is_file())
        # no root-level twin: the old flat layout is not a fallback
        self.assertFalse((self.repo / "path-lab").exists())

    def test_explicit_absolute_path_scaffolds_new_managed_lab(self):
        # from outside the repository, an absolute managed path is a
        # valid target for a new lab
        target = self.repo / "labs" / "abs-lab"
        result = self.run_start(self.scratch, "", str(target),
                                "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((target / "task1.tf").is_file())
        self.assertFalse((self.scratch / "abs-lab").exists())

    def test_explicit_absolute_path_refreshes_managed_lab(self):
        # an absolute explicit path to an existing managed lab is a
        # valid refresh target from outside the repository
        managed = build_lab_dir(self.repo / "labs", "existing-lab")
        before = snapshot(managed)
        result = self.run_start(self.scratch, "", str(managed),
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LAB RESET COMPLETE", result.stdout)
        self.assertFalse((managed / "terraform.tfstate").exists())
        self.assertIn(NEW_PROJECT, (managed / "terraform.tfvars").read_text())
        # the hard rule: user work byte-identical through the refresh
        for name in ("task1.tf", "task2.tf", "task3.tf", "task4.tf",
                     "shared-vpc/main.tf"):
            self.assertEqual(before[name], (managed / name).read_bytes())
        self.assertFalse((self.scratch / "existing-lab").exists())

    # --- prompted names with no positional -----------------------------
    def test_prompted_name_without_positional_maps_to_labs(self):
        # stdin: skip the URL prompt, name the lab, proceed
        result = self.run_start(self.scratch, "\nprompted-lab\ny\n", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "prompted-lab"
        self.assertTrue((lab / "task1.tf").is_file())
        # the caller's directory is never the lab root
        self.assertFalse((self.scratch / "prompted-lab").exists())

    def test_page_derived_default_runs_collision_validation(self):
        # the page derives slug "managed-name-test", which already
        # exists as a managed lab: accepting that default - at the
        # directory prompt AND again at the fresh-name prompt after
        # choosing NEW - must be refused, never silently targeted.
        # Defaults go through the same collision validation as typed
        # answers; ask() may not bypass the validator for defaults.
        managed = build_lab_dir(self.repo / "labs", "managed-name-test")
        before = snapshot(managed)
        page = self.scratch / "page.html"
        page.write_text(
            "<html><title>Managed Name Test - Course | Site</title></html>")
        # stdin: "" accepts the colliding default (-> use-or-new),
        # "n" chooses a new name, "" accepts the default AGAIN at the
        # fresh-name prompt (must re-prompt, not proceed), then
        # "fresh-lab" and "y" proceed
        result = self.run_start(self.scratch, "\nn\n\nfresh-lab\ny\n",
                                "--url", page.as_uri(), *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, snapshot(managed))  # untouched
        self.assertTrue((self.repo / "labs" / "fresh-lab" / "task1.tf").is_file())
        self.assertFalse((self.scratch / "managed-name-test").exists())
        self.assertFalse((self.scratch / "fresh-lab").exists())

    # --- the labs/ collection root itself is guarded --------------------
    def test_managed_symlink_escape_refused(self):
        # labs/hijack-lab is a symlink pointing outside the repo: the
        # bare slug must be refused before any fetch/reset/write, in
        # refresh mode and in unattended-new mode alike
        victim = build_lab_dir(self.workdir, "victim-lab")
        link = self.repo / "labs" / "hijack-lab"
        before = snapshot(victim)
        for mode, flags in (("refresh", ("--use-existing", "--yes")),
                            ("unattended-new", ("--yes",))):
            with self.subTest(mode=mode):
                if link.is_symlink() or link.exists():
                    link.unlink()
                link.symlink_to(victim)
                # a previous subTest must never contaminate the next
                shutil.rmtree(self.repo / "hijack-lab", ignore_errors=True)
                result = self.run_start(self.repo, "", "hijack-lab",
                                        *flags, *FULL_FLAGS)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(before, snapshot(victim), mode)
                self.assertTrue(link.is_symlink())
                self.assertFalse((self.repo / "hijack-lab").exists())
                self.assert_no_gcloud()

    def test_labs_root_symlink_escape_refused(self):
        # labs/ itself replaced by a symlink: no new lab may be
        # created through it
        victim_root = self.workdir / "victim-tree"
        hostage = victim_root / "hostage-lab"
        hostage.mkdir(parents=True)
        (hostage / "terraform.tfstate").write_text("{}")
        before = snapshot(victim_root)
        shutil.rmtree(self.repo / "labs")
        (self.repo / "labs").symlink_to(victim_root)
        result = self.run_start(self.scratch, "", "fresh-lab",
                                "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, snapshot(victim_root))
        self.assertFalse((victim_root / "fresh-lab").exists())
        self.assertTrue((self.repo / "labs").is_symlink())
        self.assert_no_gcloud()

    # --- traversal that normalizes inside labs --------------------------
    def test_dotdot_component_target_refused(self):
        # a literal '..' component never names a lab target, even
        # when normalization lands on a valid managed lab
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        (self.repo / "labs" / "detour").mkdir()  # the pivot must exist
        before = snapshot(victim)
        # refresh mode: the reset must not run through the traversal
        result = self.run_start(self.repo, "", "labs/detour/../victim-lab",
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, snapshot(victim),
                         "refresh ran through a '..' traversal")
        self.assert_no_gcloud()
        # unattended-new mode: no ghost lab may be scaffolded through it
        result = self.run_start(self.repo, "", "labs/detour/../ghost-lab",
                                "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / "labs" / "ghost-lab").exists())
        # and from inside labs/, '../labs/<slug>' may not normalize in
        result = self.run_start(self.repo / "labs", "", "../labs/ghost-lab",
                                "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / "labs" / "ghost-lab").exists())

    def test_dot_component_explicit_path_still_scaffolds(self):
        # '.' components are benign and stay valid: labs/./<slug>
        # still names the managed lab for a new scaffold
        result = self.run_start(self.repo, "", "labs/./dot-lab",
                                "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.repo / "labs" / "dot-lab" / "task1.tf").is_file())

    # --- aliases that resolve onto a managed lab are refused ----------
    def test_root_alias_target_refused_for_refresh(self):
        # repo/legacy-alias -> labs/victim-lab: the positional
        # resolves onto an existing managed lab, so it looks like a
        # refresh target - but it does not NAME the lab. Refused
        # before fetch, reset, or any write; the victim stays
        # byte-identical and no credential is touched.
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        (self.repo / "legacy-alias").symlink_to(victim)
        before = snapshot(victim)
        result = self.run_start(self.repo, "", "./legacy-alias",
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, snapshot(victim),
                         "refresh ran through a root-level alias")
        self.assert_no_gcloud()

    def test_nested_alias_target_refused(self):
        # labs/other-lab/alias -> labs/victim-lab: refused in refresh
        # mode AND in unattended-new mode (which would otherwise
        # reset the victim through the use-or-new prompt)
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = self.repo / "labs" / "other-lab"
        other.mkdir()
        (other / "alias").symlink_to(victim)
        before = snapshot(victim)
        for mode, flags in (("refresh", ("--use-existing", "--yes")),
                           ("unattended-new", ("--yes",))):
            with self.subTest(mode=mode):
                result = self.run_start(self.repo, "", "labs/other-lab/alias",
                                        *flags, *FULL_FLAGS)
                self.assertNotEqual(result.returncode, 0, mode)
                self.assertEqual(before, snapshot(victim), mode)
        self.assert_no_gcloud()

    def test_nested_labs_alias_path_refused_for_refresh_and_new(self):
        # labs/other-lab/labs -> labs/: the path's suffix is a clean
        # labs/<slug> pair and it resolves onto the victim, but the
        # detour through another lab sits INSIDE the repo boundary.
        # The whole supplied path below the repo root must be exactly
        # labs/<slug>: refresh must not reset the victim through the
        # alias, and unattended-new must not scaffold anything.
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = build_lab_dir(self.repo / "labs", "other-lab")
        (other / "labs").symlink_to(self.repo / "labs")
        before_victim = snapshot(victim)
        before_other = snapshot_nofollow(other)
        result = self.run_start(self.repo, "", "labs/other-lab/labs/victim-lab",
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before_victim, snapshot(victim),
                         "refresh reset the victim through a nested labs alias")
        self.assertEqual(before_other, snapshot_nofollow(other),
                         "a refused refresh changed the sibling lab")
        self.assert_no_gcloud()
        # unattended-new: no partial new lab anywhere
        result = self.run_start(self.repo, "", "labs/other-lab/labs/ghost-lab",
                                "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            sorted(p.name for p in (self.repo / "labs").iterdir()),
            [".gitkeep", "other-lab", "victim-lab"],
            "a refused create left a partial new lab under labs/")
        self.assertEqual(before_other, snapshot_nofollow(other),
                         "a refused create changed the sibling lab")
        self.assert_no_gcloud()

    def test_nested_repo_alias_path_refused_for_refresh_and_new(self):
        # labs/other-lab/repo-alias -> the repo root, then a clean
        # labs/<slug> suffix: the first entry into the repo is the
        # boundary, and below it only labs/<slug> itself is accepted.
        # Same refusals: no reset through the alias, no partial
        # scaffold, sibling and victim byte-identical.
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = build_lab_dir(self.repo / "labs", "other-lab")
        (other / "repo-alias").symlink_to(self.repo)
        before_victim = snapshot(victim)
        before_other = snapshot_nofollow(other)
        result = self.run_start(
            self.repo, "", "labs/other-lab/repo-alias/labs/victim-lab",
            "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before_victim, snapshot(victim),
                         "refresh reset the victim through a nested repo alias")
        self.assertEqual(before_other, snapshot_nofollow(other),
                         "a refused refresh changed the sibling lab")
        self.assert_no_gcloud()
        result = self.run_start(
            self.repo, "", "labs/other-lab/repo-alias/labs/ghost-lab",
            "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            sorted(p.name for p in (self.repo / "labs").iterdir()),
            [".gitkeep", "other-lab", "victim-lab"],
            "a refused create left a partial new lab under labs/")
        self.assertEqual(before_other, snapshot_nofollow(other),
                         "a refused create changed the sibling lab")
        self.assert_no_gcloud()

    # --- case-equivalent root spelling cannot launder a detour -------
    def test_case_variant_root_detour_refused_for_refresh_and_new(self):
        # On a case-insensitive filesystem, <workdir>/REPO IS the repo
        # root: same file, different string, and Path.resolve() keeps
        # the caller's spelling. The detour enters the repo through
        # that case-variant root, walks another lab, and returns to
        # the canonical spelling via repo-alias - so a STRING boundary
        # walk mistakes the alias for the first entry and accepts the
        # clean labs/<slug> suffix. Refresh must not reset the victim
        # through it, unattended-new must not scaffold anything, the
        # sibling stays byte-identical, and no gcloud call happens.
        if not same_file_probe(self.repo, self.repo.name.upper()):
            self.skipTest(
                "filesystem treats case spellings as distinct files")
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = build_lab_dir(self.repo / "labs", "other-lab")
        (other / "repo-alias").symlink_to(self.repo)
        detour = (self.repo.parent / self.repo.name.upper() / "labs"
                  / "other-lab" / "repo-alias" / "labs" / "victim-lab")
        before_victim = snapshot(victim)
        before_other = snapshot_nofollow(other)
        inside = self.workdir / "inside-alias"
        inside.symlink_to(self.repo.parent / self.repo.name.upper()
                          / "labs" / "other-lab")
        for path in (detour, inside / "repo-alias" / "labs" / "victim-lab"):
            for refresh in (False, True):
                with self.subTest(path=path, refresh=refresh):
                    target = path if refresh else path.parent / "ghost-lab"
                    flags = ("--use-existing",) if refresh else ()
                    result = self.run_start(self.repo, "", str(target),
                                            *flags, "--yes", *FULL_FLAGS)
                    self.assertNotEqual(result.returncode, 0,
                                        result.stdout + result.stderr)
                    self.assertEqual(before_victim, snapshot(victim))
                    self.assertEqual(before_other, snapshot_nofollow(other))
                    self.assertEqual(
                        sorted(p.name for p in (self.repo / "labs").iterdir()),
                        [".gitkeep", "other-lab", "victim-lab"],
                        "a refused operation left a partial new lab")
                    self.assert_no_gcloud()

    # --- --use-existing must name an existing lab, however chosen --------
    def test_use_existing_missing_prompted_name_fails(self):
        # no positional; the typed name is missing and --use-existing
        # is set: must fail loudly, never scaffold the replacement.
        # stdin: "" skips the URL prompt, "ghost-lab" is the name.
        result = self.run_start(self.scratch, "\nghost-lab\n",
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / "labs" / "ghost-lab").exists(),
                         "--use-existing scaffolded a missing prompted target")
        self.assertFalse((self.scratch / "ghost-lab").exists())
        self.assert_no_gcloud()

    def test_use_existing_missing_page_default_fails(self):
        # the page derives the slug; --use-existing plus accepting
        # that default must fail on a missing target, not scaffold it
        page = self.scratch / "page.html"
        page.write_text(
            "<html><title>Ghost Default Lab - Course | Site</title></html>")
        result = self.run_start(self.scratch, "",
                                "--url", page.as_uri(),
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / "labs" / "ghost-default-lab").exists(),
                         "--use-existing scaffolded a missing page-derived "
                         "target")
        self.assertFalse((self.scratch / "ghost-default-lab").exists())
        self.assert_no_gcloud()

    # --- generated outputs may not be symlinks ---------------------------
    def test_symlinked_generated_output_refused_for_refresh(self):
        # labs/escape-lab/README.md -> a file outside the repo: the
        # refresh copies templates and rewrites README/tfvars in
        # place, and every one of those writes would follow the link
        # and overwrite the outside target. Refused before reset,
        # gcloud, or any write; the referent stays byte-identical and
        # the lab's state survives.
        lab = build_lab_dir(self.repo / "labs", "escape-lab")
        outside = self.workdir / "outside-note.md"
        outside.write_text("do not touch\n")
        (lab / "README.md").unlink()
        (lab / "README.md").symlink_to(outside)
        before = snapshot(lab)
        result = self.run_start(self.scratch, "", "escape-lab",
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outside.read_bytes(), b"do not touch\n",
                         "refresh wrote through a symlinked generated output")
        self.assertEqual(before, snapshot(lab),
                        "a refused refresh changed the lab")
        self.assertTrue((lab / "README.md").is_symlink())
        self.assert_no_gcloud()

    def test_dangling_symlinked_generated_output_refused(self):
        # a DANGLING symlink has exists() == False, so a naive write
        # "creates" the referent outside the lab. The preflight
        # refuses it too; the referent stays missing.
        for name in ("backend.tf", "terraform.tfvars"):
            with self.subTest(generated=name):
                # a distinct slug per subTest: today's unguarded run
                # leaves the first lab behind for the second
                lab = build_lab_dir(self.repo / "labs",
                                    "dangle-" + name.replace(".", "-"))
                referent = self.workdir / f"dangling-{name}"
                (lab / name).unlink()
                (lab / name).symlink_to(referent)
                before = snapshot(lab)
                slug = "dangle-" + name.replace(".", "-")
                result = self.run_start(self.scratch, "", slug,
                                        "--use-existing", "--yes", *FULL_FLAGS)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(referent.exists(),
                                 f"refresh created the referent of a "
                                 f"dangling {name} link")
                self.assertEqual(before, snapshot(lab))
                self.assert_no_gcloud()

    # --- generated outputs must be regular files -------------------------
    def test_directory_generated_output_refused_for_refresh(self):
        # labs/<slug>/backend.tf replaced by a DIRECTORY: copy2 treats
        # an existing destination directory as a folder to copy INTO,
        # so the refresh would write template bytes to
        # backend.tf/backend.tf - and a symlink nested there turns
        # that into an overwrite of an external file. Refused before
        # the reset, the fetch, or any write; state, tfvars, the
        # external bytes, and credentials all survive.
        lab = build_lab_dir(self.repo / "labs", "dir-lab")
        external = self.workdir / "outside-backend.tf"
        external.write_text("external bytes\n")
        (lab / "backend.tf").unlink()
        (lab / "backend.tf").mkdir()
        (lab / "backend.tf" / "backend.tf").symlink_to(external)
        before = snapshot(lab)
        result = self.run_start(self.scratch, "", "dir-lab",
                                "--use-existing", "--yes", *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(external.read_bytes(), b"external bytes\n",
                         "refresh copied template bytes through a "
                         "directory destination into an external file")
        self.assertEqual(before, snapshot(lab),
                         "a refused refresh changed the lab")
        self.assertTrue((lab / "terraform.tfstate").exists(),
                        "a refused refresh deleted lab state")
        self.assertTrue((lab / "terraform.tfvars").exists(),
                        "a refused refresh deleted lab tfvars")
        self.assert_no_gcloud()

    def test_plain_directory_generated_output_refused(self):
        # even with nothing nested inside it, a directory sitting at a
        # generated destination is a refusal, not a folder to copy
        # into: every existing generated destination must be a
        # regular file before the reset runs
        for name in ("README.md", "variables.tf", "terraform.tfvars"):
            with self.subTest(generated=name):
                lab = build_lab_dir(self.repo / "labs",
                                    "plain-dir-" + name.replace(".", "-"))
                (lab / name).unlink()
                (lab / name).mkdir()
                before = snapshot(lab)
                result = self.run_start(
                    self.scratch, "", "plain-dir-" + name.replace(".", "-"),
                    "--use-existing", "--yes", *FULL_FLAGS)
                self.assertNotEqual(result.returncode, 0, name)
                self.assertEqual(before, snapshot(lab), name)
                self.assertTrue((lab / "terraform.tfstate").exists(), name)
                self.assert_no_gcloud()

    # --- the preview names the resolved absolute target -------------------
    def test_preview_shows_resolved_absolute_target(self):
        # invoked from outside the repository, the preview must show
        # the resolved absolute target - a repo-relative display alone
        # does not meet the spec's "previews show the resolved target"
        result = self.run_start(self.scratch, "", "abs-preview-lab",
                                "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        resolved = str(self.repo / "labs" / "abs-preview-lab")
        dir_lines = [line for line in result.stdout.splitlines()
                     if line.lstrip().startswith("directory")]
        self.assertTrue(dir_lines, "no preview directory line was printed")
        self.assertTrue(any(resolved in line for line in dir_lines),
                        f"the preview directory line must name the resolved "
                        f"absolute target {resolved}: {dir_lines}")

    # --- next steps must name the real target ---------------------------
    def test_next_steps_cd_resolves_to_managed_target(self):
        # a repo path containing spaces, invoked from outside it: the
        # printed next-step cd must parse with shlex and resolve to
        # the managed lab, so copy/pasting it works in the caller's
        # shell - not just from the repo root
        spacey = self.workdir / "repo with spaces"
        spacey.mkdir()
        repo = make_repo(spacey)
        result = run_cli(repo, self.scratch, self.env, "",
                         "spacey-lab", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((repo / "labs" / "spacey-lab" / "task1.tf").is_file())
        cd_lines = [line.strip() for line in result.stdout.splitlines()
                    if line.strip().startswith("cd ")]
        self.assertEqual(len(cd_lines), 1, cd_lines)
        tokens = shlex.split(cd_lines[0])
        self.assertEqual(len(tokens), 2, cd_lines[0])
        printed = Path(tokens[1])
        if not printed.is_absolute():
            printed = self.scratch / printed
        self.assertEqual(printed.resolve(),
                         (repo / "labs" / "spacey-lab").resolve(),
                         "the printed cd must resolve to the managed target "
                         "from the invocation directory")


if __name__ == "__main__":
    unittest.main()
