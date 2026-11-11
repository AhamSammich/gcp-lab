"""CLI-11: the final public command and current workflow guidance."""

import stat
import unittest

from util import REPO_ROOT


class ReleaseNameTests(unittest.TestCase):
    def test_cli11_only_labkit_is_shipped_and_executable(self):
        launcher = REPO_ROOT / "labkit"
        self.assertTrue(launcher.is_file())
        self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o755)
        for obsolete in ("cloud-lab", "lab-start", "lab-reset"):
            self.assertFalse((REPO_ROOT / obsolete).exists(), obsolete)

    def test_cli11_active_docs_use_final_commands(self):
        readme = (REPO_ROOT / "README.md").read_text()
        self.assertTrue(readme.startswith("# labkit\n"))
        for command in ("./labkit start", "./labkit reset",
                        "./labkit archive", "--credentials-only", "--use-existing"):
            self.assertIn(command, readme)
        for path in (REPO_ROOT / "README.md",
                     *REPO_ROOT.glob("templates/*/README.md")):
            text = path.read_text()
            for obsolete in ("./lab-start", "./lab-reset", "`cloud-lab"):
                self.assertNotIn(obsolete, text, str(path))
            self.assertIn("labkit", text, str(path))

    def test_cli11_docs_cover_finishing_and_recovery_boundaries(self):
        text = (REPO_ROOT / "README.md").read_text()
        for guidance in ("--001.meta.json", "0600", "hidden", "ignored",
                         "single writer", "concurrent", "crash-atomic",
                         "fresh destination", "input pair stays untouched",
                         "does not destroy", "does not sign",
                         "~/.cache/lab-start/", "lab-start.log", "lab-reset.log"):
            self.assertIn(guidance, text, guidance)
        finishing = text[text.index("## Finish a lab"):text.index("## Archive recovery")]
        self.assertLess(finishing.index("terraform destroy"),
                        finishing.index("./labkit archive"))
        self.assertLess(finishing.index("./labkit archive"),
                        finishing.index("./labkit reset --credentials-only"))
