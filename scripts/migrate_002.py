"""One-time migration helper for .plans/002-directory-restructure.md.

A small, stdlib-only tool for the 002 Release step: classify the
repository's lab inventory, archive every tracked lab into a verified
local zip plus a preservation sidecar, untrack exactly the inventoried
lab paths with scoped `git rm --cached`, move wholly untracked lab
trees into labs/, and retire source directories only after their
archives verify. It is deliberately NOT the reusable archive command
(roadmap item 3 owns that); it exists to migrate this one repository,
once, and to prove the result is recoverable.

Test-facing API
---------------

Exceptions (all carry a human-readable report of what was refused):

    MigrationError                    - base class; any failed/aborted step
    DestinationCollision(MigrationError)   - a destination already exists
    UnsafeArchiveEntry(MigrationError)     - an archive entry escapes its root
    StagedLabConflict(MigrationError)      - a lab path has staged changes

Functions:

    plan(repo_root) -> Plan
        Classify the whole lab inventory of a repository.
          Plan.tracked     - sorted slugs of labs with tracked source
                             (untracked/ignored files inside them still
                              belong to these labs, not to .untracked)
          Plan.untracked   - sorted slugs of wholly untracked labs.
                             An untracked root directory is only a lab
                             when it demonstrates lab evidence: a
                             top-level .terraform directory or a
                             top-level *.tf / *.tfvars / tfstate file.
                             Tracked source is its own evidence.
          Plan.collisions  - sorted slugs whose destination
                             (labs/<slug>/, archives/<slug>.zip, or the
                             archives/<slug>.meta.json sidecar) already
                             exists
        Tooling, templates, tests, virtual environments, and metadata
        directories are never classified as labs. Anything the
        classifier cannot explain - a root-level symlink, a tracked
        top-level path with nothing on disk, an untracked root
        directory with no lab evidence (a docs/ or cache lookalike) -
        is an error, not a guess.

    preflight(repo_root) -> dict
        The read-only evidence step: the plan() classification plus an
        explicit full-disk inventory of every classified lab (hidden
        files, ignored state, nested .git/.terraform, symlink targets,
        permissions, and extended attributes included), summarized as
        per-lab counts. Records problems for labs that could not be
        inventoried. Creates nothing; writes nothing.

    run(repo_root, migration_plan=None) -> Report
        Execute the migration: archive tracked labs into verified local
        zips under archives/, remove exactly the inventoried lab paths
        from Git's index with scoped `git rm --cached`, move wholly
        untracked lab trees into labs/, retire source directories only
        after their archives verify.
          Report.archived   - sorted slugs archived
          Report.moved      - sorted slugs moved into labs/
        Before anything is written the run verifies the managed ignore
        rules (archive payloads, sidecars, and moved labs ignored;
        .gitkeep placeholders trackable) and inventories EVERY lab,
        tracked and untracked alike - an unsupported entry anywhere,
        even in a lab that would only move, is a refusal with the
        index, sources, and artifacts untouched. Every inventory is
        validated with the same structural and name rules
        restore_archive enforces on a sidecar, so a source is never
        retired behind an archive that cannot rebuild its tree.
        Tracked snapshots are
        re-verified before the untrack step and before each move;
        immediately before EACH source retirement the complete zip AND
        sidecar are revalidated against the original inventory - the
        sidecar is the only copy of symlink targets, directory modes,
        and extended attributes.
        Raises DestinationCollision / StagedLabConflict before changing
        anything when the plan reports either. Every pre-existing
        destination collides - including a valid, byte-identical
        archive or a bare sidecar; accepting one would require an
        explicit audited resume plan that does not exist yet. A passed
        plan that no longer matches the live inventory is a stale-plan
        error. Resumability comes from cleanup instead: a failed
        pre-retirement attempt removes the artifacts it newly created,
        so a retry starts clean - and so does a Ctrl-C before the
        index or any source is touched: the interrupt propagates
        unwrapped after that same cleanup. Once every archive has
        verified, the retirement phase keeps verified copies: a later
        failure (or a Ctrl-C after the first index or source change)
        retains moved labs and archives, reports progress, and never
        deletes a verified destination or an unverified source
        remainder.

    verify_archive(zip_path, hashes=None) -> None
        Reopen an archive and check it before any source is retired.
        `hashes` maps archive-relative paths to expected sha256 hex
        digests; the archive's file-entry set must match it exactly and
        every hash must match. Entry names must stay inside the
        archive's root: absolute paths or any `..` component raises
        UnsafeArchiveEntry. Duplicate entries, entries that are both a
        file and a directory, and file entries that sit below another
        file entry (an extraction-through-symlink/traversal shape) are
        refused before anything is read.

    restore_archive(zip_path, dest) -> None
        Rebuild a lab tree from a verified archive into `dest` - the
        recoverability check. The zip carries the file payloads; the
        sibling <slug>.meta.json sidecar (written next to the zip by
        run()) carries what a zip cannot: symlink targets, per-entry
        permission bits including directories and the lab root,
        extended attributes, and the exact entry set. restore refuses
        unsafe entry names, metadata whose symlink entries point
        outside the destination, zip/metadata disagreements, and any
        pre-existing destination (file, directory, or dangling
        symlink) - before writing anything. It creates symlinks only
        after all files are written, so nothing is ever extracted
        through a symlink, and it re-inventories the rebuilt tree
        against the sidecar before returning. A failed restore
        removes only the partial destination it created.

Safety rules the implementation upholds (from the spec)
---------------------------------------------------------

* Classify whole lab directories, not individual files. Untracked and
  ignored files inside a tracked lab (edits, task files, state,
  tfvars, caches, nested .git metadata) belong to that lab's
  preservation inventory.
* Check every destination before changing any source. Archives are
  created exclusively (O_CREAT|O_EXCL) so a race cannot overwrite
  anything. Never overwrite or merge; a collision stops the
  migration with sources intact.
* Preserve the complete recoverable local tree. The zip cannot
  faithfully represent symlinks, directory/root permission bits, or
  platform extended attributes (e.g. macOS com.apple.provenance), so
  those are carried by the verified sidecar and restored explicitly.
  This Python build lacks os.listxattr, so the sidecar reads and
  writes extended attributes through libc directly (ctypes); if that
  support is unavailable the helper refuses to run rather than
  silently dropping metadata. No-follow reads use Darwin's
  XATTR_NOFOLLOW options bit (0x0001) or glibc's l-prefixed symbols,
  so a symlink's OWN attributes are preserved, distinct from its
  referent's. Every failed attribute call raises - a platform that
  cannot read or write an attribute in the required mode is a
  refusal, never a silent empty map - and only a genuine zero-length
  attribute value is recorded as an empty value.
* Snapshot and verify: every lab - tracked AND untracked - is
  inventoried before anything is written; each inventory is validated
  with the same structural and name rules restore_archive enforces on
  a sidecar, so a lab carrying an entry restore could not recreate
  (an unsafe name on a metadata-only entry) is refused with the
  sources, index, and artifacts untouched; each tracked lab is
  re-inventoried after archiving, all tracked sources are re-verified
  before the index changes, and each source is re-inventoried and its
  complete archive+sidecar revalidated right before retirement. A
  source that changed in between stops the run with nothing deleted.
  Symlink containment is validated against the full link map - a
  chained escape (link -> '.' plus escape -> 'link/..') or a cycle is
  invisible to a single-level lexical check and is refused - both for
  source inventory and for sidecar restore.
* Untrack with scoped `git rm --cached` only - working files stay on
  disk, unrelated staged changes stay staged, never force, never a
  whole-repository untrack/re-add. The paths are passed with
  --literal-pathspecs, so an inventoried path that carries glob
  characters untracks exactly itself; a lab path with staged changes
  is a StagedLabConflict, not something to force through.
* labs/ and archives/ must be real directories (a symlinked
  collection dir could move lab trees outside the repository), and
  untracked labs move by rename with a before/after inventory
  comparison.
* Treat local archives and sidecars as sensitive: they are created
  owner-only (0600) and this tool never prints their contents.
* Never reset, revoke credentials, destroy, or apply. This helper
  moves and archives bytes; it does not touch gcloud or terraform.

Command line
-------------

    python3 scripts/migrate_002.py [--repo ROOT] plan
    python3 scripts/migrate_002.py [--repo ROOT] preflight
    python3 scripts/migrate_002.py [--repo ROOT] run --yes
    python3 scripts/migrate_002.py [--repo ROOT] restore ZIP DEST

`--repo` is a global option: it comes BEFORE the subcommand (that is
how argparse defines it). `run` requires an explicit --yes; nothing
about it is implicit.
"""

