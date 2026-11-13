"""Lab type metadata (.labkit-type): recording, safety, and refusals.

AC10: the scaffold records the type it actually generates, and that
record is a managed generated output - it gets the same
unsafe-output safeguards as the template files, and malformed or
unsupported records fail before any mutation.

AC12: a refresh reuses the recorded type. A lab that records a type
refreshes as that type again, whatever its files look like; an
explicit --type conflicting with the recorded type (or with the
legacy fallback) is refused before the fetch, the reset, any write,
or auth. Legacy labs - a .tf workspace from before typed scaffolds,
including templates copied by hand - have no recorded type and
refresh as the fixed standard fallback: the fallback is shown in the
preview, standard is recorded only after the owner approves the
refresh, and no type is ever inferred from the file layout. A
directory with neither a record nor lab files is not a lab this
tool made: resetting it is refused, whatever way it was selected.

AC13: every refusal or cancellation leaves the whole lab tree
byte-identical - state, tfvars, tasks, absent metadata - and runs
neither the reset nor any initialization/authentication command.

Every run targets the disposable fixture repo with a stub gcloud and
a tempdir HOME; no real lab, cloud command, or credential is touched.
"""

import tempfile
import unittest
from pathlib import Path

from util import (FULL_FLAGS, build_lab_dir, make_repo, run_cli, snapshot,
                  snapshot_nofollow, stub_env)

SLUG = "landing-zone-challenge"
METADATA = ".labkit-type"
RECORDED = "terraform\n"

# every question answered except the skip flags: a refused run must
# never reach the init/auth phases, so those flags stay OFF and the
# evidence is that the phases neither printed nor invoked anything.
# No --type: the runs below exercise the recorded type and the
# legacy fallback, not flag selection.
RUN_FLAGS = ("--title", "Landing Zone Challenge",
             "--tasks", "4",
             "--project", "qwiklabs-gcp-01-newlab",
             "--zone", "us-central1-a")


class LabTypeMetadataTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)
        self.repo = make_repo(self.workdir)  # never the real checkout
        self.home = self.workdir / "home"
        self.gcloud_log = self.workdir / "gcloud.log"
        self.env = stub_env(self.home, self.gcloud_log)

    def run_start(self, stdin_text, *argv):
        # invoked from outside the repository, like the other suites
        return run_cli(self.repo, self.workdir, self.env, stdin_text, *argv)

    def legacy_lab(self, slug=SLUG):
        """A lab directory without type metadata: the legacy shape.

        Carries .tf files - the marker of a lab the original
        Terraform workflow created - so it is ELIGIBLE for the
        standard fallback. Its files still say nothing about type.
        """
        lab = build_lab_dir(self.repo / "labs", slug)
        (lab / METADATA).unlink()
        return lab

    def assert_no_gcloud(self):
        # the stub gcloud logs every invocation, the reset's
        # credential cleanup and the auth step alike
        self.assertFalse(self.gcloud_log.exists()
                         and self.gcloud_log.read_text().strip(),
                         "gcloud was invoked for a refused lab")

    def assert_refused_untouched(self, result, lab, before):
        """A refusal: nonzero exit, no reset/init/auth, tree identical."""
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertNotIn("LAB RESET", result.stdout)
        self.assertNotIn("LAB READY", result.stdout)
        self.assert_no_gcloud()
        self.assertEqual(before, snapshot(lab),
                         "a refused run changed the lab tree")

    def assert_records_standard_fallback(self, result, lab):
        """The approved legacy fallback: standard recorded, .tf work
        kept as non-generated files, reset's deletions visible."""
        self.assertEqual((lab / METADATA).read_text(), "standard\n")
        # the preview named the fallback honestly
        type_lines = [line for line in result.stdout.splitlines()
                      if line.strip().startswith("type")]
        self.assertTrue(type_lines, "no type preview line was printed")
        self.assertIn("standard", type_lines[0])
        self.assertIn("no recorded type", type_lines[0])
        # the reset ran with its normal effects, then standard was
        # recorded - the legacy .tf sources survive as user work
        self.assertIn("LAB RESET COMPLETE", result.stdout)
        self.assertFalse((lab / "terraform.tfstate").exists())
        self.assertFalse((lab / "terraform.tfvars").exists())

    # --- AC10: recording the type the lab actually gets -----------------
    def test_new_lab_records_the_type_it_actually_gets(self):
        # AC10: the scaffold writes plain type metadata naming what it
        # actually generates - here the selected terraform type - and
        # the new-lab preview names the file among what it creates
        result = self.run_start("", "recorded-lab", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "recorded-lab"
        self.assertEqual((lab / METADATA).read_text(), RECORDED)
        creates = [line for line in result.stdout.splitlines()
                   if "creates" in line]
        self.assertTrue(creates, "no creates preview line was printed")
        self.assertIn(METADATA, creates[0])

    def test_refresh_treats_metadata_as_generated_output(self):
        # AC10/AC12: the preview is accurate about the record: it is
        # named among the files a refresh rewrites, not among the kept
        # student work, and the rewrite preserves the recorded type
        lab = build_lab_dir(self.repo / "labs", SLUG)
        result = self.run_start("", SLUG, "--use-existing", "--yes",
                                *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rewrites = [line for line in result.stdout.splitlines()
                    if "rewrites" in line]
        keeps = [line for line in result.stdout.splitlines()
                 if "keeps" in line]
        self.assertTrue(rewrites, "no rewrites preview line was printed")
        self.assertIn(METADATA, rewrites[0])
        self.assertTrue(keeps, "no keeps preview line was printed")
        self.assertNotIn(METADATA, keeps[0])
        self.assertEqual((lab / METADATA).read_text(), RECORDED)

    # --- AC10: the record carries the output safeguards ------------------
    def test_symlinked_metadata_refused_referent_untouched(self):
        # AC10: the record gets the same unsafe-output safeguard as
        # the other generated files: a symlink (this one aimed at a
        # file outside the lab) is refused before fetch/reset/writes/
        # auth, and the referent stays byte-identical
        lab = build_lab_dir(self.repo / "labs", SLUG)
        outside = self.workdir / "outside-type.txt"
        outside.write_text("do not touch\n")
        (lab / METADATA).unlink()
        (lab / METADATA).symlink_to(outside)
        before = snapshot_nofollow(lab)
        result = self.run_start("", SLUG, "--use-existing", "--yes",
                                *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertEqual(outside.read_bytes(), b"do not touch\n",
                         "the refusal wrote through the metadata symlink")
        self.assertEqual(before, snapshot_nofollow(lab))
        self.assert_no_gcloud()

    def test_dangling_metadata_symlink_refused_referent_stays_missing(self):
        # AC10: a DANGLING metadata symlink (exists() is False) must
        # not become a write that creates the referent: refused, and
        # the outside path stays missing
        lab = build_lab_dir(self.repo / "labs", SLUG)
        referent = self.workdir / "never-created.txt"
        (lab / METADATA).unlink()
        (lab / METADATA).symlink_to(referent)
        before = snapshot_nofollow(lab)
        result = self.run_start("", SLUG, "--use-existing", "--yes",
                                *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertFalse(referent.exists(),
                         "the refusal created a dangling link's referent")
        self.assertEqual(before, snapshot_nofollow(lab))
        self.assert_no_gcloud()

    def test_directory_at_metadata_path_refused_before_reset(self):
        # AC10: a generated destination that is a directory is a
        # pre-reset refusal, exactly like backend.tf - nothing is
        # written into it and the lab's state survives
        lab = build_lab_dir(self.repo / "labs", SLUG)
        (lab / METADATA).unlink()
        (lab / METADATA).mkdir()
        before = snapshot_nofollow(lab)
        result = self.run_start("", SLUG, "--use-existing", "--yes",
                                *FULL_FLAGS)
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertEqual(list((lab / METADATA).iterdir()), [],
                         "the refusal wrote into the metadata directory")
        self.assertTrue((lab / "terraform.tfstate").exists(),
                        "a refused run reset the lab's state")
        self.assertEqual(before, snapshot_nofollow(lab))
        self.assert_no_gcloud()

    def test_malformed_or_unsupported_metadata_refused_byte_identical(self):
        # AC10/AC12: a record that is not exactly one known type
        # identifier fails before any mutation, and is never
        # repaired, rewritten, or silently treated as absent - the
        # standard fallback is for MISSING records only
        cases = ("", "garbage\n", "puppet\n", "terraform extra\n",
                 "Terraform\n", "terraform\ncli\n")
        for index, content in enumerate(cases):
            with self.subTest(metadata=content):
                slug = f"malformed-{index}"
                lab = build_lab_dir(self.repo / "labs", slug)
                (lab / METADATA).write_text(content)
                before = snapshot(lab)
                result = self.run_start("", slug, "--use-existing",
                                        "--yes", *FULL_FLAGS)
                self.assertNotEqual(result.returncode, 0,
                                    result.stdout + result.stderr)
                self.assertIn("not a known lab type", result.stderr)
                self.assertEqual(before, snapshot(lab))
                self.assertEqual((lab / METADATA).read_text(), content,
                                 "the refusal repaired the record")
                self.assert_no_gcloud()

    # --- AC12: legacy labs refresh as the fixed standard fallback --------
    def test_legacy_lab_refreshes_as_standard(self):
        # AC12: an existing .tf lab with no recorded type is the
        # eligible legacy shape: it refreshes as standard (no type
        # prompt, no file-based inference), the fallback is shown in
        # the preview, standard is recorded once the refresh runs, and
        # every legacy .tf source survives as non-generated work
        lab = self.legacy_lab()
        before = snapshot(lab)
        result = self.run_start("", SLUG, "--use-existing", "--yes",
                                *RUN_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_records_standard_fallback(result, lab)
        for name in ("task1.tf", "task2.tf", "task3.tf", "task4.tf",
                     "backend.tf", "providers.tf", "variables.tf",
                     "shared-vpc/main.tf"):
            self.assertEqual((lab / name).read_bytes(), before[name], name)
        # the standard stubs were generated for the missing numbers
        for n in range(1, 5):
            self.assertTrue((lab / f"task{n}.md").is_file(), f"task{n}.md")

    def test_legacy_lab_refresh_interactive_use_choice(self):
        # AC12: the interactive [u]se choice on a legacy lab takes the
        # standard fallback the same way (stdin "" accepts the use
        # default, then the exhausted stdin accepts the confirm)
        lab = self.legacy_lab()
        result = self.run_start("\n", SLUG, *RUN_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_records_standard_fallback(result, lab)

    def test_legacy_lab_refresh_prompted_name(self):
        # AC12: a typed-in directory name is as much a target as a
        # positional: naming a legacy lab at the prompt and accepting
        # the use default refreshes it as standard
        lab = self.legacy_lab("prompted-legacy")
        result = self.run_start("\nprompted-legacy\n", *RUN_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_records_standard_fallback(result, lab)

    def test_legacy_lab_refresh_page_derived_default(self):
        # AC12: a page-derived directory default naming a legacy lab
        # refreshes it as standard too - a derived default is a target
        # selection
        lab = self.legacy_lab("typed-legacy-lab")
        page = self.workdir / "page.html"
        page.write_text("<html><title>Typed Legacy Lab - Course | Site"
                        "</title></html>")
        # stdin: "" accepts the derived default, "" accepts use, the
        # exhausted stdin accepts the confirm
        result = self.run_start("\n\n", "--url", page.as_uri(), *RUN_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_records_standard_fallback(result, lab)

    def test_legacy_refresh_declined_writes_nothing(self):
        # AC12/AC13: the fallback is recorded only after approval - a
        # declined confirm leaves the whole lab tree byte-identical,
        # state and tfvars included, with no metadata written and no
        # reset, init, or gcloud anywhere
        lab = self.legacy_lab()
        before = snapshot(lab)
        # stdin: "" skips the URL prompt, "n" declines the confirm
        result = self.run_start("\nn\n", SLUG, "--use-existing", *RUN_FLAGS)
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertNotIn("LAB RESET", result.stdout)
        self.assertEqual(before, snapshot(lab),
                         "a declined refresh changed the lab tree")
        self.assertFalse((lab / METADATA).exists(),
                         "a declined refresh recorded a type")
        self.assert_no_gcloud()

    def test_hand_copied_template_tree_refreshes_as_standard(self):
        # AC12 (supersedes the earlier hand-copied refusal, owner
        # decision): a template tree copied by hand carries no
        # recorded type, so it is the eligible legacy shape too - the
        # standard fallback refreshes it, keeping every copied file
        # as non-generated work
        hand = self.repo / "labs" / "hand-copied"
        hand.mkdir()
        for name in ("README.md", "backend.tf", "providers.tf",
                     "variables.tf", "task1.tf"):
            (hand / name).write_text(f"# hand-copied {name}\n")
        before = snapshot(hand)
        result = self.run_start("", "hand-copied", "--use-existing",
                                "--yes", *RUN_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_records_standard_fallback(result, hand)
        for name in ("backend.tf", "providers.tf", "variables.tf",
                     "task1.tf"):
            self.assertEqual((hand / name).read_bytes(), before[name], name)

    # --- AC12: --type cannot fight the recorded type or the fallback -----
    def test_legacy_lab_conflicting_type_flag_refused(self):
        # AC12: an explicit --type must not override the standard
        # fallback for a legacy lab - a conflicting flag is refused
        # before the fetch, the reset, any write, or auth, and the
        # fallback is not recorded
        for flag_type in ("terraform", "cli"):
            with self.subTest(**{"type": flag_type}):
                lab = self.legacy_lab(f"conflict-{flag_type}")
                before = snapshot(lab)
                result = self.run_start(
                    "", f"conflict-{flag_type}", "--use-existing", "--yes",
                    "--type", flag_type, *RUN_FLAGS)
                self.assert_refused_untouched(result, lab, before)
                self.assertFalse((lab / METADATA).exists(),
                                 "a refused run recorded a type")

    def test_legacy_lab_explicit_standard_type_allowed(self):
        # AC12: --type standard AGREES with the fallback, so it is
        # accepted - the refresh proceeds and records standard
        lab = self.legacy_lab()
        result = self.run_start("", SLUG, "--use-existing", "--yes",
                                "--type", "standard", *RUN_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_records_standard_fallback(result, lab)

    def test_recorded_lab_conflicting_type_flag_refused(self):
        # AC12: switching an existing lab between types is not
        # supported - a recorded lab is refused when --type names a
        # different type, before any reset or write, and the record
        # keeps the type it had
        cases = (("terraform", "cli"), ("standard", "terraform"))
        for recorded, flag_type in cases:
            with self.subTest(recorded=recorded, **{"type": flag_type}):
                slug = f"typed-{recorded}"
                lab = build_lab_dir(self.repo / "labs", slug)
                (lab / METADATA).write_text(recorded + "\n")
                before = snapshot(lab)
                result = self.run_start(
                    "", slug, "--use-existing", "--yes",
                    "--type", flag_type, *RUN_FLAGS)
                self.assert_refused_untouched(result, lab, before)
                self.assertEqual((lab / METADATA).read_text(),
                                 recorded + "\n")

    def test_arbitrary_untyped_directory_still_refused(self):
        # AC12 (retained original safeguard): missing metadata alone
        # is not evidence that a directory is a lab - a notes-only
        # directory with no .tf files is refused untouched, never
        # reset, never adopted, whatever way it was selected
        not_a_lab = self.repo / "labs" / "random-dir"
        not_a_lab.mkdir()
        (not_a_lab / "notes.txt").write_text("not a lab\n")
        before = snapshot(not_a_lab)
        for mode, argv in (
            ("flag", ("random-dir", "--use-existing", "--yes", *RUN_FLAGS)),
            ("interactive", ("random-dir", *RUN_FLAGS)),
        ):
            with self.subTest(mode=mode):
                stdin = {"flag": "", "interactive": "u\n"}[mode]
                result = self.run_start(stdin, *argv)
                self.assert_refused_untouched(result, not_a_lab, before)
                self.assertIn("no .tf files", result.stderr)
                self.assertFalse((not_a_lab / METADATA).exists(),
                                 "the refusal adopted the directory")

    # --- AC12: a typed lab refreshes through the managed flow ------------
    def test_typed_lab_refresh_preserves_record_and_task_work(self):
        # AC12: a lab with a valid record refreshes with no type
        # prompt (every question is answered by flags or --yes, so
        # any new required prompt would fail the run), the record is
        # regenerated with the same type, task files stay
        # byte-identical, and stale state is cleared by the reset's
        # accepted semantics
        lab = build_lab_dir(self.repo / "labs", SLUG)
        tasks_before = {n: (lab / f"task{n}.tf").read_bytes()
                        for n in range(1, 5)}
        result = self.run_start("", SLUG, "--use-existing", "--yes",
                                *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LAB RESET COMPLETE", result.stdout)
        self.assertEqual((lab / METADATA).read_text(), RECORDED)
        # the record is generated output: it is rewritten, not listed
        # among the student work the refresh left untouched
        summary = result.stdout.split("Left untouched", 1)
        self.assertEqual(len(summary), 2, "no Left untouched summary")
        self.assertNotIn(METADATA, summary[1])
        for n, content in tasks_before.items():
            self.assertEqual((lab / f"task{n}.tf").read_bytes(), content,
                             f"task{n}.tf must survive the refresh")
        self.assertFalse((lab / "terraform.tfstate").exists())
        self.assertIn("qwiklabs-gcp-01-newlab",
                      (lab / "terraform.tfvars").read_text())


if __name__ == "__main__":
    unittest.main()
