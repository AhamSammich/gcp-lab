"""Configuration and Git safeguards; environment runtime tests are separate."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

# Allow direct execution: python3 tests/test_environment_configs.py
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from util import REPO_ROOT, run_git


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
        # F1: every declared tool/platform needs an actual catalog resolution,
        # and the lock must describe the current manifest, not an older one.
        # These manifest fields use simple quoted strings; read that subset
        # without adding a TOML dependency to the Python 3.10 test suite.
        manifest = self.manifest_file.read_text()
        installs = {name: {"pkg-path": path} for name, path in re.findall(
            r'^([\w-]+)\.pkg-path\s*=\s*"([^"\n]+)"\s*$', manifest, re.MULTILINE)}
        required = {"python3", "playwright", "terraform", "google-cloud-sdk", "kubectl"}
        self.assertTrue(required <= installs.keys(), "manifest omits required tools")
        systems_match = re.search(r'^systems\s*=\s*(\[[^\]\n]*\])', manifest, re.MULTILINE)
        self.assertIsNotNone(systems_match, "manifest must declare supported platforms")
        systems = json.loads(systems_match.group(1))
        self.assertTrue(systems, "manifest declares no supported platforms")
        lock = json.loads((self.flox_dir / "env/manifest.lock").read_text())
        self.assertEqual(lock["manifest"]["install"], installs)
        self.assertEqual(lock["manifest"]["options"]["systems"], systems)
        records = {(p["install_id"], p["system"]): p for p in lock["packages"]}
        self.assertEqual(len(records), len(lock["packages"]), "duplicate package resolutions")
        self.assertEqual(set(records), {(name, system) for name in installs for system in systems})
        for (name, system), package in records.items():
            with self.subTest(tool=name, system=system):
                self.assertEqual(package["attr_path"], installs[name]["pkg-path"])
                self.assertRegex(package["version"], r'\d+\.\d+')
                self.assertRegex(package["rev"], r'^[0-9a-f]{40}$')
                self.assertRegex(package["derivation"], r'^/nix/store/[0-9a-z]{32}-.+\.drv$')
                self.assertTrue(package["outputs_to_install"], "no installable outputs")
                for output in package["outputs_to_install"]:
                    self.assertRegex(package["outputs"][output], r'^/nix/store/[0-9a-z]{32}-.+')

    def test_f2_flox_gitignore_ignores_credentials_and_ephemeral_dirs(self):
        # F2: ordinary add-all cannot stage local credentials or runtime data.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            flox = repo / ".flox"
            flox.mkdir()
            (flox / ".gitignore").write_bytes(self.gitignore_file.read_bytes())
            for directory in ("credentials", "run", "cache"):
                nested = flox / directory / "nested"
                nested.mkdir(parents=True)
                (nested.parent / "application_default_credentials.json").write_text(
                    '{"token": "synthetic"}\n')
                (nested / "synthetic.json").write_text('{"token": "synthetic"}\n')
            manifest = flox / "env" / "manifest.toml"
            manifest.parent.mkdir()
            manifest.write_text("version = 1\n")
            run_git(repo, "init")
            run_git(repo, "add", "-A")
            staged = set(run_git(repo, "ls-files", "--cached", "-z")
                         .stdout.split("\0")) - {""}
            self.assertEqual(staged, {".flox/.gitignore", ".flox/env/manifest.toml"})


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
