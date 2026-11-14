"""Opt-in native Flox smoke test; never authenticates or contacts cloud APIs."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from util import REPO_ROOT


@unittest.skipUnless(os.environ.get("LABKIT_TEST_FLOX") == "1", "opt in with LABKIT_TEST_FLOX=1")
class FloxRuntimeTests(unittest.TestCase):
    def test_f1_f2_f4_actual_tools_and_checkout_credential_boundaries(self):
        self.assertIsNotNone(shutil.which("flox"), "Opt-in requires Flox installed")
        # Flox's Unix service socket must fit macOS's short pathname limit.
        with tempfile.TemporaryDirectory(prefix="lf-") as temp:
            root = Path(temp).resolve()
            home = root / "home"
            home.mkdir()
            host = home / ".config/gcloud"
            host.mkdir(parents=True)
            (host / "application_default_credentials.json").write_text("synthetic-host-adc")
            (host / "configurations").mkdir()
            host_config = host / "configurations/config_default"
            host_config.write_text("[core]\nproject = synthetic-host-project\n")
            kube = home / ".kube"
            kube.mkdir()
            (kube / "config").write_text("synthetic-host-kube")
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("CLOUDSDK_", "GOOGLE_", "TF_")) and k not in
                   ("KUBECONFIG", "FLOX_ENV", "FLOX_ENV_PROJECT",
                    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")}
            env.update(HOME=str(home), SHELL="/bin/bash", FLOX_DISABLE_METRICS="true",
                       CHECKPOINT_DISABLE="1", XDG_CONFIG_HOME=str(home / ".config"),
                       XDG_CACHE_HOME=str(root / "c"), XDG_STATE_HOME=str(home / ".local/state"))
            script = r'''
import json, os, pathlib, shutil, subprocess, sys
# F1: the accepted manifest selects python313 and its matching Playwright.
assert sys.version_info[:2] == (3, 13), sys.version
flox_bin = pathlib.Path(os.environ["FLOX_ENV"]) / "bin"
assert pathlib.Path(sys.executable).resolve() == (flox_bin / "python3").resolve()
os.environ["CLOUDSDK_CORE_DISABLE_USAGE_REPORTING"] = "true"
os.environ["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"
versions = {"python": sys.version.split()[0]}
for name, args in {"terraform": ["version", "-json"], "gcloud": ["version", "--format=json"], "kubectl": ["version", "--client", "-o", "json"]}.items():
    assert shutil.which(name), name
    assert pathlib.Path(shutil.which(name)).resolve() == (flox_bin / name).resolve(), name
    result = subprocess.run([name, *args], check=True, capture_output=True, text=True)
    versions[name] = json.loads(result.stdout)
state = pathlib.Path(os.environ["CLOUDSDK_CONFIG"])
expected = pathlib.Path(sys.argv[1]) / ".flox/credentials"
assert state == expected / "gcloud", (state, expected)
adc = pathlib.Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
kube = pathlib.Path(os.environ["KUBECONFIG"])
assert adc == state / "application_default_credentials.json"
assert kube == expected / "kube/config"
for directory in (expected, state, kube.parent):
    assert directory.stat().st_mode & 0o777 == 0o700
label = sys.argv[2]
if sys.argv[3] == "write":
    assert not adc.exists() and not kube.exists()
    # No account means gcloud's project setter skips remote access validation.
    # It also inspects ADC, so set the local property before writing the marker.
    account = subprocess.run(["gcloud", "config", "get", "account"], check=True, capture_output=True, text=True)
    assert account.stdout.strip() in ("", "(unset)"), account.stdout
    subprocess.run(["gcloud", "config", "set", "project", "synthetic-" + label], check=True)
    adc.write_text(label)
    kube.write_text(label)
else:
    assert adc.read_text() == label and kube.read_text() == label
    result = subprocess.run(["gcloud", "config", "get", "project"], check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "synthetic-" + label
print(json.dumps(versions))
'''
            for label in ("first", "second"):
                checkout = root / (label + " checkout")
                shutil.copytree(REPO_ROOT / ".flox", checkout / ".flox",
                                ignore=shutil.ignore_patterns("run", "cache", "lib", "log", "credentials"))
                probe = checkout / "probe.py"
                probe.write_text(script)
                for action in ("write", "read"):
                    result = subprocess.run(
                        ["bash", str(checkout / ".flox/activate"), "--", "python3", str(probe),
                         str(checkout), label, action], env=env, cwd=root,
                        capture_output=True, text=True, timeout=900)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    print(result.stdout)
            self.assertEqual((host / "application_default_credentials.json").read_text(), "synthetic-host-adc")
            self.assertEqual((kube / "config").read_text(), "synthetic-host-kube")
            self.assertEqual(host_config.read_text(), "[core]\nproject = synthetic-host-project\n")
            self.assertEqual(sorted(p.name for p in host.iterdir()),
                             ["application_default_credentials.json", "configurations"])


if __name__ == "__main__":
    unittest.main()
