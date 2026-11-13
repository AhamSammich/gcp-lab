"""Flox entry-point safety without Flox, cloud access, or real credentials."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from util import REPO_ROOT


class FloxSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="labkit-flox-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.host = self.home / ".config/gcloud"
        self.host.mkdir(parents=True)
        (self.host / "application_default_credentials.json").write_text("personal-synthetic")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        flox = self.bin / "flox"
        flox.write_text('#!/bin/bash\nexec "$TEST_PYTHON" -c \'import json, os; print(json.dumps(dict(os.environ)))\'\n')
        flox.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("CLOUDSDK_") and k not in
                    ("GOOGLE_APPLICATION_CREDENTIALS", "KUBECONFIG")}
        import sys
        self.env.update(HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}",
                        TEST_PYTHON=sys.executable)

    def checkout(self, name):
        checkout = self.root / name
        shutil.copytree(REPO_ROOT / ".flox", checkout / ".flox",
                        ignore=shutil.ignore_patterns("run", "cache", "lib", "log", "credentials"))
        return checkout

    def activate(self, checkout, extra=None):
        return subprocess.run(["bash", str(checkout / ".flox/activate"), "--", "true"],
                              env={**self.env, **(extra or {})}, cwd=self.root,
                              capture_output=True, text=True)

    def test_f2_checkout_credentials_are_private_persistent_and_separate(self):
        first, second = self.checkout("first checkout"), self.checkout("second")
        paths = []
        for checkout in (first, second):
            result = self.activate(checkout)
            self.assertEqual(result.returncode, 0, result.stderr)
            env = json.loads(result.stdout)
            state = checkout / ".flox/credentials"
            self.assertEqual(env["CLOUDSDK_CONFIG"], str(state / "gcloud"))
            self.assertEqual(env["GOOGLE_APPLICATION_CREDENTIALS"], str(state / "gcloud/application_default_credentials.json"))
            self.assertEqual(env["KUBECONFIG"], str(state / "kube/config"))
            for directory in (state, state / "gcloud", state / "kube"):
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            adc = Path(env["GOOGLE_APPLICATION_CREDENTIALS"])
            self.assertFalse(adc.exists())
            adc.write_text(checkout.name)
            paths.append(adc)
            self.assertEqual(self.activate(checkout).returncode, 0)
            self.assertEqual(adc.read_text(), checkout.name)
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual((self.host / "application_default_credentials.json").read_text(), "personal-synthetic")

    def test_f2_inherited_overrides_refused_before_flox_or_storage_changes(self):
        checkout = self.checkout("checkout")
        for name in ("CLOUDSDK_CONFIG", "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
                     "CLOUDSDK_AUTH_ACCESS_TOKEN", "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT",
                     "CLOUDSDK_ACTIVE_CONFIG_NAME", "GOOGLE_APPLICATION_CREDENTIALS", "KUBECONFIG"):
            with self.subTest(name=name):
                result = self.activate(checkout, {name: "synthetic-personal"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertFalse((checkout / ".flox/credentials").exists())

    def test_f2_symlinked_storage_refused_without_touching_target(self):
        for relative in ("credentials", "credentials/gcloud", "credentials/kube",
                         "credentials/gcloud/application_default_credentials.json"):
            with self.subTest(relative=relative):
                checkout = self.checkout("case-" + relative.replace("/", "-"))
                link = checkout / ".flox" / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(self.host, target_is_directory=True)
                result = self.activate(checkout)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Refusing credential storage", result.stderr)
                self.assertEqual(result.stdout, "")
        self.assertEqual((self.host / "application_default_credentials.json").read_text(), "personal-synthetic")

    def test_f2_symlinked_flox_directory_refused_without_creating_credentials(self):
        target = self.checkout("target")
        checkout = self.root / "linked"
        checkout.mkdir()
        (checkout / ".flox").symlink_to(target / ".flox", target_is_directory=True)
        result = self.activate(checkout)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing credential storage", result.stderr)
        self.assertFalse((target / ".flox/credentials").exists())


if __name__ == "__main__":
    unittest.main()
