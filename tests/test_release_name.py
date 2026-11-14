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
        # BRAND-1: human display name; command examples remain lowercase.
        self.assertTrue(readme.startswith("# LabKit\n"))
        for command in ("./labkit start", "./labkit reset",
                        "./labkit archive", "--credentials-only", "--use-existing"):
            self.assertIn(command, readme)
        for path in (REPO_ROOT / "README.md",
                     *REPO_ROOT.glob("templates/*/README.md")):
            text = path.read_text()
            for obsolete in ("./lab-start", "./lab-reset", "`cloud-lab"):
                self.assertNotIn(obsolete, text, str(path))
            self.assertIn("labkit", text, str(path))

    def test_cli11_docs_order_destroy_before_archive_and_credential_cleanup(self):
        # CLI-11: retain the meaningful workflow check; safety prose is reviewed manually.
        text = (REPO_ROOT / "README.md").read_text()
        finishing = text[text.index("## Finish a lab"):text.index("## Archive recovery")]
        self.assertLess(finishing.index("terraform destroy"),
                        finishing.index("./labkit archive"))
        self.assertLess(finishing.index("./labkit archive"),
                        finishing.index("./labkit reset --credentials-only"))
