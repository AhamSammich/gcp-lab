"""Shared helpers for the lab-start test suite.

Hermetic by construction: `gcloud` is a stub script that logs its
calls instead of touching real credentials, HOME is redirected into a
tempdir (so the tools' ~/.cache/lab-start logs land there too), and
terraform is never invoked (--skip-init).

Phase 002 makes the suite repo-hermetic as well: `make_repo()` builds
an isolated scaffolding-tool checkout (the helper scripts plus the
template trees) inside a tempdir. CLI tests run the fixture's scripts,
never the real checkout, so no test can write into the actual
repository's `labs/` no matter how target resolution changes.
"""

import importlib.machinery
import importlib.util
import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- extended attributes, independently of scripts/migrate_002.py ------
# The test helpers deliberately re-implement the libc calls the
# migration helper uses, so a shared bug cannot make a preservation
# test pass against wrongly-restored data. macOS needs the 4-arg
# signatures with an options word; glibc uses the 3-arg ones.
#
# XATTR_NOFOLLOW is 0x0001 on Darwin (0x0008 is XATTR_REPLACE - the
# wrong value previously lived here AND in the helper, and the two
# copies agreed with each other's bug). Errors raise here too: an
# oracle that silently returned {} on failure masked the helper's
# no-follow bug for every symlink.

_XATTR_NOFOLLOW = 0x0001


def _xattr_libc():
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        for fn in ("listxattr", "getxattr", "setxattr"):
            getattr(libc, fn)
    except (OSError, AttributeError):
        return None
    return libc


_XATTR_LIBC = _xattr_libc()
XATTRS = _XATTR_LIBC is not None


def list_xattrs(path, follow_symlinks: bool = True) -> dict:
    """{name bytes: value bytes} for one entry; raises on read errors.

    A failed libc call raises OSError - never a silent empty map: an
    oracle that swallowed errors could agree with a preservation bug
    instead of exposing it (that is exactly how the wrong no-follow
    constant survived). A platform whose libc lacks the required
    functions also raises.
    """
    libc = _XATTR_LIBC
    if libc is None:
        raise RuntimeError("xattr support unavailable")
    darwin = sys.platform == "darwin"
    opts = 0 if follow_symlinks else _XATTR_NOFOLLOW
    path_b = os.fsencode(path)
    listxattr = libc.listxattr
    listxattr.restype = ctypes.c_ssize_t
    if darwin:
        listxattr.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                              ctypes.c_size_t, ctypes.c_int]
        size = listxattr(path_b, None, 0, opts)
    else:
        listxattr.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
        size = listxattr(path_b, None, 0)
    if size < 0:
        raise OSError(ctypes.get_errno(), "listxattr probe failed", str(path))
    if size == 0:
        return {}
    buf = ctypes.create_string_buffer(size + 1)
    size = (listxattr(path_b, buf, size, opts) if darwin
            else listxattr(path_b, buf, size))
    if size < 0:
        raise OSError(ctypes.get_errno(), "listxattr read failed", str(path))
    names = [n for n in buf.raw[:size].split(b"\0") if n]
    getxattr = libc.getxattr
    getxattr.restype = ctypes.c_ssize_t
    if darwin:
        # (path, name, value, size, position, options)
        getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                             ctypes.c_void_p, ctypes.c_size_t,
                             ctypes.c_uint32, ctypes.c_int]
    else:
        getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                             ctypes.c_void_p, ctypes.c_size_t]
    out = {}
    for name in names:
        if darwin:
            vsize = getxattr(path_b, name, None, 0, 0, opts)
        else:
            vsize = getxattr(path_b, name, None, 0)
        if vsize < 0:
            raise OSError(ctypes.get_errno(), "getxattr probe failed",
                          str(path))
        if vsize == 0:
            out[name] = b""
            continue
        vbuf = ctypes.create_string_buffer(vsize + 1)
        if darwin:
            got = getxattr(path_b, name, vbuf, vsize, 0, opts)
        else:
            got = getxattr(path_b, name, vbuf, vsize)
        if got < 0:
            raise OSError(ctypes.get_errno(), "getxattr read failed",
                          str(path))
        out[name] = bytes(vbuf.raw[:got])
    return out


def set_xattr(path, name: bytes, value: bytes) -> None:
    """Set one attribute; raises OSError when the platform refuses."""
    libc = _XATTR_LIBC
    if libc is None:
        raise RuntimeError("xattr support unavailable")
    darwin = sys.platform == "darwin"
    setxattr = libc.setxattr
    setxattr.restype = ctypes.c_ssize_t
    if darwin:
        setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                             ctypes.c_void_p, ctypes.c_size_t,
                             ctypes.c_uint32, ctypes.c_int]
        rc = setxattr(os.fsencode(path), name, value, len(value), 0, 0)
    else:
        setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                             ctypes.c_void_p, ctypes.c_size_t]
        rc = setxattr(os.fsencode(path), name, value, len(value), 0)
    if rc != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno), str(path))

# the four files the standard template carries (lab-start copies
# them verbatim into every new lab)
TEMPLATE_FILES = ("README.md", "backend.tf", "providers.tf", "variables.tf")

# every question answered by flags: a run with closed stdin and no
# prompts to answer
FULL_FLAGS = (
    "--title", "Landing Zone Challenge",
    "--tasks", "4",
    "--project", "qwiklabs-gcp-01-newlab",
    "--zone", "us-central1-a",
    "--skip-init", "--skip-auth",
)

