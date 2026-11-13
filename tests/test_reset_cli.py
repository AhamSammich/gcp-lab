"""CLI-11/2/3: labkit reset retains the managed-target and credential contract.

A bare slug resolves to the managed lab under labs/; an explicit path
(absolute or relative) must identify an individual managed lab; the
collection roots (the repository root, labs/, templates/, archives/,
and their ancestors), symlinked targets, and traversal targets are
refused BEFORE any file deletion or gcloud change. A refused
operation leaves the tree byte-identical with no gcloud
invocations, with or without --dry-run. A valid reset touches
exactly the named lab - siblings stay byte-identical.

CLI-3 lives here too: `reset --credentials-only` clears gcloud's
config and credentials without a lab, against logging gcloud stubs
that can name an account, fail a chosen step, or refuse to launch.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from util import (build_lab_dir, make_repo, run_reset, same_file_probe,
                  snapshot, snapshot_nofollow, stub_env)

ACCOUNT = "student@example.com"


def gcloud_stub(case: str = "", final: str = "exit 0") -> str:
    """A logging gcloud stub: records the call, probes its own stdin.

    `case` is a POSIX sh case body (matched against "$*", the command
    line gcloud was invoked with) and `final` the script's last line -
    the exit status for unmatched calls. The stdin probe tells DEVNULL
    (fd 0 is the /dev/null character device) from an inherited pipe,
    so the tests can pin the closed-stdin hang fix.
    """
    return (
        "#!/bin/sh\n"
        'echo "gcloud $*" >> "$GCLOUD_LOG"\n'
        "if [ -c /dev/fd/0 ]; then\n"
        '  echo "stdin-closed" >> "$GCLOUD_LOG"\n'
        "else\n"
        '  echo "stdin-open" >> "$GCLOUD_LOG"\n'
        "fi\n"
        'case "$*" in\n'
        + case +
        "esac\n"
        + final +
        "\n"
    )


# review R1's repro: the account lookup SUCCEEDS (rc 0) with empty
# stdout and only a diagnostic on stderr; a revoke, were one wrongly
# attempted, fails
STDERR_DIAGNOSTIC_STUB = (
    '  "config get-value account") '
    'printf "WARNING: diagnostic only\\n" >&2 ;;\n'
    '  "auth revoke"*) exit 1 ;;\n'
)


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
    def reset_matching_fixture(self):
        lab = build_lab_dir(self.repo / "labs", "my-lab")
        nested = lab / "modules" / "network"
        nested.mkdir(parents=True)
        matches = {
            "terraform.tfstate", "terraform.tfstate.backup", "terraform.tfvars",
            "modules/network/custom.tfstate", "modules/network/custom.tfstate.backup",
            "modules/network/inputs.tfvars", "modules/network/inputs.tfvars.json",
            "modules/network/.hidden.tfstate", "modules/network/.hidden.tfvars",
            "modules/network/notes.tfstateful", "modules/network/state.tfstate.txt",
            "modules/network/vars.tfvars.backup",
        }
        for rel in matches:
            (lab / rel).write_bytes(b"disposable state or inputs\n")
        for name in ("tfstate", "tfvars", "notes.tfvar", "terraform.tf"):
            # The accepted globs include suffixes, but not bare names or
            # incomplete extensions. Matching directories must remain too.
            (nested / name).write_bytes(b"nested fixture\n")
        for name in ("cache.tfstate", "cache.tfvars"):
            directory = nested / name
            directory.mkdir()
            (directory / "keep.txt").write_bytes(b"directory is user work\n")
        (lab / "empty").mkdir()
        return lab, matches

    def test_cli2_valid_dry_run_preserves_files_and_only_queries_gcloud(self):
        lab, matches = self.reset_matching_fixture()
        build_lab_dir(self.repo / "labs", "other-lab")
        before = snapshot_nofollow(self.repo)
        result = self.run_reset_cli(self.scratch, "my-lab", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(snapshot_nofollow(self.repo), before)
        self.assertEqual(self.gcloud_log.read_text().splitlines(),
                         ["gcloud config get-value account"])
        # The preview must actually inventory the nested deletion candidates.
        for rel in matches:
            self.assertIn(rel, result.stdout)

    def test_cli2_reset_recursively_removes_only_matching_files(self):
        lab, matches = self.reset_matching_fixture()
        sibling = build_lab_dir(self.repo / "labs", "other-lab")
        before_sibling = snapshot_nofollow(sibling)
        before = snapshot_nofollow(lab)
        self.assertTrue(matches <= before.keys())
        result = self.run_reset_cli(self.scratch, "my-lab")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(snapshot_nofollow(lab),
                         {rel: facts for rel, facts in before.items() if rel not in matches})
        self.assertEqual(snapshot_nofollow(sibling), before_sibling)

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
        # CLI-11/2: importing the shared workflow must not drop a
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


class CredentialsOnlyResetTests(unittest.TestCase):
    """CLI-3: `reset --credentials-only` - credentials, no lab.

    The finishing-workflow cleanup: unset gcloud's project/region/zone,
    revoke ADC, and revoke the active account confirmed by a
    successful lookup - without a lab, without touching any lab
    file, with every subprocess stdin closed. Independent steps run
    even after a failure, but any failed or unconfirmed step fails
    the run (exit 1) - the dry-run preview included; the ordinary
    reset stays best-effort.
    """

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
        # CACHE-1: reset keeps its log filename under the shared new root.
        self.reset_log = self.home / ".cache" / "labkit" / "lab-reset.log"

    def run_reset_cli(self, cwd, *argv):
        return run_reset(self.repo, cwd, self.env, *argv)

    def install_gcloud(self, script: str) -> None:
        gcloud = self.home / "bin" / "gcloud"
        gcloud.write_text(script)
        gcloud.chmod(0o755)

    def gcloud_calls(self) -> list:
        return [line for line in self.gcloud_log.read_text().splitlines()
                if line.startswith("gcloud ")]

    # --- CLI-3: a positional directory is rejected before anything ---
    def test_credentials_only_rejects_positional_before_any_effect(self):
        # --credentials-only aims at gcloud, never at a lab: a
        # positional directory is argparse's own refusal (exit 2,
        # usage on stderr) before the banner, the log, or any
        # gcloud call - dry-run or not, however the lab is named,
        # and including an EMPTY positional: "" is still a directory
        # argument the user supplied, not the absence of one
        build_lab_dir(self.repo / "labs", "my-lab")
        for target in ("my-lab", "labs/my-lab", ""):
            for extra in ((), ("--dry-run",)):
                with self.subTest(target=target, dry_run=bool(extra)):
                    result = self.run_reset_cli(self.scratch,
                                                "--credentials-only",
                                                target, *extra)
                    self.assertEqual(result.returncode, 2,
                                     result.stdout + result.stderr)
                    self.assertIn("usage:", result.stderr)
                    self.assertIn("--credentials-only", result.stderr)
                    # the flag is known: the directory is the problem,
                    # not an unrecognized option
                    self.assertNotIn("unrecognized", result.stderr)
                    # nothing ran: no output, no log, no gcloud call
                    self.assertEqual(result.stdout, "")
                    self.assertFalse(self.reset_log.exists(),
                                     "a rejected run wrote the reset log")
                    self.assertFalse(self.gcloud_log.exists()
                                     and self.gcloud_log.read_text().strip())

    # --- CLI-3: the cleanup itself, no lab involved ------------------
    def test_credentials_only_clears_credentials_without_a_lab(self):
        # from outside the repository, with no lab anywhere in sight:
        # unset the config, revoke ADC, revoke the account a
        # successful lookup named - and every subprocess stdin closed
        (self.scratch / "terraform.tfstate").write_text("{}")
        self.install_gcloud(gcloud_stub(
            '  "config get-value account") echo "%s" ;;\n' % ACCOUNT))
        result = self.run_reset_cli(self.scratch, "--credentials-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.gcloud_calls()
        for cmd in ("config unset project",
                    "config unset compute/region",
                    "config unset compute/zone",
                    "auth application-default revoke --quiet",
                    "auth revoke %s --quiet" % ACCOUNT):
            self.assertIn("gcloud " + cmd, calls)
        # the account being revoked is never a mystery
        self.assertIn(ACCOUNT, result.stdout)
        # fd 0 was /dev/null on every call, never an open pipe - the
        # hang fix that retired lab-reset.sh
        self.assertNotIn("stdin-open", self.gcloud_log.read_text())
        # no lab was needed and no file was touched
        self.assertEqual((self.scratch / "terraform.tfstate").read_text(),
                         "{}")

    def test_credentials_only_touches_no_lab_files(self):
        # even with the cwd INSIDE a lab, --credentials-only deletes
        # nothing: the lab tree stays byte-identical while gcloud's
        # config and credentials are cleared
        lab = build_lab_dir(self.repo / "labs", "my-lab")
        before = snapshot(lab)
        self.install_gcloud(gcloud_stub(
            '  "config get-value account") echo "%s" ;;\n' % ACCOUNT))
        result = self.run_reset_cli(lab, "--credentials-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, snapshot(lab),
                        "credentials-only touched lab files")
        self.assertTrue((lab / "terraform.tfstate").exists())

    # --- CLI-3: missing gcloud fails the strict cleanup --------------
    def test_credentials_only_missing_gcloud_exits_1(self):
        # the strict cleanup must be a fact, not a hope: with no
        # gcloud to run, it fails loudly (exit 1) - the ordinary
        # reset's best-effort skip does not apply here
        (self.home / "bin" / "gcloud").unlink()
        result = self.run_reset_cli(self.scratch, "--credentials-only")
        self.assertEqual(result.returncode, 1)
        self.assertIn("gcloud", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.gcloud_log.exists()
                         and self.gcloud_log.read_text().strip())

    # --- CLI-3: the dry run queries, reports, and changes nothing ----
    def test_credentials_only_dry_run_queries_account_only(self):
        # the dry run is read-only: it asks gcloud who is active and
        # reports what it WOULD do - exactly one query, no unset, no
        # revoke, nothing revoked
        self.install_gcloud(gcloud_stub(
            '  "config get-value account") echo "%s" ;;\n' % ACCOUNT))
        result = self.run_reset_cli(self.scratch, "--credentials-only",
                                    "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.gcloud_calls(),
                         ["gcloud config get-value account"])
        self.assertIn(ACCOUNT, result.stdout)
        self.assertIn("would run", result.stdout)

    def test_credentials_only_dry_run_reports_missing_gcloud(self):
        # CLI-3: a preview of a run that cannot happen still reports
        # the missing gcloud - and the exit-1 contract covers the
        # preview too: the dry run exits 1 on the same condition the
        # real run would fail on
        (self.home / "bin" / "gcloud").unlink()
        result = self.run_reset_cli(self.scratch, "--credentials-only",
                                    "--dry-run")
        self.assertEqual(result.returncode, 1)
        self.assertIn("gcloud", result.stdout)
        self.assertFalse(self.gcloud_log.exists())

    def test_credentials_only_dry_run_reports_unconfirmed_account(self):
        # CLI-3: a failed lookup is reported as UNCONFIRMED, not as
        # "none active" - and, like the real run, the preview exits 1
        # on it
        self.install_gcloud(gcloud_stub(
            '  "config get-value account") exit 1 ;;\n'))
        result = self.run_reset_cli(self.scratch, "--credentials-only",
                                    "--dry-run")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unconfirmed", result.stdout)
        self.assertNotIn("no active account", result.stdout)

    def test_credentials_only_dry_run_with_clean_account_exits_0(self):
        # CLI-3 boundary: the dry run exits 0 when its two queries
        # both succeed - gcloud present, lookup confirmed - even
        # when the lookup confirms there is no active account; only
        # a missing gcloud or an unconfirmed lookup fails the preview
        result = self.run_reset_cli(self.scratch, "--credentials-only",
                                    "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.gcloud_calls(),
                         ["gcloud config get-value account"])
        self.assertIn("no active account", result.stdout)

    # --- CLI-3: an unconfirmed account is never revoked --------------
    def test_credentials_only_unconfirmed_account_is_never_revoked(self):
        # the lookup failing means NOTHING may be revoked - no
        # guessed, default, or placeholder account - and the run
        # exits 1 while the independent steps around it still run
        self.install_gcloud(gcloud_stub(
            '  "config get-value account") exit 1 ;;\n'))
        result = self.run_reset_cli(self.scratch, "--credentials-only")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("unconfirmed", result.stdout)
        calls = self.gcloud_calls()
        self.assertNotIn("gcloud auth revoke", " ".join(calls))
        self.assertIn("gcloud config unset project", calls)
        self.assertIn("gcloud auth application-default revoke --quiet",
                      calls)

    def test_credentials_only_unset_placeholder_is_not_an_account(self):
        # gcloud answers 'unset' for a property with no value; a
        # successful lookup that names no account is clean (nothing
        # to revoke, exit 0), and the placeholder itself is never
        # revoked as if it were an account
        for placeholder in ("unset", "(unset)"):
            with self.subTest(placeholder=placeholder):
                self.install_gcloud(gcloud_stub(
                    '  "config get-value account") echo "%s" ;;\n'
                    % placeholder))
                result = self.run_reset_cli(self.scratch,
                                            "--credentials-only")
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                self.assertNotIn("gcloud auth revoke",
                                 " ".join(self.gcloud_calls()))
                self.assertIn("no active account", result.stdout)

    # --- review R1: a stderr diagnostic is never an account ----------
    def test_credentials_only_never_takes_a_stderr_diagnostic_as_account(self):
        # review R1 / CLI-3: the lookup succeeds (rc 0) with EMPTY
        # stdout and only a diagnostic on stderr - that confirms NO
        # account. The diagnostic must never be mistaken for an
        # account name: nothing is revoked, and the strict run stays
        # green, because the credentials state it asked about is
        # clean, not broken
        self.install_gcloud(gcloud_stub(STDERR_DIAGNOSTIC_STUB))
        result = self.run_reset_cli(self.scratch, "--credentials-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("gcloud auth revoke",
                         " ".join(self.gcloud_calls()))
        self.assertIn("no active account", result.stdout)

    def test_credentials_only_dry_run_never_reports_stderr_diagnostic_as_account(self):
        # the preview side of review R1: the dry run reports the
        # confirmed empty account, never the stderr diagnostic as a
        # would-be account - and its single query proves the preview
        # changed nothing else
        self.install_gcloud(gcloud_stub(STDERR_DIAGNOSTIC_STUB))
        result = self.run_reset_cli(self.scratch, "--credentials-only",
                                    "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no active account", result.stdout)
        self.assertNotIn("diagnostic", result.stdout)
        self.assertEqual(self.gcloud_calls(),
                         ["gcloud config get-value account"])

    # --- CLI-3: independent steps continue, failures exit 1 ----------
    def test_credentials_only_continues_after_failed_adc(self):
        # a failed step fails the run, but the independent steps
        # still run: ADC revocation fails and the config is still
        # unset and the confirmed account still revoked
        self.install_gcloud(gcloud_stub(
            '  "auth application-default revoke --quiet") exit 1 ;;\n'
            '  "config get-value account") echo "%s" ;;\n' % ACCOUNT))
        result = self.run_reset_cli(self.scratch, "--credentials-only")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        calls = self.gcloud_calls()
        for cmd in ("config unset project",
                    "auth application-default revoke --quiet",
                    "auth revoke %s --quiet" % ACCOUNT):
            self.assertIn("gcloud " + cmd, calls)

    def test_credentials_only_failed_unset_exits_1(self):
        # a failed config unset is a failed step too: exit 1, with
        # all three properties still attempted and the ADC and
        # account steps still running after it
        self.install_gcloud(gcloud_stub(
            '  "config unset"*) exit 1 ;;\n'
            '  "config get-value account") echo "%s" ;;\n' % ACCOUNT))
        result = self.run_reset_cli(self.scratch, "--credentials-only")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        calls = self.gcloud_calls()
        for cmd in ("config unset project",
                    "config unset compute/region",
                    "config unset compute/zone",
                    "auth application-default revoke --quiet",
                    "auth revoke %s --quiet" % ACCOUNT):
            self.assertIn("gcloud " + cmd, calls)

    def test_credentials_only_failed_account_revoke_exits_1(self):
        # the confirmed account's revocation failing is a failed
        # step as well
        self.install_gcloud(gcloud_stub(
            '  "auth revoke"*) exit 1 ;;\n'
            '  "config get-value account") echo "%s" ;;\n' % ACCOUNT))
        result = self.run_reset_cli(self.scratch, "--credentials-only")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("gcloud auth revoke %s --quiet" % ACCOUNT,
                      self.gcloud_calls())

    def test_credentials_only_launch_failure_continues_without_crash(self):
        # a gcloud that cannot even launch (here: a broken
        # interpreter, found on PATH but unexecutable) is a failed
        # step, not a crash: every step is still attempted, nothing
        # is revoked on the unconfirmed lookup, and the run reports
        # and exits 1 instead of dying in a traceback
        self.install_gcloud("#!/no/such/interpreter\nexit 0\n")
        result = self.run_reset_cli(self.scratch, "--credentials-only")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        # the flow got past the first broken exec: the ADC step and
        # the account report both ran
        self.assertIn("application-default credentials", result.stdout)
        self.assertIn("unconfirmed", result.stdout)
        self.assertFalse(self.gcloud_log.exists())

    # --- CLI-3 boundary: the ordinary reset stays best-effort --------
    def test_ordinary_reset_stays_best_effort_when_gcloud_fails(self):
        # the strict exit-1 contract belongs ONLY to --credentials-
        # only: an ordinary reset against a gcloud whose every step
        # fails still completes best-effort (exit 0), cleaning the
        # lab's local state along the way
        lab = build_lab_dir(self.repo / "labs", "my-lab")
        self.install_gcloud(gcloud_stub(final="exit 1"))
        result = self.run_reset_cli(self.scratch, "my-lab")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((lab / "terraform.tfstate").exists())
        self.assertIn("gcloud auth application-default revoke --quiet",
                      self.gcloud_calls())


class StubbedToolsSafetyTests(unittest.TestCase):
    """Stubbed-tools safety: no real cloud tool can ever be reached.

    stub_env must do more than point PATH at the stub bin: the
    ambient environment's gcloud/Terraform credential and config
    overrides (CLOUDSDK_*, GOOGLE_*, TF_*) must be stripped too, or a
    tool that honors them could follow them straight to the owner's
    real credentials and config despite the stub.
    """

    def test_stub_env_strips_real_cloud_overrides(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name) / "home"
        gcloud_log = Path(tmp.name) / "gcloud.log"
        with mock.patch.dict(os.environ, {
            "CLOUDSDK_CONFIG": "/real-owner/config-gcloud",
            "CLOUDSDK_ACTIVE_CONFIG_NAME": "owners-config",
            "GOOGLE_APPLICATION_CREDENTIALS": "/real-owner/adc.json",
            "TF_CLI_CONFIG_FILE": "/real-owner/terraform.tfrc",
        }):
            env = stub_env(home, gcloud_log)
        for var in ("CLOUDSDK_CONFIG", "CLOUDSDK_ACTIVE_CONFIG_NAME",
                    "GOOGLE_APPLICATION_CREDENTIALS", "TF_CLI_CONFIG_FILE"):
            self.assertNotIn(var, env,
                             f"{var} leaked from the real environment")
        # and the hermetic settings are all the env carries for tools
        self.assertEqual(env["PATH"], str(home / "bin"))
        self.assertEqual(env["HOME"], str(home))
        self.assertEqual(env["GCLOUD_LOG"], str(gcloud_log))


if __name__ == "__main__":
    unittest.main()
