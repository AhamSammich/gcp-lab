"""Archive one managed lab, verify the v1 pair, then remove its source."""

import contextlib
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

from .common import REPO_ROOT, resolve_lab_target
from . import preservation as p


def _git(*args, check=True):
    # No caller-selected repository, index, config injection, optional index
    # refresh, user/global excludes, or fsmonitor process may affect this check.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull)
    command = ["git", "-C", str(REPO_ROOT), "--work-tree=" + str(REPO_ROOT),
               "-c", "core.bare=false", "-c", "core.fsmonitor=false",
               "-c", "core.excludesFile=" + os.devnull, *args]
    result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True)
    if check and result.returncode:
        raise p.PreservationError(f"Git preflight failed: {os.fsdecode(result.stderr).strip()}")
    return result


def _intersects(a, b):
    # Git's byte spelling is not filesystem identity on the supported
    # case/normalization-folding filesystems. Refuse equivalent spellings.
    a, b = p._fs_equivalence_key(a), p._fs_equivalence_key(b)
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def preflight(source, paths):
    for directory in (REPO_ROOT / "labs", REPO_ROOT / "archives"):
        if directory.is_symlink() or not directory.is_dir():
            raise p.PreservationError(f"collection must be a real directory: {directory}")
    if not os.path.lexists(REPO_ROOT / ".git"):
        raise p.PreservationError(f"no tool Git checkout at {REPO_ROOT}")
    protected = [str(path.relative_to(REPO_ROOT)) for path in (source, *paths)]
    # HEAD and index are independent authorities: staged deletion must not
    # erase HEAD protection, and an index-only addition is protected too.
    records = []
    for item in _git("ls-tree", "-r", "-z", "HEAD").stdout.split(b"\0"):
        if item:
            header, name = item.split(b"\t", 1)
            records.append((header.split()[0], os.fsdecode(name)))
    for item in _git("ls-files", "--stage", "-z").stdout.split(b"\0"):
        if item:
            header, name = item.split(b"\t", 1)
            records.append((header.split()[0], os.fsdecode(name)))
    for mode, name in records:
        for target in protected:
            if _intersects(name, target):
                kind = "gitlink" if mode == b"160000" else "tracked path"
                raise p.PreservationError(
                    f"tool {kind} {name!r} intersects {target!r}; refusing archive")
    for path in paths:
        rel = str(path.relative_to(REPO_ROOT))
        result = _git("check-ignore", "--no-index", "-q", "--", rel, check=False)
        if result.returncode != 0:
            detail = os.fsdecode(result.stderr).strip()
            raise p.PreservationError(f"output is not gitignored: {path}" +
                                      (f" ({detail})" if detail else ""))


def next_pair(source):
    directory = REPO_ROOT / "archives"
    if directory.is_symlink() or not directory.is_dir():
        raise p.PreservationError(f"collection must be a real directory: {directory}")
    pattern = re.compile(re.escape(source.name) + r"--([0-9]+)\.(?:zip|meta\.json)\Z")
    highest = 0
    for child in directory.iterdir():
        match = pattern.fullmatch(child.name)
        if match:
            highest = max(highest, int(match[1]))
    zip_path = directory / f"{source.name}--{highest + 1:03d}.zip"
    return zip_path, zip_path.with_suffix(".meta.json")


def _identity(path):
    st = os.lstat(path)
    return st.st_dev, st.st_ino


@contextlib.contextmanager
def _exclusive(path, owned):
    # Defer ordinary Ctrl-C until open -> fstat -> ownership registration is
    # complete. Never grant ownership on EEXIST, even for dangling links.
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    fd = None
    try:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            st = os.fstat(fd)
            owned[path] = (st.st_dev, st.st_ino)
            os.fchmod(fd, 0o600)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            yield stream
    finally:
        if fd is not None:
            os.close(fd)


def write_pair(source, paths, inventory, owned):
    zip_path, meta_path = paths
    with _exclusive(zip_path, owned) as stream:
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED,
                             allowZip64=True) as archive:
            for rel, fact in sorted(inventory.items()):
                if fact["type"] != "file":
                    continue
                info = zipfile.ZipInfo(rel)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | fact["mode"]) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(source / rel, "rb") as payload, \
                        archive.open(info, "w", force_zip64=True) as output:
                    while True:
                        chunk = payload.read(p.READ_CHUNK)
                        if not chunk:
                            break
                        output.write(chunk)
    with _exclusive(meta_path, owned) as stream:
        record = {"version": p.SIDECAR_VERSION, "kind": p.SIDECAR_KIND,
                  "entries": inventory}
        stream.write(json.dumps(record, sort_keys=True).encode("utf-8"))


