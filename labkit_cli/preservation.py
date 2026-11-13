"""The version-1 archive preservation core: entry safety, metadata
validation, and the verified restore labkit runs.

A lab archive is a PAIR: <name>.zip carries the regular-file payloads;
the sibling <name>.meta.json sidecar carries what a zip cannot - the
entry set and types, full permission bits including the
setuid/setgid/sticky group, symlink targets, extended attributes, and
the lab root's own metadata. The 002 migration wrote unnumbered
<slug>.zip pairs; the archive command writes numbered <slug>--NNN.zip
pairs. Both are version-1 records that differ only in file name, and
the sidecar is always the zip's name with .zip replaced by
.meta.json.

restore_archive(zip_path, dest) rebuilds the recorded tree into
`dest`. The rules it upholds:

* Nothing is written before the WHOLE pair validates: zip entry
  names (no absolute paths, no `..`, no duplicates, nothing below a
  non-directory entry), the sidecar's structure AND every entry's
  field types (modes are integers in range, file hashes are hex,
  extended-attribute values are hex, symlink targets are non-empty
  and relative), the payload set agreement, and every payload's
  sha256. Payload members are read in bounded chunks - never as
  whole-entry reads that a huge archive member could turn into
  memory exhaustion - and ZIP64-formatted pairs read the same way.
* A destination that exists in ANY form - file, directory, or a
  dangling symlink - is refused, never merged or written through.
* Build order keeps every write inside plain directories:
  directories first with owner-only modes, then files (streamed), then
  directory metadata, then symlinks last - nothing is ever
  extracted through a link - and the rebuilt tree is re-inventoried
  and compared against the record before the recorded file and directory
  modes (directories deepest first, the lab root last) are applied.
* On ANY failure or interrupt - including Ctrl-C - exactly the
  partial destination this attempt created is removed; a remnant
  that could not be removed is reported on stderr, the original
  error still wins, and the input pair is never modified.

Symlink chains are resolved against the complete link map before
anything is written, so chained escapes and cycles are caught, not
just single-level ones, and names that collide only by case or
Unicode normalization are refused because a case-insensitive
filesystem could not recreate such a tree faithfully. Extended
attributes are read and written no-follow for symlinks (the link's
OWN attributes, never its referent's), and every attribute call
must succeed: a platform that cannot read or write an attribute in
the required mode is a refusal, never a silent empty map or a
dropped value.
"""

import ctypes
import hashlib
import json
import os
import posixpath
import shutil
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path
from typing import TypeGuard

# shutil.rmtree's onexc= keyword exists from Python 3.12; the
# documented baseline (3.10/3.11) only knows onerror= - see
# _force_rmtree. A module constant so the 3.10 branch stays
# exercisable in tests on newer interpreters.
_PY312 = sys.version_info >= (3, 12)

# every zip payload member is read and written in pieces of this
# size - bounded, streamed, whole entries never materialize in memory
READ_CHUNK = 1 << 20

# Archives carry the file payloads; the sidecar carries what a zip
# cannot (symlink targets, directory/root modes, xattrs, entry set).
SIDECAR_VERSION = 1
SIDECAR_KIND = "gcp-lab-002-archive-metadata"


class PreservationError(Exception):
    """Any failed or refused preservation step (base class)."""


class UnsafeArchiveEntry(PreservationError):
    """An archive entry escapes its root (absolute path or `..`)."""


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
        raise PreservationError(
            f"this platform exposes no {name}; a no-follow "
            "extended-attribute operation would silently follow the "
            "symlink - refusing") from None