import ctypes
import hashlib
import json
import os
import posixpath
import shutil
import stat
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path

# shutil.rmtree's onexc= keyword exists from Python 3.12; the
# documented baseline (3.10/3.11) only knows onerror= - see
# _force_rmtree. A module constant so the 3.10 branch stays
# exercisable in tests on newer interpreters.
_PY312 = sys.version_info >= (3, 12)


class MigrationError(Exception):
    """Any failed or refused migration step (base class)."""


class DestinationCollision(MigrationError):
    """A destination (labs/<slug>/ or archives/<slug>.zip) already exists."""


class UnsafeArchiveEntry(MigrationError):
    """An archive entry escapes its root (absolute path or `..`)."""


class StagedLabConflict(MigrationError):
    """A lab path has staged changes; refusing to force index removal."""


# Root-level entries that are never labs: the tool itself, its
# templates and tests, the managed collections, and local metadata.
EXCLUDED_ROOTS = frozenset({
    ".git", ".venv", "__pycache__", "scripts", "templates", "tests",
    "labs", "archives", ".plans",
})

# Archives carry the file payloads; the sidecar carries what a zip
# cannot (symlink targets, directory/root modes, xattrs, entry set).
SIDECAR_VERSION = 1
SIDECAR_KIND = "gcp-lab-002-archive-metadata"


class Plan:
    """The classified inventory: sorted slugs, plus destination collisions."""

    def __init__(self, tracked, untracked, collisions):
        self.tracked = tracked
        self.untracked = untracked
        self.collisions = collisions


class Report:
    """What a completed (or resumed) migration actually did."""

    def __init__(self, archived, moved):
        self.archived = archived
        self.moved = moved


# --- extended attributes ------------------------------------------------
# This Python build has no os.listxattr/os.getxattr, so preservation
# goes through libc directly. No-follow reads and writes use Darwin's
# XATTR_NOFOLLOW options bit (0x0001 - 0x0008 is XATTR_REPLACE) or
# glibc's l-prefixed symbols; anything else would silently follow a
# symlink and misattribute the target's attributes to the link.

_XATTR_NOFOLLOW = 0x0001


def _xattr_support():
    if hasattr(os, "listxattr") and hasattr(os, "getxattr"):
        return "os"
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        for name in ("listxattr", "getxattr", "setxattr", "removexattr"):
            getattr(libc, name)
        if sys.platform != "darwin":
            # glibc names the no-follow variants with an l prefix;
            # without them a no-follow read would silently follow the
            # link, so their absence is a refusal, not a fallback
            for name in ("llistxattr", "lgetxattr", "lsetxattr",
                         "lremovexattr"):
                getattr(libc, name)
        return libc
    except (OSError, AttributeError):
        return None


_XATTR = _xattr_support()


def _libc_symbol(libc, name):
    """A libc function, or a refusal - never a silent follow."""
    try:
        return getattr(libc, name)
    except AttributeError:
        raise MigrationError(
            f"this platform exposes no {name}; a no-follow "
            "extended-attribute operation would silently follow the "
            "symlink - refusing") from None


def _xattr_list(path, follow_symlinks=True):
    """{name: hex value} for one entry. Requires _XATTR support.

    A no-follow read reports the entry's OWN attributes - a symlink's,
    not its referent's. Every failed call raises MigrationError: a
    platform that cannot read attributes is a refusal, never a silent
    empty map. A genuine zero-length attribute value is real data and
    is recorded as "".
    """
    if _XATTR is None:
        raise MigrationError("extended-attribute support unavailable")
    if _XATTR == "os":
        try:
            names = os.listxattr(path, follow_symlinks=follow_symlinks)
            return {n: os.getxattr(path, n, follow_symlinks=follow_symlinks).hex()
                    for n in names}
        except OSError as e:
            raise MigrationError(f"cannot read extended attributes of {os.path.basename(path)!r}: {e}") from e
    libc = _XATTR
    darwin = sys.platform == "darwin"
    opts = 0 if follow_symlinks else _XATTR_NOFOLLOW
    path_b = os.fsencode(path)

    listxattr = (libc.listxattr if darwin
                 else _libc_symbol(libc, "listxattr" if follow_symlinks
                                   else "llistxattr"))
    listxattr.restype = ctypes.c_ssize_t
    if darwin:
        listxattr.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                              ctypes.c_size_t, ctypes.c_int]
        size = listxattr(path_b, None, 0, opts)
    else:
        listxattr.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
        size = listxattr(path_b, None, 0)
    if size < 0:
        err = ctypes.get_errno()
        raise MigrationError(f"listxattr failed on {os.path.basename(path)!r}: {os.strerror(err)}")
    if size == 0:
        return {}
    buf = ctypes.create_string_buffer(size + 1)
    size = (listxattr(path_b, buf, size, opts) if darwin
            else listxattr(path_b, buf, size))
    if size < 0:
        err = ctypes.get_errno()
        raise MigrationError(f"listxattr failed on {os.path.basename(path)!r}: {os.strerror(err)}")
    names = [n for n in buf.raw[:size].split(b"\0") if n]

    getxattr = (libc.getxattr if darwin
                else _libc_symbol(libc, "getxattr" if follow_symlinks
                                  else "lgetxattr"))
    getxattr.restype = ctypes.c_ssize_t
    if darwin:
        # macOS: (path, name, value, size, position, options)
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
            err = ctypes.get_errno()
            raise MigrationError(
                f"getxattr failed on {os.path.basename(path)!r}/"
                f"{os.fsdecode(name)}: {os.strerror(err)}")
        if vsize == 0:
            out[os.fsdecode(name)] = ""
            continue
        vbuf = ctypes.create_string_buffer(vsize + 1)
        got = (getxattr(path_b, name, vbuf, vsize, 0, opts) if darwin
               else getxattr(path_b, name, vbuf, vsize))
        if got < 0:
            err = ctypes.get_errno()
            raise MigrationError(
                f"getxattr failed on {os.path.basename(path)!r}/"
                f"{os.fsdecode(name)}: {os.strerror(err)}")
        out[os.fsdecode(name)] = bytes(vbuf.raw[:got]).hex()
    return out