def verify_pair(paths, inventory):
    zip_path, meta_path = paths
    entries = p._load_sidecar(meta_path)
    p._validate_entries(entries, meta_path.name)
    if entries != inventory:
        raise p.PreservationError(f"metadata differs from original inventory: {meta_path}")
    with zipfile.ZipFile(zip_path) as archive:
        files, dirs = p._check_zip_names(archive.infolist())
        expected = {r for r, f in inventory.items() if f["type"] == "file"}
        if files != expected or dirs:
            raise p.PreservationError(f"payload set differs from original inventory: {zip_path}")
        for info in archive.infolist():
            mode = (stat.S_IFREG | inventory[info.filename]["mode"]) << 16
            if info.create_system != 3 or info.external_attr != mode:
                raise p.PreservationError(f"payload mode differs: {info.filename!r}")
            if p._hash_payload(archive, info.filename) != inventory[info.filename]["sha256"]:
                raise p.PreservationError(f"payload hash differs: {info.filename!r}")


def remove_source(path):
    # Only directories need writable/searchable permissions to unlink their
    # children. Never chmod files (which may have outside hardlinks), and
    # lstat every entry so links are unlinked, never traversed or chmod'd.
    if not stat.S_ISDIR(os.lstat(path).st_mode):
        raise p.PreservationError(f"source directory changed: {path}")
    os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) | 0o700,
             follow_symlinks=False)
    with os.scandir(path) as children:
        entries = list(children)
    for child in entries:
        child_path = path / child.name
        if stat.S_ISDIR(os.lstat(child_path).st_mode):
            remove_source(child_path)
        else:
            os.unlink(child_path)
    os.rmdir(path)


def _cleanup_owned(owned):
    for path, identity in owned.items():
        try:
            if _identity(path) == identity:
                path.unlink()
        except FileNotFoundError:
            pass
        except BaseException as error:
            print(f"warning: archive artifact remnant {path}: {error}", file=sys.stderr)


def _guidance(source, paths, *, stream):
    print(f"Verified recovery pair: {paths[0]} and {paths[1]}", file=stream)
    destination = source.with_name(source.name + "-restored")
    number = 1
    while os.path.lexists(destination):
        destination = source.with_name(f"{source.name}-restored-{number}")
        number += 1
    print(shlex.join(str(part) for part in
          (REPO_ROOT / "labkit", "restore", paths[0], destination)), file=stream)
    print("cd " + shlex.quote(str(REPO_ROOT)), file=stream)


def archive_lab(raw_target=None, dry_run=False):
    owned = {}
    destructive = False
    source = None
    paths = None
    try:
        # Capture cwd before any deletion; guidance uses only absolute paths.
        cwd = Path.cwd()
        try:
            source = resolve_lab_target(raw_target or str(cwd), cwd=cwd,
                                        must_exist=True)
        except SystemExit as error:
            raise p.PreservationError(str(error)) from error
        paths = next_pair(source)
        preflight(source, paths)
        source_identity = _identity(source)
        inventory = p._inventory_tree(source)
        if dry_run:
            print(f"Source: {source}\nArchive: {paths[0]}\nMetadata: {paths[1]}\n"
                  "Would verify both artifacts, then remove the source tree.")
            return 0
        write_pair(source, paths, inventory, owned)
        verify_pair(paths, inventory)
        preflight(source, paths)
        if (_identity(source) != source_identity or
                p._inventory_tree(source) != inventory):
            raise p.PreservationError(f"source changed before removal: {source}")
        for path in paths:
            if _identity(path) != owned[path]:
                raise p.PreservationError(f"archive artifact replaced: {path}")
        verify_pair(paths, inventory)
        destructive = True
        remove_source(source)
        if os.path.lexists(source):
            raise p.PreservationError(f"source remains: {source}")
        print(f"Archived and removed: {source}")
        _guidance(source, paths, stream=sys.stdout)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        interrupted = isinstance(error, KeyboardInterrupt)
        print(f"archive failed: {'interrupted (Ctrl-C)' if interrupted else error}",
              file=sys.stderr)
        if destructive and source is not None and paths is not None:
            print(f"Source remnant (if present): {source}", file=sys.stderr)
            # A reporting error must never hide the original failure or cause
            # verified recovery artifacts to be removed after cleanup began.
            try:
                _guidance(source, paths, stream=sys.stderr)
            except BaseException as guidance_error:
                print(f"recovery guidance failed: {guidance_error}; pair: "
                      f"{paths[0]}, {paths[1]}", file=sys.stderr)
        else:
            _cleanup_owned(owned)
            if source is not None:
                print(f"Source not removed by archive: {source}", file=sys.stderr)
        return 130 if interrupted else 1


def add_arguments(parser):
    parser.add_argument("directory", nargs="?", help="managed lab slug or path; default: current lab root")
    parser.add_argument("--dry-run", action="store_true", help="read-only archive preview; do not write or remove anything")


def run(args):
    return archive_lab(args.directory, args.dry_run)
