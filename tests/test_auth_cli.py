"""CLI tests for the auth step: one browser login, end to end.

CLI-11/2: a full labkit start WITHOUT --skip-auth, against stub gcloud:
the auth prompt is answered, and the gcloud log must show the single
`auth login --update-adc` flow - no second application-default login.
"""

import tempfile
import unittest
from pathlib import Path

from util import make_repo, run_cli, stub_env

PROJECT = "qwiklabs-gcp-01-newlab"

# stdin, in prompt order: URL (enter to skip), Proceed, Authenticate
ANSWERS = "\ny\ny\n"


class AuthCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)
        self.repo = make_repo(self.workdir)  # never the real checkout
        self.home = self.workdir / "home"
        self.gcloud_log = self.workdir / "gcloud.log"
        self.env = stub_env(self.home, self.gcloud_log)

    def test_auth_is_one_login(self):
        # CLI-2: exactly one login followed by the requested config values.
        result = run_cli(
            self.repo, self.workdir, self.env, ANSWERS,
            "auth-lab",
            "--title", "Auth Lab",
            "--tasks", "1",
            "--project", PROJECT,
            "--zone", "us-central1-a",
            "--skip-init",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # auth ran and succeeded (not skipped)
        self.assertIn("gcloud:    ok", result.stdout)
        self.assertEqual(self.gcloud_log.read_text().splitlines(), [
            "gcloud auth login --update-adc",
            f"gcloud config set project {PROJECT}",
            "gcloud config set compute/region us-central1",
            "gcloud config set compute/zone us-central1-a",
            "gcloud config get-value account",  # final connection instructions
        ])


if __name__ == "__main__":
    unittest.main()
