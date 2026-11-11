"""CLI-9/CLI-11: runnable command paths and migration retirement.

The README and the instructions template carry explanatory prose;
that wording is manual-review material and is deliberately not
frozen by tests. These hold only what a reader must be able to run
or rely on:

  * CLI-9: the retired one-time migration executable is gone and
    the active README names the standalone restore script instead
  * CLI-11: the instructions template's relative labkit reset
    command reaches the repository root helper from inside a lab

These read the real README.md and the real instructions template;
they must never touch labs/ or archives/.
"""

import re
import unittest

from pathlib import Path

from util import REPO_ROOT

README_TEXT = (REPO_ROOT / "README.md").read_text()
TEMPLATE_TEXT = (REPO_ROOT / "templates" / "new-terraform-lab" / "README.md").read_text()


class MigrationRetirementDocsTests(unittest.TestCase):
    """CLI-9: the one-time migration executable is retired, not advertised."""

    def test_cli9_retired_migration_has_no_active_command(self):
        # CLI-9 explicitly retires the one-time executable, not old pairs.
        self.assertFalse((REPO_ROOT / "scripts" / "migrate_002.py").exists())
        self.assertNotRegex(README_TEXT, r"python3 scripts/migrate_002\.py")
        self.assertIn("python3 scripts/restore_lab.py", README_TEXT)


class InstructionsTemplateDocsTests(unittest.TestCase):
    """The instructions template stays manual - its commands must run."""

    def test_cli11_relative_reset_from_inside_lab_reaches_the_helper(self):
        # CLI-11/2: the documented command must reach the final executable
        # from labs/<slug>, retaining ordinary reset without a directory.
        match = re.search(r"`((?:\.\./)+labkit) reset`", TEMPLATE_TEXT)
        self.assertIsNotNone(
            match, "the template must name the from-inside-lab "
            "labkit reset invocation as a relative path")
        invoked = Path(match.group(1))
        # resolve it the way a shell would, from inside a lab
        from_inside = (REPO_ROOT / "labs" / "example-lab" / invoked)
        self.assertEqual(from_inside.resolve(),
                         (REPO_ROOT / "labkit").resolve(),
                         "the template's relative labkit command must "
                         "reach the repository root's helper from inside "
                         "a lab directory")
        self.assertTrue((REPO_ROOT / "labkit").is_file())


if __name__ == "__main__":
    unittest.main()
