"""Component tests: gcloud_setup() is ONE login that covers both stores.

`gcloud auth login --update-adc` writes the student credentials to
gcloud's own store AND application-default credentials (ADC, what
terraform authenticates with) in a single browser flow. The stub
gcloud logs every call, so these tests pin the exact command
sequence - real credentials are never touched.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from util import load_lab_start, stub_env

lab_start = load_lab_start()

PROJECT = "qwiklabs-gcp-01-newlab"
REGION = "us-central1"
ZONE = "us-central1-a"

# the "user closed the browser" stub: the login fails, everything
# else would have succeeded - gcloud_setup must bail out before any
# config set (both stores written or neither)
FAIL_LOGIN_STUB = """#!/bin/sh
echo "gcloud $*" >> "$GCLOUD_LOG"
case "$*" in
  "auth login --update-adc") exit 1 ;;
esac
exit 0
"""


class GcloudSetupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.home = root / "home"
        self.gcloud_log = root / "gcloud.log"

    def run_setup(self, stub_text=None):
        """gcloud_setup() against the stub gcloud, in-process."""
        env = stub_env(self.home, self.gcloud_log)
        if stub_text is not None:
            (self.home / "bin" / "gcloud").write_text(stub_text)
        with mock.patch.dict(os.environ, env, clear=True):
            return lab_start.gcloud_setup(PROJECT, REGION, ZONE)

    def test_one_login_covers_both_stores(self):
        # CLI-2: exactly one browser flow, not merely at least one.
        self.assertTrue(self.run_setup())
        calls = self.gcloud_log.read_text().splitlines()
        self.assertEqual([c for c in calls if c.startswith("gcloud auth ")],
                         ["gcloud auth login --update-adc"])

    def test_project_region_zone_set_after_login(self):
        # CLI-2: configure the exact values, in order, after one login.
        # CLI-10: ambient cloud overrides must not reach the stub tools.
        with mock.patch.dict(os.environ, {
            "CLOUDSDK_CORE_PROJECT": "synthetic-ambient-project",
            "GOOGLE_APPLICATION_CREDENTIALS": str(self.home / "ambient-adc.json"),
            "TF_CLI_CONFIG_FILE": str(self.home / "ambient.tfrc"),
        }):
            self.assertTrue(self.run_setup())
        self.assertEqual(self.gcloud_log.read_text().splitlines(), [
            "gcloud auth login --update-adc",
            f"gcloud config set project {PROJECT}",
            f"gcloud config set compute/region {REGION}",
            f"gcloud config set compute/zone {ZONE}",
        ])

    def test_cancelled_login_touches_nothing(self):
        # CLI-2: a cancelled login is attempted once, without configuration.
        self.assertFalse(self.run_setup(FAIL_LOGIN_STUB))
        self.assertEqual(self.gcloud_log.read_text().splitlines(),
                         ["gcloud auth login --update-adc"])


if __name__ == "__main__":
    unittest.main()