def _xattr_set(path, name, value_hex, follow_symlinks=True):
    if _XATTR is None:
        raise MigrationError("extended-attribute support unavailable")
    if _XATTR == "os":
        os.setxattr(path, name, bytes.fromhex(value_hex),
                     follow_symlinks=follow_symlinks)
        return
    libc = _XATTR
    darwin = sys.platform == "darwin"
    opts = 0 if follow_symlinks else _XATTR_NOFOLLOW
    value = bytes.fromhex(value_hex)
    setxattr = (libc.setxattr if darwin
                else _libc_symbol(libc, "setxattr" if follow_symlinks
                                  else "lsetxattr"))
    setxattr.restype = ctypes.c_ssize_t
    if darwin:
        setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                             ctypes.c_void_p, ctypes.c_size_t,
                             ctypes.c_uint32, ctypes.c_int]
        rc = setxattr(os.fsencode(path), os.fsencode(name), value,
                       len(value), 0, opts)
    else:
        setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                             ctypes.c_void_p, ctypes.c_size_t]
        rc = setxattr(os.fsencode(path), os.fsencode(name), value,
                       len(value), 0)
    if rc != 0:
        err = ctypes.get_errno()
        raise MigrationError(
            f"cannot restore extended attribute {name!r} on "
            f"{os.path.basename(path)!r}: {os.strerror(err)}")


def _xattr_remove(path, name, follow_symlinks=True):
    if _XATTR is None:
        raise MigrationError("extended-attribute support unavailable")
    if _XATTR == "os":
        os.removexattr(path, name, follow_symlinks=follow_symlinks)
        return
    libc = _XATTR
    darwin = sys.platform == "darwin"
    opts = 0 if follow_symlinks else _XATTR_NOFOLLOW
    removexattr = (libc.removexattr if darwin
                   else _libc_symbol(libc, "removexattr" if follow_symlinks
                                     else "lremovexattr"))
    removexattr.restype = ctypes.c_ssize_t
    if darwin:
        removexattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        rc = removexattr(os.fsencode(path), os.fsencode(name), opts)
    else:
        removexattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        rc = removexattr(os.fsencode(path), os.fsencode(name))
    if rc != 0:
        err = ctypes.get_errno()
        raise MigrationError(
            f"cannot remove unexpected extended attribute {name!r} on "
            f"{os.path.basename(path)!r}: {os.strerror(err)}")


def _apply_xattrs(path, recorded, follow_symlinks=True):
    """Make an entry's attribute map equal the recorded one exactly.

    Recorded values are set, and attributes the platform added that
    the record does not carry (e.g. a system provenance stamp on a
    freshly created file) are removed - restoration must not invent
    metadata the archived tree did not have.
    """
    if not recorded:
        # still normalize: a created entry may carry system-added
        # attributes the original did not have
        current = _xattr_list(path, follow_symlinks)
        for name in current:
            _xattr_remove(path, name, follow_symlinks)
        return
    for name, value_hex in recorded.items():
        _xattr_set(path, name, value_hex, follow_symlinks)
    current = _xattr_list(path, follow_symlinks)
    for name in current:
        if name not in recorded:
            _xattr_remove(path, name, follow_symlinks)


# --- inventory -----------------------------------------------------------

def _sha256_file(path, rel):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as e:
        raise MigrationError(
            f"unreadable file {rel!r} - refusing to archive a partial inventory ({e})") from e
    return digest.hexdigest()


def _fs_equivalence_key(value: str) -> str:
    """Conservative filesystem-equivalence key for a name or path.

    casefold() folds case but not Unicode normalization, yet a
    normalization-insensitive filesystem (the default on macOS)
    treats precomposed 'e\u0301' (U+00E9) and decomposed 'e'+U+0301
    as the SAME name - so a chain spelled with one reaches through
    a real link named with the other. Every name-ambiguity decision
    (link-chain lookups, entry-name collisions) therefore folds
    case AND normalizes canonically; the recorded names themselves
    are never rewritten - only lookups use this key.
    """
    return unicodedata.normalize("NFD", value.casefold())


def _resolve_within(rel, links, depth=0):
    """Resolve `rel` against the known symlink map, purely lexically.

    Returns the path relative to the lab root ("" is the root itself,
    a legitimate target), or None when the chain leaves the root or
    loops. Symlink targets are spliced from `links` without touching
    the filesystem, so chained escapes - link -> '.' plus
    escape -> 'link/..' normalizes to '.' lexically but resolves
    OUTSIDE the root - and cycles are caught; a single-level normpath
    check cannot see them. Case and Unicode normalization are
    significant: a spelling that equivalence-matches a known link
    without matching it exactly is refused too (None), because the
    archive may be followed or restored on a case-insensitive or
    normalization-insensitive filesystem where that spelling DOES
    reach through the real link.
    """
    if depth > 40:
        return None  # a chain this long is a loop
    folded = {_fs_equivalence_key(key) for key in links}
    stack = []
    for part in rel.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not stack:
                return None
            stack.pop()
            continue
        stack.append(part)
        current = "/".join(stack)
        if current in links:
            target = links[current]
            if not target or os.path.isabs(target):
                return None
            spliced = _resolve_within(
                posixpath.join(posixpath.dirname(current), target),
                links, depth + 1)
            if spliced is None:
                return None
            stack = spliced.split("/") if spliced else []
        elif _fs_equivalence_key(current) in folded:
            # a case- or Unicode-normalization-variant spelling of a
            # known link: harmless on a fully sensitive platform, but
            # a case-insensitive or normalization-insensitive one
            # would traverse the real link here - refuse rather than
            # guess which filesystem the tree will live on
            return None
    return "/".join(stack)


