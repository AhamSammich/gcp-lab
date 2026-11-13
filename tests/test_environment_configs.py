"""Static safeguards only; runtime evidence lives in test_devcontainer_runtime.py."""

import json
import re
import sys
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

# Allow direct execution: python3 tests/test_environment_configs.py
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from util import REPO_ROOT


class DevContainerConfigTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(
            (REPO_ROOT / ".devcontainer/devcontainer.json").read_text()
        )

    def test_ac2_credentials_use_checkout_scoped_volume_not_host_bind(self):
        mounts = [dict(part.split("=", 1) for part in mount.split(","))
                  for mount in self.data["mounts"]]
        credential_mounts = [m for m in mounts
                             if m["target"] == "/home/vscode/.config/gcloud"]
        self.assertEqual(len(credential_mounts), 1)
        mount = credential_mounts[0]
        self.assertEqual(mount["type"], "volume")
        self.assertIn("${devcontainerId}", mount["source"])
        # No supplemental host mounts or Docker arguments may bypass isolation.
        self.assertTrue(all(m["type"] == "volume" for m in mounts))
        self.assertFalse(self.data.get("runArgs"))

    def test_ac2_default_config_and_adc_paths_are_inside_credential_volume(self):
        env = self.data.get("containerEnv", {})
        self.assertEqual(env.get("CLOUDSDK_CONFIG"), "/home/vscode/.config/gcloud")
        self.assertEqual(env.get("GOOGLE_APPLICATION_CREDENTIALS"),
                         "/home/vscode/.config/gcloud/application_default_credentials.json")

    def test_ac1_container_and_editor_default_to_nonroot(self):
        self.assertNotIn(self.data.get("containerUser", "root"), ("root", "0"))
        self.assertNotIn(self.data.get("remoteUser", "root"), ("root", "0"))


class FloxConfigTests(unittest.TestCase):
    """Flox environment configuration contract."""

    def setUp(self):
        self.flox_dir = REPO_ROOT / ".flox"
        self.manifest_file = self.flox_dir / "env" / "manifest.toml"
        self.gitignore_file = self.flox_dir / ".gitignore"

    def test_f1_complete_metadata_and_resolved_lockfile(self):
        metadata = json.loads((self.flox_dir / "env.json").read_text())
        self.assertEqual(metadata["version"], 1)
        lock = json.loads((self.flox_dir / "env/manifest.lock").read_text())
        self.assertTrue(lock["packages"], "A manifest alone is not a resolved environment")

    def test_f2_flox_gitignore_ignores_credentials_and_ephemeral_dirs(self):
        self.assertTrue(self.gitignore_file.is_file(),
                        ".flox/.gitignore must exist")
        content = self.gitignore_file.read_text(encoding="utf-8")
        for entry in ("run/", "cache/", "credentials/"):
            self.assertIn(entry, content, f".flox/.gitignore must ignore {entry}")


class EnvironmentDocsTests(unittest.TestCase):
    """Docs contract for optional environments."""

    def test_ac3_f3_root_links_to_both_environment_setup_guides(self):
        # AC3/F3: readers can reach environment-specific setup from the root.
        readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        links = re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", readme_text)
        for guide in (".devcontainer/README.md", ".flox/README.md"):
            with self.subTest(guide=guide):
                self.assertIn(guide, links)
                self.assertTrue((REPO_ROOT / guide).is_file())

    def test_ac3_f3_environment_guide_local_links_resolve(self):
        # AC3/F3: moving setup instructions must not strand their local links.
        for guide in (".devcontainer/README.md", ".flox/README.md"):
            path = REPO_ROOT / guide
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", text):
                url = urlsplit(target)
                if url.scheme or url.netloc or not url.path:
                    continue
                with self.subTest(guide=guide, target=target):
                    self.assertTrue((path.parent / unquote(url.path)).is_file())


if __name__ == "__main__":
    unittest.main()
