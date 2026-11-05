"""Shared helpers for the lab-start test suite.

Hermetic by construction: `gcloud` is a stub script that logs its
calls instead of touching real credentials, HOME is redirected into a
tempdir (so the tools' ~/.cache/lab-start logs land there too), and
terraform is never invoked (--skip-init).
"""

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# the stub: records every invocation, answers 0, prints nothing -
# `gcloud config get-value account` yields an empty active account,
# so lab-reset takes its "no active account to revoke" path
GCLOUD_STUB = """#!/bin/sh
echo "gcloud $*" >> "$GCLOUD_LOG"
exit 0
"""


def load_lab_start():
    """Import lab-start (no .py extension) as a module, like lab-reset does."""
    loader = importlib.machinery.SourceFileLoader(
        "lab_start", str(REPO_ROOT / "lab-start"))
    spec = importlib.util.spec_from_loader("lab_start", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def stub_env(home: Path, gcloud_log: Path) -> dict:
    """A hermetic env: ONLY the stub bin on PATH, HOME in the tempdir."""
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    gcloud = bin_dir / "gcloud"
    gcloud.write_text(GCLOUD_STUB)
    gcloud.chmod(0o755)
    # lab-reset's shebang is `#!/usr/bin/env python3` - point that at
    # the interpreter running the tests
    python3 = bin_dir / "python3"
    if not python3.exists():
        python3.symlink_to(sys.executable)
    env = dict(os.environ)
    env["PATH"] = str(bin_dir)  # nothing real can leak in
    env["HOME"] = str(home)
    env["GCLOUD_LOG"] = str(gcloud_log)
    return env


def build_lab_dir(parent: Path) -> Path:
    """A half-finished lab dir: populated tasks, a user module, stale state."""
    lab = parent / "landing-zone-challenge"
    lab.mkdir()
    (lab / "task1.tf").write_text(
        '# Task 1\nresource "google_compute_network" "net" {\n  name = "vpc"\n}\n')
    (lab / "task2.tf").write_text("# Task 2\n# half done\n")
    (lab / "task3.tf").write_text("# Task 3\n# not started\n")
    (lab / "task4.tf").write_text("# Task 4\n")  # bare stub
    (lab / "terraform.tfstate").write_text('{"project": "qwiklabs-gcp-00-dead"}')
    (lab / "terraform.tfstate.backup").write_text("{}")
    (lab / "terraform.tfvars").write_text(
        'project_id = "qwiklabs-gcp-00-dead"\ncompute_region = "us-central1"\n')
    (lab / "variables.tf").write_text('variable "project_id" {}\n')
    (lab / "README.md").write_text("# Landing Zone Challenge\n\nstale readme\n")
    (lab / "providers.tf").write_text("# stale providers\n")
    (lab / "backend.tf").write_text("# stale backend\n")
    module = lab / "shared-vpc"
    module.mkdir()
    (module / "main.tf").write_text("# user module work\n")
    return lab


def snapshot(lab: Path) -> dict:
    """relative path -> bytes for every file under lab."""
    return {str(p.relative_to(lab)): p.read_bytes()
            for p in sorted(lab.rglob("*")) if p.is_file()}


def run_cli(cwd: Path, env: dict, stdin_text: str, *argv: str):
    """Run lab-start as a subprocess, the way a user would."""
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "lab-start"), *argv],
        cwd=cwd, env=env, input=stdin_text,
        capture_output=True, text=True, timeout=120,
    )