def _xattr_list(path, follow_symlinks=True):
    """{name: hex value} for one entry. Requires _XATTR support.

    A no-follow read reports the entry's OWN attributes - a symlink's,
    not its referent's. Every failed call raises PreservationError: a
    platform that cannot read attributes is a refusal, never a silent
    empty map. A genuine zero-length attribute value is real data and
    is recorded as "".
    """
    if _XATTR is None:
        raise PreservationError("extended-attribute support unavailable")
    if _XATTR == "os":
        try:
            names = getattr(os, "listxattr")(path, follow_symlinks=follow_symlinks)
            return {n: getattr(os, "getxattr")(path, n, follow_symlinks=follow_symlinks).hex()
                    for n in names}
        except OSError as e:
            raise PreservationError(f"cannot read extended attributes of {os.path.basename(path)!r}: {e}") from e
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
        raise PreservationError(f"listxattr failed on {os.path.basename(path)!r}: {os.strerror(err)}")
    if size == 0:
        return {}
    buf = ctypes.create_string_buffer(size + 1)
    size = (listxattr(path_b, buf, size, opts) if darwin
            else listxattr(path_b, buf, size))
    if size < 0:
        err = ctypes.get_errno()
        raise PreservationError(f"listxattr failed on {os.path.basename(path)!r}: {os.strerror(err)}")
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
            raise PreservationError(
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
            raise PreservationError(
                f"getxattr failed on {os.path.basename(path)!r}/"
                f"{os.fsdecode(name)}: {os.strerror(err)}")
        out[os.fsdecode(name)] = bytes(vbuf.raw[:got]).hex()
    return out


def _xattr_set(path, name, value_hex, follow_symlinks=True):
    if _XATTR is None:
        raise PreservationError("extended-attribute support unavailable")
    if _XATTR == "os":
        getattr(os, "setxattr")(path, name, bytes.fromhex(value_hex),
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
        raise PreservationError(
            f"cannot restore extended attribute {name!r} on "
            f"{os.path.basename(path)!r}: {os.strerror(err)}")


def _xattr_remove(path, name, follow_symlinks=True):
    if _XATTR is None:
        raise PreservationError("extended-attribute support unavailable")
    if _XATTR == "os":
        getattr(os, "removexattr")(path, name, follow_symlinks=follow_symlinks)
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
        raise PreservationError(
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
            for chunk in iter(lambda: fh.read(READ_CHUNK), b""):
                digest.update(chunk)
    except OSError as e:
        raise PreservationError(
            f"unreadable file {rel!r} - refusing to verify a partial "
            f"inventory ({e})") from e
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
    preservation contract verifies. Symlinked directories are never
    descended into; special files (FIFOs, sockets, devices) and
    links whose target chain leaves the lab root are refused instead
    of guessed at. The finished inventory is validated with the same
    structural rules the restore path enforces on a sidecar.
    """
    lab_root = Path(lab_root)
    if lab_root.is_symlink() or not lab_root.is_dir():
        raise PreservationError(f"not a real directory: {lab_root.name}")
    try:
        root_st = lab_root.lstat()
        facts = {"": {"type": "dir", "mode": stat.S_IMODE(root_st.st_mode),
                      "xattrs": _xattr_list(lab_root)}}
    except OSError as e:
        raise PreservationError(f"cannot inventory {lab_root.name!r}: {e}") from e
    pending = [""]
    while pending:
        rel = pending.pop()
        dir_path = lab_root if rel == "" else lab_root / rel
        try:
            with os.scandir(dir_path) as it:
                children = sorted(it, key=lambda entry: entry.name)
        except OSError as e:
            where = rel or "."
            raise PreservationError(f"cannot list {where!r} in {lab_root.name!r}: {e}") from e
        for child in children:
            child_rel = child.name if rel == "" else f"{rel}/{child.name}"
            try:
                st = child.stat(follow_symlinks=False)
            except OSError as e:
                raise PreservationError(f"cannot stat {child_rel!r} in {lab_root.name!r}: {e}") from e
            if stat.S_ISLNK(st.st_mode):
                try:
                    target = os.readlink(child.path)
                except OSError as e:
                    raise PreservationError(f"cannot read symlink {child_rel!r}: {e}") from e
                if os.path.isabs(target):
                    raise PreservationError(
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
                raise PreservationError(
                    f"unsupported entry type at {child_rel!r} "
                    "(special files cannot be preserved) - refusing")

    # after the walk: every symlink's full chain must resolve inside
    # the lab root against the complete link map - a two-link escape
    # or a cycle is invisible to a single-level lexical check
    links = {rel: fact["target"] for rel, fact in facts.items()
             if fact["type"] == "symlink"}
    for rel, target in links.items():
        if _resolve_within(posixpath.join(posixpath.dirname(rel) or ".",
                                          target), links) is None:
            raise PreservationError(
                f"symlink {rel!r} escapes its lab root through a link "
                "chain (or loops) - refusing")
    _validate_entries(facts, lab_root.name)
    return facts


# --- archive entry names --------------------------------------------------

def _filesystem_text(value: object) -> TypeGuard[str]:
    """A nonempty string the filesystem can represent without truncation."""
    if not isinstance(value, str) or not value or "\0" in value:
        return False
    try:
        os.fsencode(value)
    except UnicodeError:
        return False
    return True


def _safe_relpath(name):
    """True when an archive entry name stays inside the archive root."""
    if not _filesystem_text(name):
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
        name = info.orig_filename
        if name != info.filename:
            raise UnsafeArchiveEntry(f"archive entry name was altered: {name!r}")
        is_dir = name.endswith("/")
        base = name[:-1] if is_dir else name
        if not base or not _safe_relpath(base):
            raise UnsafeArchiveEntry(f"archive entry escapes its root: {name!r}")
        if is_dir and info.file_size != 0:
            raise PreservationError(f"archive directory has a payload: {name!r}")
        if base in seen:
            raise PreservationError(f"duplicate archive entry: {base!r}")
        seen.add(base)
        (dir_names if is_dir else file_names).add(base)
    for base in seen:
        parts = base.split("/")
        for i in range(1, len(parts)):
            ancestor = "/".join(parts[:i])
            if ancestor in file_names:
                raise PreservationError(
                    f"archive entry {base!r} sits below non-directory entry "
                    f"{ancestor!r} - refusing unsafe extraction shape")
    return file_names, dir_names


# --- sidecar validation -----------------------------------------------------

_HEX_DIGITS = frozenset("0123456789abcdef")


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PreservationError(f"duplicate sidecar field: {key!r}")
        result[key] = value
    return result


def _load_sidecar(sidecar_path):
    try:
        raw = sidecar_path.read_text()
    except (OSError, UnicodeError) as e:
        raise PreservationError(
            f"no readable preservation sidecar next to the archive "
            f"({sidecar_path.name!r}): a zip alone cannot rebuild links, "
            f"modes, or metadata - {e}") from e
    try:
        data = json.loads(raw, object_pairs_hook=_unique_json_object)
    except json.JSONDecodeError as e:
        raise PreservationError(f"sidecar {sidecar_path.name!r} is not valid JSON: {e}") from e
    if (not isinstance(data, dict)
            or type(data.get("version")) is not int
            or data.get("version") != SIDECAR_VERSION
            or data.get("kind") != SIDECAR_KIND
            or not isinstance(data.get("entries"), dict)):
        raise PreservationError(
            f"sidecar {sidecar_path.name!r} is not a recognized "
            "version-1 preservation record - refusing")
    return data["entries"]


def _validate_entries(entries, source_name):
    """Full structural and type validation of preservation entries.

    These are the exact rules restore enforces on a sidecar, applied
    BEFORE the first write: every path must be safe, entry names
    must not collide case-insensitively or by Unicode normalization,
    every entry's facts must be a mapping with a usable type, an
    in-range integer mode, and a mapping of hex-valued attributes,
    files must carry a valid sha256, symlinks a non-empty relative
    target, every parent must be a recorded directory (a file or
    symlink parent is the extract-through-symlink shape), and every
    symlink's full target chain - resolved against the complete link
    map, so chained escapes and cycles are caught - must stay inside
    the destination. A record that fails any of these is refused
    whole, never partially extracted.
    """
    root_fact = entries.get("")
    if not isinstance(root_fact, dict) or root_fact.get("type") != "dir":
        raise PreservationError(
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
                raise PreservationError(
                    f"{source_name}: entries {folded_names[folded]!r} and "
                    f"{rel!r} differ only by case (or Unicode "
                    "normalization) - refusing to guess what a "
                    "case-insensitive filesystem would recreate")
            folded_names[folded] = rel
        if not isinstance(fact, dict) or fact.get("type") not in (
                "dir", "file", "symlink"):
            raise PreservationError(
                f"{source_name}: entry {rel!r} has no usable type")
        mode = fact.get("mode")
        if (isinstance(mode, bool) or not isinstance(mode, int)
                or not 0 <= mode <= 0o7777):
            raise PreservationError(
                f"{source_name}: entry {rel!r} has no usable permission "
                f"mode: {mode!r}")
        if not isinstance(fact.get("xattrs"), dict):
            raise PreservationError(
                f"{source_name}: entry {rel!r} has no extended-attribute "
                f"record: {fact.get('xattrs')!r}")
        for name, value in fact["xattrs"].items():
            if not _filesystem_text(name):
                raise PreservationError(
                    f"{source_name}: entry {rel!r} has a non-text "
                    f"attribute name: {name!r}")
            if (not isinstance(value, str)
                    or len(value) % 2 != 0
                    or set(value) - _HEX_DIGITS):
                raise PreservationError(
                    f"{source_name}: attribute {name!r} of entry {rel!r} "
                    f"has a non-hex value: {value!r}")
        if fact["type"] == "file":
            digest = fact.get("sha256")
            if (not isinstance(digest, str) or len(digest) != 64
                    or set(digest) - _HEX_DIGITS):
                raise PreservationError(
                    f"{source_name}: file entry {rel!r} has no valid "
                    f"sha256 record: {digest!r}")
        elif fact["type"] == "symlink":
            target = fact.get("target")
            if not _filesystem_text(target):
                raise PreservationError(
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
                raise PreservationError(
                    f"{source_name}: sidecar is missing parent entry "
                    f"{ancestor!r} of {rel!r} - incomplete metadata")
            if entries[ancestor]["type"] != "dir":
                raise PreservationError(
                    f"{source_name}: entry {rel!r} sits below "
                    f"non-directory {ancestor!r} - refusing unsafe "
                    "extraction shape")


# --- payload streaming ------------------------------------------------------

def _hash_payload(archive, rel):
    """sha256 of one zip member, read in bounded chunks."""
    digest = hashlib.sha256()
    with archive.open(rel) as payload:
        while True:
            chunk = payload.read(READ_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _write_payload(archive, rel, target):
    """Stream one zip member into `target` in bounded chunks.

    The destination file is created exclusively with owner-only
    permissions; its recorded mode is applied by the caller after
    the attribute work, while it is still owner-writable.
    """
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as out:
        with archive.open(rel) as payload:
            while True:
                chunk = payload.read(READ_CHUNK)
                if not chunk:
                    break
                out.write(chunk)


# --- restore ----------------------------------------------------------------

def _facts_match(recorded, actual):
    """Compare content and attributes before applying restrictive modes."""
    if recorded["type"] != actual.get("type"):
        return False
    if recorded["xattrs"] != actual.get("xattrs"):
        return False
    if recorded["type"] == "dir":
        return True
    if recorded["type"] == "file":
        return recorded["sha256"] == actual.get("sha256")
    # symlink
    return (recorded["mode"] == actual.get("mode")
            and recorded["target"] == actual.get("target"))


def restore_archive(zip_path: Path, dest: Path) -> None:
    """Rebuild a lab tree from a verified archive pair (see module
    docstring). `dest` must not exist in any form; on any failure or
    interrupt exactly the partial destination this attempt created
    is removed and any unremovable remnant is reported."""
    zip_path = Path(zip_path)
    dest = Path(dest)
    if not zip_path.name.endswith(".zip"):
        raise PreservationError(f"not a .zip archive: {zip_path.name!r}")
    if _XATTR is None:
        raise PreservationError(
            "extended-attribute support unavailable - restoration cannot "
            "be verified on this platform")
    sidecar_path = zip_path.with_suffix(".meta.json")
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as e:
        raise PreservationError(f"cannot open archive {zip_path.name!r}: {e}") from e
    built = False
    try:
        # entry-name safety first: an escaping name is refused before
        # any sidecar requirement or destination check
        file_names, dir_names = _check_zip_names(archive.infolist())
        entries = _load_sidecar(sidecar_path)
        _validate_entries(entries, zip_path.name)
        expected_files = {rel for rel, fact in entries.items()
                           if fact["type"] == "file"}
        expected_dirs = {rel for rel, fact in entries.items()
                         if fact["type"] == "dir" and rel}
        if not dir_names <= expected_dirs:
            raise PreservationError("archive directories do not match preservation metadata")
        if file_names != expected_files:
            raise PreservationError(
                f"archive payload does not match its preservation metadata "
                f"({len(file_names)} payload entries, {len(expected_files)} "
                "recorded files)")
        for rel in sorted(file_names):
            if _hash_payload(archive, rel) != entries[rel]["sha256"]:
                raise PreservationError(f"hash mismatch for archive entry {rel!r}")

        # everything is validated BEFORE the destination is touched
        if os.path.lexists(dest):
            raise PreservationError(f"destination already exists: {dest}")
        if not dest.parent.is_dir() or dest.parent.is_symlink():
            raise PreservationError(
                f"destination parent must be an existing real directory: "
                f"{dest.parent}")

        # Under the single-writer contract this absent destination is ours
        # once creation is attempted, even if mkdir creates it then aborts.
        # EEXIST is the exception: a collision never grants ownership.
        built = True
        try:
            os.mkdir(dest, 0o700)
        except FileExistsError:
            built = False
            raise
        # directories first (owner-only modes), then files, so nothing is
        # ever written below a restrictive mode; recorded dir modes are
        # applied at the very end, deepest first
        for rel in sorted(entries, key=lambda r: (r.count("/"), r)):
            if entries[rel]["type"] == "dir" and rel != "":
                os.mkdir(dest / rel, 0o700)
        for rel in sorted(entries):
            fact = entries[rel]
            if fact["type"] != "file":
                continue
            target = dest / rel
            _write_payload(archive, rel, target)
            # Keep files readable until the independent disk inventory;
            # recorded modes (including 0000) come after verification.
            _apply_xattrs(target, fact["xattrs"])
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
                    raise PreservationError(
                        f"cannot restore permission bits of symlink {rel!r} "
                        "on this platform")
            if fact["xattrs"]:
                _apply_xattrs(dest / rel, fact["xattrs"],
                              follow_symlinks=False)
        actual = _inventory_tree(dest)
        for rel, fact in entries.items():
            if not _facts_match(fact, actual.get(rel, {})):
                raise PreservationError(
                    f"restored entry {rel!r} does not match its record - "
                    "refusing to report success")
        # Files first, then directories deepest first, root last: a
        # recorded 0000 mode must not block verification or chmod below it.
        file_rels = sorted(rel for rel, fact in entries.items()
                           if fact["type"] == "file")
        dir_rels = sorted((rel for rel, fact in entries.items()
                            if fact["type"] == "dir" and rel != ""),
                           key=lambda r: -r.count("/"))
        for rel in file_rels + dir_rels + [""]:
            os.chmod(dest / rel, entries[rel]["mode"])
            if stat.S_IMODE(os.lstat(dest / rel).st_mode) != entries[rel]["mode"]:
                raise PreservationError(f"cannot restore permission bits of {rel!r}")
    except PreservationError:
        if built:
            _cleanup_partial(dest)
        raise
    except (OSError, zipfile.BadZipFile) as e:
        if built:
            _cleanup_partial(dest)
        raise PreservationError(f"restore failed: {e}") from e
    except BaseException:
        # a Ctrl-C (or any abort) mid-extraction must not leave the
        # half-built destination behind either; the interrupt itself
        # is re-raised unchanged, never wrapped
        if built:
            _cleanup_partial(dest)
        raise
    finally:
        archive.close()


def _cleanup_partial(dest):
    try:
        if not os.path.lexists(dest):
            return  # creation may have failed before making a directory
        _force_rmtree(dest)
    except BaseException as e:  # even a cleanup interrupt must not mask the cause
        sys.stderr.write(f"warning: could not remove partial restore "
                         f"destination {dest}: {e}\n")


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
    exc_info[1].
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