def _inventory_tree(lab_root):
    """Explicit disk inventory of one lab tree.

    Returns {relative path: facts}. The lab root itself is recorded
    under "". Facts carry type, the full permission bits including
    the setuid/setgid/sticky group, sha256 (files), link target
    (symlinks), and the extended-attribute map - everything the
    preservation contract verifies. Hidden entries, ignored state,
    and nested .git/.terraform content are included; special files
    (FIFOs, sockets, devices) and symlinks whose target chain - through
    other links - leaves the lab root, or loops, are refused instead
    of guessed at. Symlinked directories are never descended into.
    The finished inventory is validated with the same structural and
    name rules restore_archive enforces on a sidecar: an entry
    restore could not recreate (an unsafe name on a metadata-only
    entry, a broken parent chain) is a refusal HERE, before anything
    is written - never a source deletion followed by an archive
    that cannot rebuild the tree it replaced.
    """
    lab_root = Path(lab_root)
    if lab_root.is_symlink() or not lab_root.is_dir():
        raise MigrationError(f"not a real directory: {lab_root.name}")
    try:
        root_st = lab_root.lstat()
        facts = {"": {"type": "dir", "mode": stat.S_IMODE(root_st.st_mode),
                     "xattrs": _xattr_list(lab_root)}}
    except OSError as e:
        raise MigrationError(f"cannot inventory {lab_root.name!r}: {e}") from e
    pending = [""]
    while pending:
        rel = pending.pop()
        dir_path = lab_root if rel == "" else lab_root / rel
        try:
            with os.scandir(dir_path) as it:
                children = sorted(it, key=lambda entry: entry.name)
        except OSError as e:
            where = rel or "."
            raise MigrationError(f"cannot list {where!r} in {lab_root.name!r}: {e}") from e
        for child in children:
            child_rel = child.name if rel == "" else f"{rel}/{child.name}"
            try:
                st = child.stat(follow_symlinks=False)
            except OSError as e:
                raise MigrationError(f"cannot stat {child_rel!r} in {lab_root.name!r}: {e}") from e
            if stat.S_ISLNK(st.st_mode):
                try:
                    target = os.readlink(child.path)
                except OSError as e:
                    raise MigrationError(f"cannot read symlink {child_rel!r}: {e}") from e
                if os.path.isabs(target):
                    raise MigrationError(
                        f"symlink {child_rel!r} has an absolute target - "
                        "refusing rather than guessing how to preserve it")
                facts[child_rel] = {
                    "type": "symlink", "mode": stat.S_IMODE(st.st_mode),
                    "target": target,
                    "xattrs": _xattr_list(child.path, follow_symlinks=False),
                }
            elif stat.S_ISDIR(st.st_mode):
                facts[child_rel] = {"type": "dir",
                                    "mode": stat.S_IMODE(st.st_mode),
                                    "xattrs": _xattr_list(child.path)}
                pending.append(child_rel)
            elif stat.S_ISREG(st.st_mode):
                facts[child_rel] = {
                    "type": "file", "mode": stat.S_IMODE(st.st_mode),
                    "sha256": _sha256_file(child.path, child_rel),
                    "xattrs": _xattr_list(child.path),
                }
            else:
                raise MigrationError(
                    f"unsupported entry type at {child_rel!r} "
                    "(special files cannot be preserved in a zip) - refusing")

    # after the walk: every symlink's full chain must resolve inside
    # the lab root against the complete link map - a two-link escape
    # or a cycle is invisible to the single-level check above
    links = {rel: fact["target"] for rel, fact in facts.items()
             if fact["type"] == "symlink"}
    for rel, target in links.items():
        if _resolve_within(posixpath.join(posixpath.dirname(rel) or ".",
                                          target), links) is None:
            raise MigrationError(
                f"symlink {rel!r} escapes its lab root through a link "
                "chain (or loops) - refusing")
    # ...and the finished inventory must satisfy the same structural
    # and name rules restore_archive enforces on a sidecar, so a
    # source is never retired behind an archive that cannot rebuild
    # it. The zip carries regular files only, so metadata-only
    # entries (directories, symlinks) are invisible to the zip-name
    # validator; this is the only place their names are checked
    # before retirement.
    _validate_entries(facts, lab_root.name)
    return facts


def _file_hashes(inventory):
    return {rel: fact["sha256"] for rel, fact in inventory.items()
            if fact["type"] == "file"}


# --- archive entry names --------------------------------------------------

def _safe_relpath(name):
    """True when an archive entry name stays inside the archive root."""
    if not isinstance(name, str) or not name:
        return False
    if name.startswith("/") or name.startswith("\\"):
        return False
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    if any(":" in part or "\\" in part for part in parts):
        return False
    return True


def _check_zip_names(infos):
    """Validate zip entry names; returns (file_names, dir_names)."""
    seen = set()
    file_names, dir_names = set(), set()
    for info in infos:
        name = info.filename
        is_dir = name.endswith("/")
        base = name[:-1] if is_dir else name
        if not base or not _safe_relpath(base):
            raise UnsafeArchiveEntry(f"archive entry escapes its root: {name!r}")
        if base in seen:
            raise MigrationError(f"duplicate archive entry: {base!r}")
        seen.add(base)
        (dir_names if is_dir else file_names).add(base)
    for base in seen:
        parts = base.split("/")
        for i in range(1, len(parts)):
            ancestor = "/".join(parts[:i])
            if ancestor in file_names:
                raise MigrationError(
                    f"archive entry {base!r} sits below non-directory entry "
                    f"{ancestor!r} - refusing unsafe extraction shape")
    return file_names, dir_names


# --- git plumbing -----------------------------------------------------------

