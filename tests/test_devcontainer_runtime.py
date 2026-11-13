"""Opt-in AC1/AC2/AC4 lifecycle test. No real credentials or cloud calls.

Run: LABKIT_TEST_DEVCONTAINER=1 python3 -m unittest discover -s tests -p test_devcontainer_runtime.py -v
Only uniquely named scratch containers and their credential volumes are removed.
Build images/caches are left intact; no Docker pruning is performed.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from util import REPO_ROOT


@unittest.skipUnless(os.environ.get("LABKIT_TEST_DEVCONTAINER") == "1",
                     "opt-in Docker/devcontainer build and lifecycle test")
class DevContainerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.containers = []
        self.volumes = set()
        self.addCleanup(self.cleanup_scratch_resources)

    def cleanup_scratch_resources(self):
        for container in self.containers:
            exists = subprocess.run(["docker", "container", "inspect", container],
                                    capture_output=True).returncode == 0
            if exists:
                self.run_command("docker", "rm", "-f", container)
        for volume in self.volumes:
            exists = subprocess.run(["docker", "volume", "inspect", volume],
                                    capture_output=True).returncode == 0
            if exists:
                self.run_command("docker", "volume", "rm", volume)

    def run_command(self, *args):
        result = subprocess.run(args, text=True, capture_output=True, timeout=1800)
        self.assertEqual(result.returncode, 0,
                         f"{args!r}\n{result.stdout}\n{result.stderr}")
        return result.stdout.strip()

    def start(self, workspace, rebuild=False):
        args = ["devcontainer", "up", "--workspace-folder", str(workspace)]
        if rebuild:
            args.extend(["--remove-existing-container", "--build-no-cache"])
        output = self.run_command(*args)
        result = json.loads(output.splitlines()[-1])
        container = result["containerId"]
        self.containers.append(container)
        return container

    def execute(self, workspace, *args):
        return self.run_command("devcontainer", "exec", "--workspace-folder",
                                str(workspace), *args)

    def credential_volume(self, container, workspace):
        data = json.loads(self.run_command("docker", "inspect", container))[0]
        self.assertNotIn(data["Config"]["User"], ("", "0", "root"))
        mounts = data["Mounts"]
        # The scratch checkout is the only permitted host bind, never HOME/gcloud.
        self.assertTrue(all(m["Type"] == "volume" or
                            (m["Type"] == "bind" and
                             Path(m["Source"]).resolve() == workspace.resolve())
                            for m in mounts), mounts)
        matches = [m for m in mounts
                   if m["Destination"] == "/home/vscode/.config/gcloud"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["Type"], "volume")
        volume = matches[0]["Name"]
        self.assertTrue(volume.startswith("labkit-gcloud-config-"), volume)
        self.volumes.add(volume)
        return volume

    def test_ac1_ac2_ac4_tooling_writable_adc_rebuild_and_checkout_separation(self):
        # Only configuration is copied. No labs, host config, or real credentials.
        with tempfile.TemporaryDirectory(prefix="labkit-container-test-") as tmp:
            workspaces = [Path(tmp) / name for name in ("first", "second")]
            for workspace in workspaces:
                shutil.copytree(REPO_ROOT / ".devcontainer", workspace / ".devcontainer")
                (workspace / "lab-work.txt").write_text("keep lab work\n")
            first, second = workspaces
            container = self.start(first)
            volume = self.credential_volume(container, first)
            self.execute(first, "python3", "-c",
                         "import os,sys; assert os.getuid()!=0; assert sys.version_info >= (3,10)")
            for command in (("terraform", "version"), ("gcloud", "version"),
                            ("kubectl", "version", "--client"),
                            ("gke-gcloud-auth-plugin", "--version")):
                print(self.execute(first, *command), flush=True)
            config_path = self.execute(first, "gcloud", "info", "--format=value(config.paths.global_config_dir)")
            self.assertEqual(config_path, "/home/vscode/.config/gcloud")
            self.execute(first, "gcloud", "config", "set", "core/disable_usage_reporting", "true")
            self.execute(first, "python3", "-c", """
import json, os
from pathlib import Path
config = Path(os.environ['CLOUDSDK_CONFIG'])
adc = Path(os.environ['GOOGLE_APPLICATION_CREDENTIALS'])
assert adc.parent == config
assert not adc.exists(), 'fresh checkout unexpectedly has ADC'
adc.write_text(json.dumps({'type':'authorized_user', 'client_id':'synthetic',
                          'client_secret':'synthetic', 'refresh_token':'synthetic'}))
(config / 'persistence-marker').write_text('synthetic-only')
""")
            other = self.start(second)
            other_volume = self.credential_volume(other, second)
            self.assertNotEqual(volume, other_volume)
            self.execute(second, "python3", "-c", """
import os
from pathlib import Path
assert not Path(os.environ['GOOGLE_APPLICATION_CREDENTIALS']).exists()
assert not (Path(os.environ['CLOUDSDK_CONFIG']) / 'persistence-marker').exists()
""")
            rebuilt = self.start(first, rebuild=True)
            self.assertEqual(self.credential_volume(rebuilt, first), volume)
            self.assertEqual(self.execute(first, "gcloud", "config", "get",
                                          "core/disable_usage_reporting").lower(), "true")
            self.execute(first, "python3", "-c", """
import json, os
from pathlib import Path
assert json.loads(Path(os.environ['GOOGLE_APPLICATION_CREDENTIALS']).read_text())['refresh_token'] == 'synthetic'
marker = Path(os.environ['CLOUDSDK_CONFIG']) / 'persistence-marker'
assert marker.read_text() == 'synthetic-only'
marker.write_text('still-writable')
""")
            # Intentional cleanup targets only scratch credentials, not lab files.
            self.run_command("docker", "rm", "-f", rebuilt, other)
            self.run_command("docker", "volume", "rm", volume, other_volume)
            for workspace in workspaces:
                self.assertEqual((workspace / "lab-work.txt").read_text(), "keep lab work\n")


if __name__ == "__main__":
    unittest.main()
