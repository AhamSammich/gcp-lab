"""Docs contract tests pinning honest workflow claims (002 Phase 3).

The README is the public contract; these tests hold it to what the
tools actually implement, so documentation cannot advertise a CLI
mode that does not exist:

  * the CLI is standard-only: every new lab gets the standard
    skeleton - no selector, no alternate mode, no automatic
    detection - and the README may not promise that `lab-start`
    writes the lab-supplied-Terraform instructions
  * templates/new-terraform-lab/ is unwired: it exists for future
    internal plumbing and as manual reference only
  * the migration's extended-attribute behavior is stated as
    implemented: the run READS byte values into the preservation
    sidecar without writing any (a read failure aborts the run);
    RESTORE applies them and needs compatible attribute-writing
    support on the target platform - no silent loss either way

These read the real README.md and the real instructions template;
they must never touch labs/ or archives/.
"""

import re
import unittest

from pathlib import Path

from util import REPO_ROOT

README_TEXT = (REPO_ROOT / "README.md").read_text()
TEMPLATE_TEXT = (REPO_ROOT / "templates" / "new-terraform-lab" / "README.md").read_text()


class StandardOnlyDocsTests(unittest.TestCase):
    """The README must describe the CLI as it is: standard-only."""

    def test_cli_documented_as_always_standard(self):
        self.assertRegex(
            README_TEXT,
            r"`lab-start` always uses the standard skeleton",
            "the README must state that every new lab gets the standard "
            "skeleton - there is no alternate scaffolding workflow")

    def test_no_unimplemented_workflow_promise(self):
        # no claim that lab-start writes the lab-supplied-Terraform
        # instructions (or any non-standard scaffold): that CLI mode
        # does not exist
        self.assertNotRegex(README_TEXT, r"`lab-start` writes a README")
        self.assertNotRegex(
            README_TEXT, r"(?i)do(es)? not use the standard skeleton")

    def test_no_selector_or_autodetection_advertised(self):
        # a selector or auto-detection may only ever be mentioned as
        # absent - never as a way to invoke the helper
        for banned in ("template flag", "template selector",
                       "--template", "automatically detects",
                       "auto-detect"):
            self.assertNotIn(banned, README_TEXT, banned)

    def test_terraform_template_documented_as_unwired(self):
        # the instructions template ships, but the README must say it
        # has no CLI surface yet and why it is tracked at all
        self.assertRegex(README_TEXT, r"(?i)not wired to the CLI",
                         "the README must say the terraform "
                         "instructions template has no CLI surface yet")
        self.assertRegex(README_TEXT, r"(?i)future internal plumbing",
                         "the README must say why the unwired "
                         "template ships")
        self.assertRegex(README_TEXT, r"(?i)manual reference")


class XattrDocsTests(unittest.TestCase):
    """The README's xattr claims must match the run/restore split."""

    def test_run_documented_as_sidecar_read_not_write(self):
        # the migration only READS xattrs into the sidecar; writing
        # happens at restore, never during the run
        self.assertRegex(
            README_TEXT,
            r"(?i)reads every attribute(?:'s)? byte value into the "
            r"(?:preservation )?sidecar without writing")
        # the old combined claim - that a platform unable to WRITE an
        # attribute fails the migration - is gone
        self.assertNotRegex(README_TEXT, r"cannot read or write")

    def test_read_failure_aborts_migration_documented(self):
        self.assertRegex(
            README_TEXT,
            r"(?i)a platform that cannot read an attribute in the "
            r"required mode aborts the migration")

    def test_restore_documented_as_requiring_write_support(self):
        self.assertRegex(README_TEXT,
                         r"(?i)restore (?:then )?applies the recorded "
                         r"attributes")
        self.assertRegex(README_TEXT, r"(?i)compatible attribute-writing "
                         r"support")
        # and no silent loss at restore either
        self.assertRegex(README_TEXT, r"(?i)fails loudly there rather "
                         r"than dropping")


class InstructionsTemplateDocsTests(unittest.TestCase):
    """The instructions template stays manual - never CLI-generated."""

    def test_template_makes_no_cli_generation_claim(self):
        # the template may instruct a reader by hand; it must not
        # claim lab-start produced it or would produce it
        self.assertNotIn("lab-start", TEMPLATE_TEXT)

    def test_relative_lab_reset_from_inside_lab_reaches_the_helper(self):
        # the template tells the reader to run lab-reset from inside
        # their lab directory with no argument. Labs live under
        # labs/<slug> now, so that relative spelling must climb two
        # levels to reach the repository root's helper - a documented
        # command that does not resolve is a broken contract
        match = re.search(r"`((?:\.\./)+lab-reset)`", TEMPLATE_TEXT)
        self.assertIsNotNone(
            match, "the template must name the from-inside-lab "
            "lab-reset invocation as a relative path")
        invoked = Path(match.group(1))
        # resolve it the way a shell would, from inside a lab
        from_inside = (REPO_ROOT / "labs" / "example-lab" / invoked)
        self.assertEqual(from_inside.resolve(),
                         (REPO_ROOT / "lab-reset").resolve(),
                         "the template's relative lab-reset command must "
                         "reach the repository root's helper from inside "
                         "a lab directory")
        self.assertTrue((REPO_ROOT / "lab-reset").is_file())


if __name__ == "__main__":
    unittest.main()