def _git(repo_root, *args, check=True, literal_pathspecs=False):
    """Run one git command in `repo_root`; raise MigrationError on failure.

    literal_pathspecs=True passes git's GLOBAL --literal-pathspecs, so
    every path argument is matched exactly: without it a pathspec is
    a wildcard pattern, and an inventoried path that happens to carry
    glob characters would also untrack every tracked path it matches.
    """
    command = ["git"]
    if literal_pathspecs:
        command.append("--literal-pathspecs")
    command += ["-C", str(repo_root), *args]
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise MigrationError(
            f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result


def _git_lines(repo_root, *args):
    out = _git(repo_root, *args).stdout
    return [p for p in out.split("\0") if p]


# --- classification ---------------------------------------------------------

def _repo_root(repo_root):
    repo_root = Path(repo_root).resolve()
    if not repo_root.is_dir():
        raise MigrationError(f"not a directory: {repo_root}")
    if not (repo_root / ".git").exists():
        raise MigrationError(f"no .git under {repo_root} - refusing to migrate a non-repository")
    return repo_root


def _shows_lab_evidence(path):
    """Does an untracked root directory demonstrate lab evidence?

    Tracked source is its own evidence; a wholly untracked directory
    needs Terraform lab markers - a top-level .terraform directory or
    a top-level *.tf / *.tfvars / tfstate file - before the classifier
    calls it a lab. Documentation, scratch caches, and
    virtual-environment lookalikes stay unclassified and are refused
    rather than guessed into labs/.
    """
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if (entry.name == ".terraform"
                        and entry.is_dir(follow_symlinks=False)):
                    return True
                if not entry.is_file(follow_symlinks=False):
                    continue
                name = entry.name
                if (name.endswith((".tf", ".tfvars"))
                        or ".tfstate" in name):
                    return True
    except OSError:
        return False
    return False


def plan(repo_root: Path) -> Plan:
    """Classify the lab inventory of `repo_root` (see module docstring)."""
    repo_root = _repo_root(repo_root)
    tracked_tops = {}
    for path in _git_lines(repo_root, "ls-files", "-z"):
        top = path.split("/", 1)[0]
        tracked_tops.setdefault(top, []).append(path)

    labs_tracked, labs_untracked = [], []
    unclassified = []
    for name in sorted(os.listdir(repo_root)):
        if name in EXCLUDED_ROOTS:
            continue
        entry = repo_root / name
        if entry.is_symlink():
            raise MigrationError(
                f"unexpected root-level symlink {name!r} - refusing to guess its classification")
        if entry.is_dir():
            if name in tracked_tops:
                labs_tracked.append(name)
            elif _shows_lab_evidence(entry):
                labs_untracked.append(name)
            else:
                unclassified.append(name)
        elif not entry.is_file():
            raise MigrationError(
                f"unexpected root-level entry {name!r} - refusing to guess")
    if unclassified:
        raise MigrationError(
            "unclassified root directories (no tracked source, no "
            "Terraform lab evidence, not tooling/templates/tests/"
            "metadata): " + ", ".join(unclassified)
            + " - refusing to guess; review them explicitly before "
            "migrating")

    # every tracked top-level path must be explainable: an excluded
    # root, a root-level file, or a lab directory that exists on disk
    for top in tracked_tops:
        if top in EXCLUDED_ROOTS or top in labs_tracked:
            continue
        if (repo_root / top).is_file():
            continue
        raise MigrationError(
            f"tracked top-level path {top!r} has no working-tree directory - "
            "the inventory is unexpected; refusing rather than guessing")

    collisions = []
    for slug in labs_tracked + labs_untracked:
        destinations = (repo_root / "archives" / f"{slug}.zip",
                        repo_root / "archives" / f"{slug}.meta.json",
                        repo_root / "labs" / slug)
        if any(os.path.lexists(dest) for dest in destinations):
            collisions.append(slug)
    return Plan(sorted(labs_tracked), sorted(labs_untracked),
                sorted(set(collisions)))


def _plans_equal(left, right):
    return (left.tracked == right.tracked
            and left.untracked == right.untracked
            and left.collisions == right.collisions)


def preflight(repo_root: Path) -> dict:
    """Read-only inventory evidence: classification, counts, problems."""
    repo_root = _repo_root(repo_root)
    current = plan(repo_root)
    out = {"xattr_support": _XATTR is not None,
           "tracked": {}, "untracked": {},
           "collisions": list(current.collisions),
           "problems": []}
    if _XATTR is None:
        out["problems"].append(
            "extended-attribute support unavailable on this platform - "
            "preservation cannot be guaranteed and run() will refuse")
    for group in ("tracked", "untracked"):
        for slug in getattr(current, group):
            try:
                inventory = _inventory_tree(repo_root / slug)
            except MigrationError as e:
                out["problems"].append(f"{slug}: {e}")
                continue
            counts = {"files": 0, "dirs": 0, "symlinks": 0, "entries": 0,
                      "bytes": 0, "xattrs": 0}
            for rel, fact in inventory.items():
                counts["entries"] += 1
                counts["xattrs"] += len(fact["xattrs"])
                if fact["type"] == "file":
                    counts["files"] += 1
                    counts["bytes"] += (repo_root / slug / rel).stat().st_size
                elif fact["type"] == "dir":
                    counts["dirs"] += 1
                else:
                    counts["symlinks"] += 1
            out[group][slug] = counts
    return out


# --- archive writing and verification -----------------------------------------

def _exclusive_open(path):
    """Open for writing, refusing to touch anything that already exists."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as e:
        raise DestinationCollision(f"destination already exists: {path.name}") from e
    except OSError as e:
        raise MigrationError(f"cannot create {path.name!r}: {e}") from e
    return fd


def _write_archive(zip_path, lab_root, inventory, created):
    fd = _exclusive_open(zip_path)
    created.append(zip_path)
    with os.fdopen(fd, "wb") as fh:
        with zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as archive:
            for rel in sorted(inventory):
                fact = inventory[rel]
                if fact["type"] != "file":
                    continue
                info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = (0o100000 | fact["mode"]) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(lab_root / rel, "rb") as src:
                    archive.writestr(info, src.read())
    os.chmod(zip_path, 0o600)


def _write_sidecar(sidecar_path, slug, inventory, created):
    data = {"version": SIDECAR_VERSION, "kind": SIDECAR_KIND,
            "slug": slug, "entries": inventory}
    blob = json.dumps(data, sort_keys=True, indent=1).encode()
    fd = _exclusive_open(sidecar_path)
    created.append(sidecar_path)
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)
    os.chmod(sidecar_path, 0o600)


def verify_archive(zip_path: Path, hashes: dict | None = None) -> None:
    """Reopen and verify an archive before any source is retired."""
    zip_path = Path(zip_path)
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as e:
        raise MigrationError(f"cannot open archive {zip_path.name!r}: {e}") from e
    with archive:
        file_names, _dir_names = _check_zip_names(archive.infolist())
        if hashes is None:
            return
        if file_names != set(hashes):
            raise MigrationError(
                f"archive entry set does not match the expected inventory "
                f"({len(file_names)} entries in the archive, "
                f"{len(hashes)} expected)")
        for rel, expected in sorted(hashes.items()):
            actual = hashlib.sha256(archive.read(rel)).hexdigest()
            if actual != expected:
                raise MigrationError(f"hash mismatch for archive entry {rel!r}")


def _verify_archive_against(zip_path, sidecar_path, inventory):
    """Full verification of an archive, against its complete record.

    Used both right after writing and immediately before EVERY source
    retirement: the zip's payload entry set, content hashes, and the
    full stored permission bits - including the setuid/setgid/sticky
    group - are checked, AND the sidecar - the only
    copy of the entry set, types, modes, symlink targets, extended
    attributes, and lab-root metadata - is reloaded, validated with
    the same structural rules restore_archive enforces, and compared
    entry-for-entry against the original inventory.
    """
    verify_archive(zip_path, _file_hashes(inventory))
    recorded = _load_sidecar(sidecar_path)
    # the reloaded sidecar is validated with restore's rules BEFORE
    # the comparison: retirement must never depend on a record that
    # restore_archive would refuse
    _validate_entries(recorded, zip_path.name)
    if recorded != inventory:
        raise MigrationError(
            f"sidecar round-trip mismatch for {zip_path.name!r}")
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            stored_mode = (info.external_attr >> 16) & 0o7777
            if inventory[info.filename]["mode"] != stored_mode:
                raise MigrationError(
                    f"permission bits mismatch for {info.filename!r} in "
                    f"{zip_path.name!r}")


# --- sidecar validation (restore side) -------------------------------------

def _load_sidecar(sidecar_path):
    try:
        raw = sidecar_path.read_text()
    except OSError as e:
        raise MigrationError(
            f"no readable preservation sidecar next to the archive "
            f"({sidecar_path.name!r}): a zip alone cannot rebuild links, "
            f"modes, or metadata - {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise MigrationError(f"sidecar {sidecar_path.name!r} is not valid JSON: {e}") from e
    if (not isinstance(data, dict)
            or data.get("version") != SIDECAR_VERSION
            or data.get("kind") != SIDECAR_KIND
            or not isinstance(data.get("entries"), dict)):
        raise MigrationError(
            f"sidecar {sidecar_path.name!r} is not a recognized "
            "gcp-lab-002 preservation record - refusing")
    return data["entries"]


def _validate_entries(entries, source_name):
    """Structural safety checks on preservation entries before use.

    These are the exact rules restore_archive enforces on a sidecar,
    applied to every inventory too: every path must be safe, entry
    names must not collide case-insensitively or by Unicode
    normalization (a tree a case-insensitive or
    normalization-insensitive filesystem cannot recreate
    faithfully), every
    parent must be a recorded directory (a file or symlink parent is
    the extract-through-symlink shape), and every symlink's full
    target chain - resolved against the complete link map, so chained
    escapes and cycles are caught, not just single-level ones - must
    stay inside the destination.
    """
    if entries.get("", {}).get("type") != "dir":
        raise MigrationError(
            f"{source_name}: sidecar has no lab-root directory entry")
    links = {}
    folded_names = {}
    for rel, fact in entries.items():
        if rel != "" and not _safe_relpath(rel):
            raise UnsafeArchiveEntry(
                f"{source_name}: entry name is not a safe relative "
                f"path: {rel!r}")
        if rel != "":
            folded = _fs_equivalence_key(rel)
            if folded in folded_names:
                raise MigrationError(
                    f"{source_name}: entries {folded_names[folded]!r} and "
                    f"{rel!r} differ only by case (or Unicode "
                    "normalization) - refusing to guess what a "
                    "case-insensitive filesystem would recreate")
            folded_names[folded] = rel
        if not isinstance(fact, dict) or fact.get("type") not in (
                "dir", "file", "symlink"):
            raise MigrationError(
                f"{source_name}: entry {rel!r} has no usable type")
        if fact["type"] == "symlink":
            target = fact.get("target")
            if not isinstance(target, str) or not target:
                raise MigrationError(
                    f"{source_name}: symlink entry {rel!r} has no target")
            if os.path.isabs(target):
                raise UnsafeArchiveEntry(
                    f"{source_name}: symlink entry {rel!r} has an "
                    "absolute target")
            links[rel] = target
    for rel, target in links.items():
        if _resolve_within(posixpath.join(posixpath.dirname(rel) or ".",
                                          target), links) is None:
            raise UnsafeArchiveEntry(
                f"symlink entry {rel!r} escapes the destination through "
                "a link chain (or loops)")
    for rel in entries:
        parts = rel.split("/")
        for i in range(1, len(parts)):
            ancestor = "/".join(parts[:i])
            if ancestor not in entries:
                raise MigrationError(
                    f"{source_name}: sidecar is missing parent entry "
                    f"{ancestor!r} of {rel!r} - incomplete metadata")
            if entries[ancestor]["type"] != "dir":
                raise MigrationError(
                    f"{source_name}: entry {rel!r} sits below "
                    f"non-directory {ancestor!r} - refusing unsafe "
                    "extraction shape")


# --- restore ----------------------------------------------------------------

def _facts_match(recorded, actual, ignore_dir_modes=False):
    if recorded["type"] != actual.get("type"):
        return False
    if recorded["type"] == "dir":
        if not ignore_dir_modes and recorded["mode"] != actual.get("mode"):
            return False
        return recorded["xattrs"] == actual.get("xattrs")
    if recorded["type"] == "file":
        return (recorded["mode"] == actual.get("mode")
                and recorded["sha256"] == actual.get("sha256")
                and recorded["xattrs"] == actual.get("xattrs"))
    # symlink
    return (recorded["mode"] == actual.get("mode")
            and recorded["target"] == actual.get("target")
            and recorded["xattrs"] == actual.get("xattrs"))


def restore_archive(zip_path: Path, dest: Path) -> None:
    """Rebuild a lab tree from a verified archive (see module docstring)."""
    zip_path = Path(zip_path)
    dest = Path(dest)
    if not zip_path.name.endswith(".zip"):
        raise MigrationError(f"not a .zip archive: {zip_path.name!r}")
    if _XATTR is None:
        raise MigrationError(
            "extended-attribute support unavailable - restoration cannot "
            "be verified on this platform")
    sidecar_path = zip_path.with_suffix(".meta.json")
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as e:
        raise MigrationError(f"cannot open archive {zip_path.name!r}: {e}") from e
    built = False
    try:
        # entry-name safety first: an escaping name is refused before
        # any sidecar requirement or destination check
        file_names, _ = _check_zip_names(archive.infolist())
        entries = _load_sidecar(sidecar_path)
        _validate_entries(entries, zip_path.name)
        expected_files = {rel for rel, fact in entries.items()
                          if fact["type"] == "file"}
        if file_names != expected_files:
            raise MigrationError(
                f"archive payload does not match its preservation metadata "
                f"({len(file_names)} payload entries, {len(expected_files)} "
                "recorded files)")
        for rel in sorted(file_names):
            actual = hashlib.sha256(archive.read(rel)).hexdigest()
            if actual != entries[rel]["sha256"]:
                raise MigrationError(f"hash mismatch for archive entry {rel!r}")

        # everything is validated BEFORE the destination is touched
        if os.path.lexists(dest):
            raise MigrationError(f"destination already exists: {dest}")
        if not dest.parent.is_dir() or dest.parent.is_symlink():
            raise MigrationError(
                f"destination parent must be an existing real directory: "
                f"{dest.parent}")

        os.mkdir(dest)
        built = True
        # directories first (default modes), then files, so nothing is
        # ever written below a restrictive mode; recorded dir modes are
        # applied at the very end, deepest first
        for rel in sorted(entries, key=lambda r: (r.count("/"), r)):
            if entries[rel]["type"] == "dir" and rel != "":
                os.mkdir(dest / rel)
        for rel in sorted(entries):
            fact = entries[rel]
            if fact["type"] != "file":
                continue
            target = dest / rel
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(archive.read(rel))
            # attributes while the file is still owner-writable; the
            # recorded mode (e.g. 0444) comes last
            _apply_xattrs(target, fact["xattrs"])
            os.chmod(target, fact["mode"])
        # directory metadata while permissions are still default;
        # the lab root's own xattrs are part of the record too
        for rel in sorted(entries):
            fact = entries[rel]
            if fact["type"] == "dir":
                _apply_xattrs(dest / rel, fact["xattrs"])
        # symlinks last: files and directories are complete, so no
        # later write can ever travel through a link
        for rel in sorted(entries):
            fact = entries[rel]
            if fact["type"] != "symlink":
                continue
            os.symlink(fact["target"], dest / rel)
            link_mode = stat.S_IMODE(os.lstat(dest / rel).st_mode)
            if link_mode != fact["mode"]:
                if hasattr(os, "lchmod"):
                    os.lchmod(dest / rel, fact["mode"])
                else:
                    raise MigrationError(
                        f"cannot restore permission bits of symlink {rel!r} "
                        "on this platform")
            if fact["xattrs"]:
                _apply_xattrs(dest / rel, fact["xattrs"],
                              follow_symlinks=False)
        actual = _inventory_tree(dest)
        for rel, fact in entries.items():
            if not _facts_match(fact, actual.get(rel, {}),
                                 ignore_dir_modes=True):
                raise MigrationError(
                    f"restored entry {rel!r} does not match its record - "
                    "refusing to report success")
        # only now the recorded directory modes, deepest first, and
        # finally the lab root's own mode
        dir_rels = sorted((rel for rel, fact in entries.items()
                           if fact["type"] == "dir" and rel != ""),
                          key=lambda r: -r.count("/"))
        for rel in dir_rels:
            os.chmod(dest / rel, entries[rel]["mode"])
        os.chmod(dest, entries[""]["mode"])
    except MigrationError:
        if built:
            _cleanup_partial(dest)
        raise
    except OSError as e:
        if built:
            _cleanup_partial(dest)
        raise MigrationError(f"restore failed: {e}") from e
    finally:
        archive.close()


def _cleanup_partial(dest):
    try:
        _force_rmtree(dest)
    except Exception as e:  # cleanup must not mask the original failure
        sys.stderr.write(f"warning: could not remove partial restore "
                         f"destination {dest}: {e}\n")


# --- the migration itself ----------------------------------------------------

def _rmtree_step(func, failed, exc, tree_parent):
    """One rmtree error step: make `failed` removable, then retry.

    The failing entry is chmod'd, and so is its parent when the
    parent sits INSIDE the doomed tree (a 0500 directory containing
    files blocks unlink with its own mode, not the child's) - the
    parent of the tree itself is never touched. If the retry still
    fails, the ORIGINAL error wins, so rmtree's failure semantics
    are preserved.
    """
    try:
        try:
            os.chmod(failed, 0o700)
        except OSError:
            pass
        parent = os.path.dirname(failed)
        if parent and os.path.abspath(parent) != tree_parent:
            try:
                os.chmod(parent, 0o700)
            except OSError:
                pass
        func(failed)
    except OSError:
        raise exc


def _force_rmtree(path):
    """Remove a tree even when lab subdirectories carry mode 0500/0000.

    shutil.rmtree grew `onexc` in Python 3.12; the documented baseline
    is Python 3.10, so the error hook is chosen for the running
    interpreter: 3.12+ uses onexc(func, path, exc); 3.10/3.11 use
    onerror(func, path, exc_info) with the exception instance at
    exc_info[1]. Failing on 3.10/3.11 here would break the deletion
    step late - after index changes and moves - which is exactly the
    failure shape this helper must not have. The interpreter check is
    a module constant so the 3.10 branch stays exercisable anywhere.
    """
    path = os.fspath(path)
    tree_parent = os.path.dirname(os.path.abspath(path))

    def _onexc(func, failed, exc):
        _rmtree_step(func, failed, exc, tree_parent)

    if _PY312:
        shutil.rmtree(path, onexc=_onexc)
    else:
        def _onerror(func, failed, exc_info):
            _rmtree_step(func, failed, exc_info[1], tree_parent)
        shutil.rmtree(path, onerror=_onerror)


def _cleanup_created(created):
    """Remove exactly the artifacts this attempt created - nothing else."""
    for path in reversed(created):
        try:
            os.unlink(path)
        except OSError as e:
            sys.stderr.write(
                f"warning: could not remove attempt artifact {path}: {e}\n")


def _ensure_real_dir(path):
    """labs/ and archives/ must be real directories, never symlinks."""
    if os.path.lexists(path):
        if path.is_symlink() or not path.is_dir():
            raise MigrationError(
                f"{path.name}/ must be a real directory, not a symlink or "
                "file - a linked collection could move lab trees or "
                "archives outside the repository")
    else:
        path.mkdir()
    return path


def _untrack(repo_root, paths):
    """Scoped index-only removal of exactly the inventoried lab paths.

    --literal-pathspecs makes the paths literal: a pathspec is a
    wildcard pattern by default, so an inventoried path carrying
    glob characters (a lab directory named `t*`) would untrack every
    tracked path it matches - silently staging deletions for
    unrelated tool files.
    """
    if not paths:
        return
    for start in range(0, len(paths), 200):
        chunk = paths[start:start + 200]
        _git(repo_root, "rm", "--cached", "--", *chunk,
             literal_pathspecs=True)


def _verify_ignore_rules(repo_root, current):
    """The managed ignore rules must be active before anything is written.

    Every archive payload, sidecar, and moved lab must come back
    ignored from `git check-ignore --no-index`, and the .gitkeep
    placeholders must stay trackable. Absent or wrong rules are a
    refusal - the helper never edits .gitignore to fix them itself.
    """
    def ignored(rel):
        result = _git(repo_root, "check-ignore", "--no-index", "-q",
                      "--", rel, check=False)
        if result.returncode not in (0, 1):
            raise MigrationError(
                f"git check-ignore failed for {rel!r}: "
                f"{result.stderr.strip()}")
        return result.returncode == 0

    problems = []
    for slug in current.tracked:
        for rel in (f"archives/{slug}.zip", f"archives/{slug}.meta.json"):
            if not ignored(rel):
                problems.append(f"{rel} would be tracked")
    for slug in current.untracked:
        if not ignored(f"labs/{slug}"):
            problems.append(f"labs/{slug} would be tracked")
    for placeholder in ("labs/.gitkeep", "archives/.gitkeep"):
        if ignored(placeholder):
            problems.append(f"{placeholder} would be ignored")
    if problems:
        raise MigrationError(
            "the managed-directory ignore rules are not active as "
            "required: " + "; ".join(problems)
            + " - fix .gitignore and re-run; refusing to migrate "
            "without them")


def run(repo_root: Path, migration_plan: Plan | None = None) -> Report:
    """Execute the one-time migration (see module docstring)."""
    repo_root = _repo_root(repo_root)
    if _XATTR is None:
        raise MigrationError(
            "extended-attribute support unavailable on this platform - "
            "refusing to run a migration whose preservation cannot be "
            "verified")
    current = plan(repo_root)
    if migration_plan is not None and not _plans_equal(migration_plan, current):
        raise MigrationError(
            "the inventory changed since this plan was made - re-run "
            "plan()/preflight and review before running")
    if current.collisions:
        raise DestinationCollision(
            "destination already exists for: " + ", ".join(current.collisions)
            + " - stopping before any source or index change")

    lab_slugs = set(current.tracked) | set(current.untracked)
    staged = _git_lines(repo_root, "diff", "--cached", "--name-only", "-z")
    conflicting = sorted(p for p in staged if p.split("/", 1)[0] in lab_slugs)
    if conflicting:
        raise StagedLabConflict(
            "staged changes on inventoried lab paths (refusing to force "
            "index removal): " + ", ".join(conflicting))

    _verify_ignore_rules(repo_root, current)

    tracked_by_slug = {}
    for path in _git_lines(repo_root, "ls-files", "-z"):
        top = path.split("/", 1)[0]
        if top in current.tracked:
            tracked_by_slug.setdefault(top, []).append(path)

    # inventory EVERY lab - tracked and untracked alike - before a
    # single artifact is written, the index is touched, or anything
    # moves. An unsupported entry anywhere (even inside a lab that
    # would only move) is a refusal with the index, sources, and
    # artifacts untouched; missing tracked sources are caught here
    # too, before any archive exists.
    inventories = {}
    for slug in sorted(lab_slugs):
        inventory = _inventory_tree(repo_root / slug)
        if slug in current.tracked:
            missing = sorted(p[len(slug) + 1:]
                             for p in tracked_by_slug.get(slug, [])
                             if p[len(slug) + 1:] not in inventory)
            if missing:
                raise MigrationError(
                    f"{slug}: tracked paths missing from the working tree "
                    f"({len(missing)} of them, e.g. {missing[0]!r}) - the "
                    "inventory is unexpected; refusing")
        inventories[slug] = inventory

    labs_dir = _ensure_real_dir(repo_root / "labs")
    archives_dir = _ensure_real_dir(repo_root / "archives")
    for slug in sorted(lab_slugs):
        destinations = (archives_dir / f"{slug}.zip",
                        archives_dir / f"{slug}.meta.json",
                        labs_dir / slug)
        if any(os.path.lexists(dest) for dest in destinations):
            raise DestinationCollision(
                f"destination for {slug!r} appeared after planning - "
                "stopping before any source or index change")

    created = []          # archives/sidecars this attempt owns
    moved, retired = [], []
    retirement_started = False
    mutated = False       # index or any source has been touched
    try:
        # phase 1: archive every tracked lab and verify it, before any
        # index change, move, or deletion
        for slug in current.tracked:
            source = repo_root / slug
            inventory = inventories[slug]
            _write_archive(archives_dir / f"{slug}.zip", source,
                           inventory, created)
            _write_sidecar(archives_dir / f"{slug}.meta.json", slug,
                           inventory, created)
            _verify_archive_against(archives_dir / f"{slug}.zip",
                                   archives_dir / f"{slug}.meta.json",
                                   inventory)
            if _inventory_tree(source) != inventory:
                raise MigrationError(
                    f"{slug}: the source changed while it was being "
                    "archived - stopping; nothing will be deleted")

        # every archive verified - the retirement phase begins. From
        # here on, verified artifacts are retained on failure and the
        # error reports progress instead.
        retirement_started = True

        # phase 2: untrack exactly the inventoried lab paths - but only
        # after re-verifying every tracked source against the snapshot
        # its archive was built from
        for slug in current.tracked:
            if _inventory_tree(repo_root / slug) != inventories[slug]:
                raise MigrationError(
                    f"{slug}: the source changed before untracking - "
                    "stopping; nothing will be deleted")
        lab_tracked_paths = sorted(
            path for slug in current.tracked
            for path in tracked_by_slug.get(slug, []))
        mutated = True
        _untrack(repo_root, lab_tracked_paths)

        # phase 3: move wholly untracked lab trees, verifying each move
        for slug in current.untracked:
            source = repo_root / slug
            destination = labs_dir / slug
            if os.path.lexists(destination):
                raise DestinationCollision(
                    f"destination appeared for {slug!r} during the run - "
                    "stopping without moving it")
            if _inventory_tree(source) != inventories[slug]:
                raise MigrationError(
                    f"{slug}: the source changed before it was moved - "
                    "stopping; the verified destination at "
                    f"{destination} is not touched")
            try:
                os.rename(source, destination)
            except OSError as e:
                raise MigrationError(
                    f"move failed for {slug!r}: {e}; already moved: "
                    + (", ".join(sorted(moved)) or "none")) from e
            if _inventory_tree(destination) != inventories[slug]:
                raise MigrationError(
                    f"{slug}: the moved tree does not match its snapshot - "
                    f"the verified destination at {destination} is retained")
            moved.append(slug)

        # phase 4: retire archived sources, one by one. Immediately
        # before EACH deletion the source is re-inventoried AND the
        # complete archive+sidecar is revalidated against the original
        # inventory - the sidecar is the only copy of symlink targets,
        # directory modes, and extended attributes
        for slug in current.tracked:
            source = repo_root / slug
            if _inventory_tree(source) != inventories[slug]:
                raise MigrationError(
                    f"{slug}: the source changed before retirement - "
                    "stopping without deleting it; already retired: "
                    + (", ".join(sorted(retired)) or "none"))
            _verify_archive_against(archives_dir / f"{slug}.zip",
                                   archives_dir / f"{slug}.meta.json",
                                   inventories[slug])
            _force_rmtree(source)
            retired.append(slug)

        return Report(archived=sorted(current.tracked),
                      moved=sorted(moved))
    except MigrationError as e:
        if not retirement_started:
            _cleanup_created(created)
            raise
        raise type(e)(_progress_message(str(e), moved, retired)) from e
    except OSError as e:
        if not retirement_started:
            _cleanup_created(created)
            raise MigrationError(str(e)) from e
        raise MigrationError(
            _progress_message(str(e), moved, retired)) from e
    except KeyboardInterrupt:
        # the operator's Ctrl-C is a decision, not a migration
        # failure: it propagates unchanged, never wrapped as one.
        # Before the index or any source is touched the half-written
        # attempt is removed so a retry starts from a pristine tree;
        # once anything is mutated the verified artifacts stay and
        # the operator gets the progress state on stderr.
        if not mutated:
            _cleanup_created(created)
            print("migration interrupted before any index or source "
                  "change; partial artifacts removed - safe to retry",
                  file=sys.stderr)
        else:
            print("migration interrupted "
                  + _progress_message("after index or source changes",
                                      moved, retired),
                  file=sys.stderr)
        raise
    except Exception as e:
        # nothing unexpected may escape unwrapped: a late failure must
        # stay auditable with its progress context, never surface as
        # an uncaught exception (e.g. corrupt sidecar JSON)
        if not retirement_started:
            _cleanup_created(created)
            raise MigrationError(f"unexpected failure: {e}") from e
        raise MigrationError(
            _progress_message(f"unexpected failure: {e}", moved, retired)) from e


def _progress_message(message, moved, retired):
    """What already succeeded, so a late failure stays auditable."""
    return (f"{message} [progress retained: moved="
            + (", ".join(sorted(moved)) or "none")
            + "; sources retired=" + (", ".join(sorted(retired)) or "none")
            + "; verified archives retained]")


# --- command line ---------------------------------------------------------------

def _print_plan(current):
    print(f"tracked labs ({len(current.tracked)}): "
          + (", ".join(current.tracked) or "none"))
    print(f"untracked labs ({len(current.untracked)}): "
          + (", ".join(current.untracked) or "none"))
    print(f"destination collisions ({len(current.collisions)}): "
          + (", ".join(current.collisions) or "none"))


def _print_preflight(pre):
    support = "available" if pre["xattr_support"] else "UNAVAILABLE"
    print(f"extended-attribute support: {support}")
    for group in ("tracked", "untracked"):
        print(f"{group} labs ({len(pre[group])}):")
        for slug in sorted(pre[group]):
            counts = pre[group][slug]
            print(f"  {slug}: {counts['files']} files, {counts['dirs']} dirs, "
                  f"{counts['symlinks']} symlinks, {counts['bytes']} bytes, "
                  f"{counts['xattrs']} xattrs "
                  f"({counts['entries']} entries total)")
    print(f"destination collisions ({len(pre['collisions'])}): "
          + (", ".join(pre["collisions"]) or "none"))
    print(f"problems ({len(pre['problems'])}):")
    for problem in pre["problems"]:
        print(f"  {problem}")


def _main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="migrate_002.py",
        description="One-time 002 lab-directory migration helper. plan and "
                    "preflight are read-only; run performs the migration "
                    "and requires an explicit --yes.")
    parser.add_argument("--repo", default=".", help="repository root (default: cwd)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="classify the lab inventory (read-only)")
    commands.add_parser("preflight",
                        help="full read-only inventory with counts and problems")
    run_cmd = commands.add_parser("run", help="execute the migration")
    run_cmd.add_argument("--yes", action="store_true", required=True,
                         help="explicit confirmation required by run")
    restore_cmd = commands.add_parser(
        "restore", help="rebuild a lab tree from a verified archive + sidecar")
    restore_cmd.add_argument("zip_path")
    restore_cmd.add_argument("dest")
    args = parser.parse_args(argv)

    repo = Path(args.repo)
    try:
        if args.command == "plan":
            _print_plan(plan(repo))
        elif args.command == "preflight":
            _print_preflight(preflight(repo))
        elif args.command == "run":
            report = run(repo)
            print(f"archived ({len(report.archived)}): "
                  + (", ".join(report.archived) or "none"))
            print(f"moved ({len(report.moved)}): "
                  + (", ".join(report.moved) or "none"))
            print("the lab deletions are staged in the index; commit them "
                  "when ready")
        elif args.command == "restore":
            restore_archive(Path(args.zip_path), Path(args.dest))
            print(f"restored {args.zip_path} into {args.dest}")
    except MigrationError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