# the stub: records every invocation, answers 0, prints nothing -
# `gcloud config get-value account` yields an empty active account,
# so lab-reset takes its "no active account to revoke" path
GCLOUD_STUB = """#!/bin/sh
echo "gcloud $*" >> "$GCLOUD_LOG"
exit 0
"""


def load_module(name: str, path: Path):
    """Import any file as a module, the way lab-reset loads lab-start."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load_lab_start(repo_root: Path = REPO_ROOT):
    """Import lab-start (no .py extension) as a module, like lab-reset does."""
    return load_module("lab_start", repo_root / "lab-start")


def load_migrate_002(repo_root: Path = REPO_ROOT):
    """Import the one-time 002 migration helper (scripts/migrate_002.py)."""
    return load_module("migrate_002", repo_root / "scripts" / "migrate_002.py")


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


def build_lab_dir(parent: Path, slug: str = "landing-zone-challenge") -> Path:
    """A half-finished lab dir: populated tasks, a user module, stale state."""
    lab = parent / slug
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


def snapshot_nofollow(root: Path) -> dict:
    """relpath -> (kind, payload) for every entry, never following symlinks.

    snapshot() recurses into symlinked directories (rglob follows
    them), which cannot survive a fixture carrying a directory alias:
    an alias pointing back at an ancestor loops the walk forever.
    These regressions compare trees that contain exactly such
    aliases, so each entry records only its OWN facts - file bytes,
    symlink target, or directory presence.
    """
    facts = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in dirnames + filenames:
            path = base / name
            if path.is_symlink():
                facts[str(path.relative_to(root))] = (
                    "symlink", os.readlink(path))
            elif path.is_dir():
                facts[str(path.relative_to(root))] = ("dir", None)
            else:
                facts[str(path.relative_to(root))] = (
                    "file", path.read_bytes())
    return facts


def run_cli(repo: Path, cwd: Path, env: dict, stdin_text: str, *argv: str):
    """Run the FIXTURE repo's lab-start as a subprocess, the way a user would."""
    return subprocess.run(
        [sys.executable, str(repo / "lab-start"), *argv],
        cwd=cwd, env=env, input=stdin_text,
        capture_output=True, text=True, timeout=120,
    )


def run_reset(repo: Path, cwd: Path, env: dict, *argv: str):
    """Run the FIXTURE repo's lab-reset as a subprocess."""
    return subprocess.run(
        [sys.executable, str(repo / "lab-reset"), *argv],
        cwd=cwd, env=env, input="",
        capture_output=True, text=True, timeout=120,
    )


def run_git(repo: Path, *args: str, check: bool = True):
    """Run git inside a fixture repo; returns the CompletedProcess."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


def same_file_probe(path: Path, spelling: str) -> bool:
    """True when `path.parent/spelling` names the SAME file as `path`.

    Verified by filesystem identity (st_dev+st_ino) - a stat probe,
    never a name comparison - so a case-sensitive filesystem answers
    False honestly for a case variant (or for a variant that simply
    does not exist), and two genuinely distinct directories are never
    equated. Case-insensitive and Unicode-normalizing filesystems
    answer True: one inode, several spellings.
    """
    try:
        given = os.stat(path.parent / spelling)
        wanted = os.stat(path)
    except OSError:
        return False
    return (given.st_dev, given.st_ino) == (wanted.st_dev, wanted.st_ino)


def make_repo(parent: Path) -> Path:
    """An isolated scaffolding-tool checkout in a tempdir.

    Mirrors a fresh clone of the 002 target layout: the two helper
    scripts, templates/new-standard-lab (the four template files),
    templates/new-terraform-lab (the instructions README), and managed
    labs/ + archives/ with tracked placeholders. Template content is
    copied from the real repo's relocated layout - no flat-scaffold
    fallback, no synthesized stand-ins: the fixture exercises exactly
    what a fresh clone ships.
    """
    repo = (parent / "repo").resolve()  # real path: matches the way
    repo.mkdir()                        # lab-start anchors REPO_ROOT

    for script in ("lab-start", "lab-reset"):
        shutil.copy2(REPO_ROOT / script, repo / script)
        (repo / script).chmod(0o755)

    relocated = repo / "templates" / "new-standard-lab"
    relocated.mkdir(parents=True)
    for name in TEMPLATE_FILES:
        shutil.copy2(REPO_ROOT / "templates" / "new-standard-lab" / name,
                     relocated / name)

    instructions = repo / "templates" / "new-terraform-lab"
    instructions.mkdir()
    shutil.copy2(REPO_ROOT / "templates" / "new-terraform-lab" / "README.md",
                 instructions / "README.md")

    (repo / "labs").mkdir()
    (repo / "labs" / ".gitkeep").write_text("")
    (repo / "archives").mkdir()
    (repo / "archives" / ".gitkeep").write_text("")
    return repo


def make_git_repo(parent: Path) -> Path:
    """make_repo() plus a git repository carrying the real ignore rules.

    The .gitignore copy is the point: these fixtures pin the tracking
    contract against whatever rules the real repo currently ships.
    """
    repo = make_repo(parent)
    shutil.copy2(REPO_ROOT / ".gitignore", repo / ".gitignore")
    run_git(repo, "init")
    run_git(repo, "config", "user.email", "lab-tests@example.com")
    run_git(repo, "config", "user.name", "Lab Tests")
    run_git(repo, "add", ".gitignore")
    run_git(repo, "commit", "-m", "ignore rules", "--no-verify")
    return repo
