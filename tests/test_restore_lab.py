"""Recovery tests for labkit restore (RESTORE-1/2, formerly CLI-9).

`labkit restore ZIP DEST` must rebuild a lab tree from
a version-1 archive+sidecar pair - both the unnumbered pairs the 002
migration wrote and the numbered <slug>--NNN.zip pairs the archive
command will write (same version-1 metadata, different file names) -
refusing existing destinations, validating the whole pair before the
first write, and cleaning up exactly its own partial destination on
any failure or interrupt while the input pair stays untouched.

The preservation expectations come from CLI-6 (file bytes, full mode
bits including setuid/setgid/sticky, entry types, empty directories,
the lab root, xattrs, safe relative links, no silent unsupported data
loss, bounded payload reads, ZIP64).

Hermeticity (CLI-10): every fixture here is disposable. The old v1
pair is frozen output captured from the old 002 writer using synthetic
data; numbered pairs are hand-built from
the version-1 constants with facts captured by this file's own
oracle, which shares no code with the production module. Command-level
tests run a fixture checkout's copy of labkit as a
subprocess with a stub-only PATH and a disposable HOME, from a cwd
outside the checkout. No real lab, archive, credential, or cloud tool
is ever touched.
"""

import contextlib
import copy
import ctypes
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from util import (REPO_ROOT, XATTRS, list_xattrs, load_labkit_cli,
                  make_repo, set_xattr, stub_env)

from old_v1_fixture import ZIP_BYTES, META_BYTES

def setUpModule():
    """CLI-10: execute only a disposable checkout's implementation."""
    global preservation, _checkout
    checkout_tmp = tempfile.TemporaryDirectory(prefix="restore-checkout-")
    unittest.addModuleCleanup(checkout_tmp.cleanup)
    _checkout = make_repo(Path(checkout_tmp.name))
    preservation = load_labkit_cli(_checkout, "preservation")


# --- the version-1 pair format, pinned as literals -------------------------
# Both the 002 migration and the archive command write this same
# metadata format; only the file names differ (<slug>.zip vs
# <slug>--001.zip). The constants are deliberately NOT imported from
# the production module: the pairs these tests restore must match the
# format as written on disk, not whatever the module currently
# believes, so a format drift is a test failure, not a tautology.
V1_VERSION = 1
V1_KIND = "gcp-lab-002-archive-metadata"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- independent fact oracles (CLI-6 round-trip evidence) -----------------

def tree_facts(root: Path) -> dict:
    """relpath -> recoverable facts, read with stdlib calls only.

    Entry type, FULL permission bits (setuid/setgid/sticky included),
    symlink target, and content hash, captured without following any
    symlink. Shares no code with labkit_cli.preservation, so comparing
    a restored tree against these facts is a round-trip check the
    production verifier cannot influence.
    """
    root = Path(root)
    facts = {"": {"type": "dir",
                  "mode": stat.S_IMODE(os.lstat(root).st_mode)}}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in dirnames + filenames:
            path = base / name
            rel = str(path.relative_to(root))
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                facts[rel] = {"type": "symlink",
                              "target": os.readlink(path),
                              "mode": stat.S_IMODE(st.st_mode)}
            elif stat.S_ISDIR(st.st_mode):
                facts[rel] = {"type": "dir",
                              "mode": stat.S_IMODE(st.st_mode)}
            elif stat.S_ISREG(st.st_mode):
                digest = hashlib.sha256()
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        digest.update(chunk)
                facts[rel] = {"type": "file",
                              "mode": stat.S_IMODE(st.st_mode),
                              "sha256": digest.hexdigest()}
            else:
                facts[rel] = {"type": "special"}
    return facts


def xattr_facts(root: Path) -> dict:
    """relpath -> {name: hex value} via the independent libc oracle.

    Symlinks report the LINK's own attributes (no-follow); everything
    else reports the entry's. Reads go through util's re-implemented
    libc calls, never the production module.
    """
    root = Path(root)
    paths = {"": root}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in dirnames + filenames:
            path = base / name
            paths[str(path.relative_to(root))] = path
    facts = {}
    for rel, path in paths.items():
        follow = not os.path.islink(path)
        facts[rel] = {attr.decode(): value.hex() for attr, value in
                      list_xattrs(path, follow_symlinks=follow).items()}
    return facts


def _stamp_link_xattr(path, name: bytes, value: bytes) -> None:
    """Set an attribute on the LINK ITSELF via the literal ABI.

    Independent of both the production module and util.set_xattr
    (which follows links): a no-follow write needs Darwin's
    XATTR_NOFOLLOW options bit (0x0001) or glibc's l-prefixed symbol,
    spelled out here so a shared bug cannot make source and restored
    agree with each other's mistake.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    path_b = os.fsencode(path)
    if sys.platform == "darwin":
        fn = libc.setxattr
        fn.restype = ctypes.c_ssize_t
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                       ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        rc = fn(path_b, name, value, len(value), 0, 0x0001)
    else:
        fn = libc.lsetxattr
        fn.restype = ctypes.c_ssize_t
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                       ctypes.c_size_t]
        rc = fn(path_b, name, value, len(value), 0)
    if rc != 0:
        raise OSError(ctypes.get_errno(), "cannot stamp link attribute",
                      str(path))


# --- disposable fixtures ---------------------------------------------------

def build_source_lab(parent: Path, slug: str = "demo-lab") -> Path:
    """A lab exercising every preservation fact CLI-6 names: hidden,
    executable, read-only, and 0600 files, the full special-bit group
    on files and directories, nested and empty directories, a
    directory-alias link, a file link, an internal dangling link, and
    a lab root with its own distinctive mode."""
    lab = parent / slug
    lab.mkdir()
    (lab / "main.tf").write_text("# main\n")
    hidden = lab / ".hidden"
    hidden.write_text("hidden\n")
    hidden.chmod(0o600)
    executable = lab / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    readonly = lab / "readonly.tf"
    readonly.write_text("# read only\n")
    readonly.chmod(0o444)
    setuid_sh = lab / "setuid.sh"
    setuid_sh.write_text("#!/bin/sh\n")
    setuid_sh.chmod(0o4755)
    setgid_sh = lab / "setgid.sh"
    setgid_sh.write_text("#!/bin/sh\n")
    setgid_sh.chmod(0o2755)
    sticky_tf = lab / "sticky.tf"
    sticky_tf.write_text("# sticky bit\n")
    sticky_tf.chmod(0o1644)
    (lab / "empty-dir").mkdir()
    sticky_dir = lab / "sticky-dir"
    sticky_dir.mkdir()
    sticky_dir.chmod(0o1777)
    nested = lab / "nested" / "deep"
    nested.mkdir(parents=True)
    (nested / "file.txt").write_text("deep file\n")
    docs = lab / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("# docs\n")
    (lab / "link.tf").symlink_to("main.tf")
    (lab / "docs-alias").symlink_to("docs")
    (lab / "dangling.tf").symlink_to("no-such-file")
    lab.chmod(0o2755)
    return lab


def stamp_xattrs(lab: Path) -> None:
    """Test-controlled attributes on a file, a directory, the lab
    root, and a symlink's OWN record, so a restore cannot pass by
    accidentally preserving system-added attributes alone."""
    set_xattr(lab / "main.tf", b"user.mark", b"FILE")
    set_xattr(lab / "empty-dir", b"user.mark", b"DIR")
    set_xattr(lab, b"user.mark", b"ROOT")
    _stamp_link_xattr(lab / "link.tf", b"user.mark", b"LINK")


def write_v1_pair(archives_dir: Path, source: Path, facts: dict, *,
                  name: str, zip64: bool = False) -> Path:
    """Write a version-1 archive+sidecar pair from independent facts.

    The same shape the 002 migration writes: the zip carries file
    payloads, the sidecar carries types, full modes, link targets, and
    xattrs. `name` is the pair's base name without .zip - unnumbered
    ("slug") or numbered ("slug--001").
    """
    zip_path = archives_dir / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for rel, fact in sorted(facts.items()):
            if fact["type"] != "file":
                continue
            data = (source / rel).read_bytes()
            if zip64:
                with archive.open(rel, "w", force_zip64=True) as entry:
                    entry.write(data)
            else:
                archive.writestr(rel, data)
    entries = {}
    for rel, fact in sorted(facts.items()):
        path = source if rel == "" else source / rel
        follow = fact["type"] != "symlink"
        entry = {"type": fact["type"], "mode": fact["mode"],
                 "xattrs": {attr.decode(): value.hex() for attr, value in
                            list_xattrs(path,
                                        follow_symlinks=follow).items()}}
        if fact["type"] == "file":
            entry["sha256"] = fact["sha256"]
        elif fact["type"] == "symlink":
            entry["target"] = fact["target"]
        entries[rel] = entry
    zip_path.with_suffix(".meta.json").write_text(json.dumps(
        {"version": V1_VERSION, "kind": V1_KIND, "slug": name,
         "entries": entries}))
    return zip_path


def pair_bytes(zip_path: Path):
    """The exact bytes of a pair, for untouched-before/after checks."""
    return (zip_path.read_bytes(),
            zip_path.with_suffix(".meta.json").read_bytes())


def good_entries(payloads: dict) -> dict:
    """A structurally valid sidecar record for the given payloads."""
    entries = {"": {"type": "dir", "mode": 0o755, "xattrs": {}}}
    for rel, data in payloads.items():
        parts = rel.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            entries.setdefault(parent, {"type": "dir", "mode": 0o755,
                                         "xattrs": {}})
        entries[rel] = {"type": "file", "mode": 0o644, "xattrs": {},
                        "sha256": sha256_hex(data)}
    return entries


def hand_sidecar(zip_path: Path, entries: dict) -> None:
    """Write a full sidecar next to an existing zip (test-controlled)."""
    zip_path.with_suffix(".meta.json").write_text(json.dumps(
        {"version": V1_VERSION, "kind": V1_KIND,
         "slug": zip_path.name[:-4], "entries": entries}))


def hand_pair(zip_path: Path, payloads: dict, entries: dict) -> Path:
    """A pair with test-controlled zip payloads AND sidecar record."""
    with zipfile.ZipFile(zip_path, "w") as archive:
        for rel, data in payloads.items():
            archive.writestr(rel, data)
    hand_sidecar(zip_path, entries)
    return zip_path


@unittest.skipUnless(XATTRS, "extended attributes required to build "
                             "and verify preservation pairs")
class RestoreRoundTripTests(unittest.TestCase):
    """Old and new version-1 pairs rebuild the whole lab tree (CLI-9),
    with the CLI-6 preservation facts asserted through this file's
    independent oracle."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        env = stub_env(self.root / "home", self.root / "gcloud.log")
        (self.root / "home" / "bin" / "git").symlink_to(shutil.which("git"))
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    @unittest.skipUnless(sys.platform == "darwin", "frozen old pair carries Darwin provenance")
    def test_old_v1_pair_written_by_the_migration_restores(self):
        """CLI-9/10: frozen old writer output, independently expected facts."""
        zip_path = self.root / "old-lab.zip"
        zip_path.write_bytes(ZIP_BYTES)
        zip_path.with_suffix(".meta.json").write_bytes(META_BYTES)
        facts = {
            "": {"type": "dir", "mode": 0o750},
            "empty": {"type": "dir", "mode": 0o700},
            "main.tf": {"type": "file", "mode": 0o600,
                        "sha256": sha256_hex(b"# frozen old lab\n")},
            "link.tf": {"type": "symlink", "mode": 0o777,
                        "target": "main.tf"},
        }
        before = pair_bytes(zip_path)
        dest = self.root / "restored-old-pair"
        preservation.restore_archive(zip_path, dest)
        self.assertEqual(tree_facts(dest), facts)
        self.assertEqual((dest / "main.tf").read_bytes(), b"# frozen old lab\n")
        attrs = {rel: {"com.apple.provenance": "010200c130e6e389d0ee45"}
                 for rel in facts}
        attrs["main.tf"]["user.mark"] = b"OLDPAIR".hex()
        self.assertEqual(xattr_facts(dest), attrs)
        self.assertEqual(pair_bytes(zip_path), before)

    def test_numbered_v1_pair_restores_full_preservation(self):
        """CLI-9: the numbered <slug>--001.zip pair (the name the
        archive command writes) restores through the same sidecar
        derivation. CLI-6: bytes, full modes including the
        setuid/setgid/sticky group, entry types, empty directories,
        the lab root, xattrs, and safe relative links (file,
        directory-alias, internal dangling) all round-trip, checked
        against facts captured before the pair existed."""
        lab = build_source_lab(self.root)
        stamp_xattrs(lab)
        facts = tree_facts(lab)
        xattrs = xattr_facts(lab)
        archives = self.root / "archives"
        archives.mkdir()
        zip_path = write_v1_pair(archives, lab, facts, name="demo-lab--001")
        before = pair_bytes(zip_path)
        dest = self.root / "restored-numbered"
        preservation.restore_archive(zip_path, dest)
        self.assertEqual(tree_facts(dest), facts)
        self.assertEqual(xattr_facts(dest), xattrs)
        self.assertEqual(pair_bytes(zip_path), before)

    def test_large_payloads_stream_bounded_and_zip64_pairs_restore(self):
        """CLI-6 (recovery side): payload members are read in bounded
        chunks - never whole-entry reads that a huge archive member
        could turn into a memory exhaustion - and ZIP64-formatted
        pairs restore byte-for-byte. The fixture forces ZIP64
        extensions on every entry (version-needed 45), pinning that
        the restore path really parses them; ZipFile.read, the
        whole-entry API, fails the test if the restore ever uses it.
        """
        lab = self.root / "big-lab"
        lab.mkdir()
        (lab / "big.bin").write_bytes(os.urandom(5 << 20))
        (lab / "small.txt").write_text("trailer\n")
        facts = tree_facts(lab)
        archives = self.root / "archives"
        archives.mkdir()
        zip_path = write_v1_pair(archives, lab, facts, name="big-lab--001",
                                 zip64=True)
        header = zip_path.read_bytes()[:6]
        self.assertEqual(int.from_bytes(header[4:6], "little"), 45)
        dest = self.root / "restored-zip64"
        zip_read = zipfile.ZipExtFile.read

        def bounded_zip_read(stream, size=-1):
            self.assertGreater(size, 0, "ZIP reads must be bounded")
            self.assertLessEqual(size, 1 << 20)
            return zip_read(stream, size)

        file_open = open

        @contextlib.contextmanager
        def bounded_file_open(*args, **kwargs):
            with file_open(*args, **kwargs) as stream:
                reader = mock.Mock(wraps=stream)

                def read(size=-1):
                    self.assertGreater(size, 0, "hash reads must be bounded")
                    self.assertLessEqual(size, 1 << 20)
                    return stream.read(size)

                reader.read.side_effect = read
                yield reader

        with mock.patch.object(
                zipfile.ZipFile, "read",
                side_effect=AssertionError(
                    "whole-entry reads are unbounded; payloads must be "
                    "streamed in bounded chunks")), \
                mock.patch.object(zipfile.ZipExtFile, "read", bounded_zip_read), \
                mock.patch("builtins.open", bounded_file_open):
            preservation.restore_archive(zip_path, dest)
        self.assertEqual(tree_facts(dest), facts)

    def test_empty_xattrs_round_trip_on_root_directory_and_file(self):
        """CLI-6/CLI-9: zero-length xattrs are data, not invalid hex."""
        lab = build_source_lab(self.root)
        for path in (lab, lab / "empty-dir", lab / "main.tf"):
            set_xattr(path, b"user.empty", b"")
        facts, xattrs = tree_facts(lab), xattr_facts(lab)
        zip_path = write_v1_pair(self.root, lab, facts, name="empty--001")
        before = pair_bytes(zip_path)
        dest = self.root / "restored-empty"
        preservation.restore_archive(zip_path, dest)
        self.assertEqual(tree_facts(dest), facts)
        self.assertEqual(xattr_facts(dest), xattrs)
        self.assertEqual(pair_bytes(zip_path), before)

    def test_restrictive_modes_are_applied_after_content_verification(self):
        """CLI-6/CLI-9: mode 0000 is preserved, not a verification failure."""
        source = self.root / "closed-source"
        (source / "closed").mkdir(parents=True)
        (source / "closed/secret").write_bytes(b"private")
        facts = tree_facts(source)
        for fact in facts.values():
            fact["mode"] = 0
        zip_path = write_v1_pair(self.root, source, facts, name="closed")
        before = pair_bytes(zip_path)
        dest = self.root / "restored-closed"
        preservation.restore_archive(zip_path, dest)
        # Inspect each recorded mode before opening its parent for the oracle.
        self.assertEqual(stat.S_IMODE(dest.lstat().st_mode), 0)
        dest.chmod(0o700)
        self.assertEqual(stat.S_IMODE((dest / "closed").lstat().st_mode), 0)
        (dest / "closed").chmod(0o700)
        self.assertEqual(stat.S_IMODE((dest / "closed/secret").lstat().st_mode), 0)
        (dest / "closed/secret").chmod(0o600)
        self.assertEqual((dest / "closed/secret").read_bytes(), b"private")
        self.assertEqual(pair_bytes(zip_path), before)


@unittest.skipUnless(XATTRS, "extended attributes required to build "
                             "and verify preservation pairs")
class RestoreRefusalTests(unittest.TestCase):
    """Refusals fire before the first write (CLI-9), and nothing the
    record cannot recreate is silently dropped (CLI-6)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        patch = mock.patch.dict(os.environ, stub_env(
            self.root / "home", self.root / "gcloud.log"), clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.lab = build_source_lab(self.root, slug="refusal-lab")
        self.facts = tree_facts(self.lab)
        self.archives = self.root / "archives"
        self.archives.mkdir()
        self.zip_path = write_v1_pair(self.archives, self.lab, self.facts,
                                       name="refusal-lab")

    def test_existing_destinations_are_refused_including_dangling_links(self):
        """CLI-9: a destination that exists in ANY form - a populated
        directory, a plain file, or a dangling symlink - is refused,
        its contents preserved, never written through; a missing
        parent directory is a refusal too, and the input pair is
        never modified by any of them."""
        before = pair_bytes(self.zip_path)
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("precious\n")
        as_file = self.root / "as-file"
        as_file.write_text("do not replace\n")
        as_link = self.root / "as-link"
        as_link.symlink_to(self.root / "no-such-target")
        cases = [("directory", occupied), ("file", as_file),
                 ("dangling link", as_link),
                 ("missing parent", self.root / "no-such-dir" / "dest")]
        for label, dest in cases:
            with self.subTest(label=label):
                with self.assertRaises(preservation.PreservationError):
                    preservation.restore_archive(self.zip_path, dest)
        self.assertEqual([p.name for p in occupied.iterdir()], ["keep.txt"])
        self.assertEqual((occupied / "keep.txt").read_text(), "precious\n")
        self.assertEqual(as_file.read_text(), "do not replace\n")
        self.assertTrue(as_link.is_symlink())
        self.assertFalse(as_link.exists())  # still dangling, not written
        self.assertEqual(pair_bytes(self.zip_path), before)

    def test_unsafe_entries_are_refused_before_any_write(self):
        """CLI-9/CLI-6: sidecar entry names that escape, absolute or
        chained symlink targets, a symlink parent of real payload
        entries, and the unsafe zip shapes (duplicates, an entry
        below a file entry, absolute names) are all refused with
        NOTHING extracted anywhere."""
        base = {"keep.txt": b"keep"}

        def sidecar(mutate):
            entries = good_entries(base)
            mutate(entries)
            return entries

        cases = []
        entries = sidecar(lambda e: e.update(
            {"../esc": {"type": "file", "mode": 0o644, "xattrs": {},
                        "sha256": sha256_hex(b"esc")}}))
        cases.append(("sidecar entry name escapes",
                      hand_pair(self.archives / "v1.zip", base, entries),
                      preservation.UnsafeArchiveEntry))
        entries = sidecar(lambda e: e.update(
            {"esc": {"type": "symlink", "mode": 0o777,
                     "target": "/etc/hosts", "xattrs": {}}}))
        cases.append(("absolute symlink target",
                      hand_pair(self.archives / "v2.zip", base, entries),
                      preservation.UnsafeArchiveEntry))
        entries = sidecar(lambda e: e.update(
            {"a": {"type": "symlink", "mode": 0o777, "target": ".",
                   "xattrs": {}},
             "b": {"type": "symlink", "mode": 0o777,
                   "target": "a/../../outside", "xattrs": {}}}))
        cases.append(("chained symlink escape",
                      hand_pair(self.archives / "v3.zip", base, entries),
                      preservation.UnsafeArchiveEntry))
        payloads = {"sub/file.txt": b"payload"}
        entries = good_entries(payloads)
        entries["sub"] = {"type": "symlink", "mode": 0o777,
                          "target": "../..", "xattrs": {}}
        cases.append(("symlink parent of a payload entry",
                      hand_pair(self.archives / "v4.zip", payloads,
                                entries),
                      preservation.PreservationError))

        dup = self.archives / "v5.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(dup, "w") as z:
                z.writestr("a.txt", b"one")
                z.writestr("a.txt", b"two")
        hand_sidecar(dup, good_entries({"a.txt": b"one"}))
        cases.append(("duplicate zip entries", dup,
                      preservation.PreservationError))
        below = self.archives / "v6.zip"
        with zipfile.ZipFile(below, "w") as z:
            z.writestr("a", b"not a directory")
            z.writestr("a/b.txt", b"below")
        hand_sidecar(below, good_entries({"a": b"not a directory",
                                          "a/b.txt": b"below"}))
        cases.append(("payload entry below a file entry", below,
                      preservation.PreservationError))
        absolute = self.archives / "v7.zip"
        escaped = self.root / "absolute-escape.txt"
        with zipfile.ZipFile(absolute, "w") as z:
            z.writestr(str(escaped), b"escape")
        hand_sidecar(absolute, {"": {"type": "dir", "mode": 0o755,
                                     "xattrs": {}}})
        cases.append(("absolute zip entry name", absolute,
                       preservation.UnsafeArchiveEntry))

        traversal = self.archives / "v8.zip"
        with zipfile.ZipFile(traversal, "w") as z:
            z.writestr("../escaped.txt", b"escape")
        hand_sidecar(traversal, good_entries({}))
        cases.append(("relative zip escape", traversal,
                      preservation.UnsafeArchiveEntry))
        conflict = self.archives / "v9.zip"
        with zipfile.ZipFile(conflict, "w") as z:
            z.writestr("d/", b"")
            z.writestr("d", b"not a directory")
        hand_sidecar(conflict, good_entries({"d": b"not a directory"}))
        cases.append(("zip directory/file type conflict", conflict,
                      preservation.PreservationError))

        for label, zip_path, error in cases:
            with self.subTest(label=label):
                dest = self.root / ("dest-" + zip_path.stem)
                with self.assertRaises(error):
                    preservation.restore_archive(zip_path, dest)
                self.assertFalse(os.path.lexists(dest))
        # nothing escaped anywhere above the refused destinations
        self.assertFalse(list(self.root.glob("esc*")))
        self.assertFalse(list(self.root.glob("outside*")))
        self.assertFalse(escaped.exists())
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_cli6_cli9_case_unicode_link_chains_cycles_and_duplicate_names(self):
        """CLI-6/9/10: retained ambiguity refusals happen before mkdir."""
        variants = []
        for name, target in (("link", "LINK/.."), ("\u00e9", "e\u0301/.."),
                             ("loop", "loop")):
            entries = good_entries({})
            entries[name] = {"type": "symlink", "mode": 0o777,
                             "target": "." if name != "loop" else "loop", "xattrs": {}}
            entries["escape"] = {"type": "symlink", "mode": 0o777,
                                  "target": target, "xattrs": {}}
            variants.append((name + target, {}, entries))
        for names in (("README", "readme"), ("caf\u00e9", "cafe\u0301")):
            payloads = {names[0]: b"first", names[1]: b"second"}
            variants.append((repr(names), payloads, good_entries(payloads)))
        for i, (label, payloads, entries) in enumerate(variants):
            with self.subTest(label=label):
                archive = hand_pair(self.archives / f"ambiguous-{i}.zip", payloads, entries)
                before = pair_bytes(archive)
                dest = self.root / f"ambiguous-dest-{i}"
                with mock.patch.object(os, "mkdir", wraps=os.mkdir) as mkdir:
                    with self.assertRaises(preservation.PreservationError) as caught:
                        preservation.restore_archive(archive, dest)
                    mkdir.assert_not_called()
                if payloads:
                    for name in payloads:
                        self.assertIn(name, str(caught.exception))
                self.assertFalse(os.path.lexists(dest))
                self.assertEqual(pair_bytes(archive), before)

    def test_nul_truncated_zip_name_is_refused_before_writes(self):
        """R2 CLI-6/CLI-9: the original ZIP name must not be silently truncated."""
        source = self.root / "nul-source"
        source.mkdir()
        (source / "a").write_bytes(b"data")
        archive = write_v1_pair(self.archives, source, tree_facts(source),
                                name="nul-name")
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("aXb", b"data")
        raw = archive.read_bytes()
        self.assertEqual(raw.count(b"aXb"), 2)  # local and central headers
        archive.write_bytes(raw.replace(b"aXb", b"a\0b"))
        # Independent evidence: stdlib accepts the patched headers and
        # exposes the payload under the truncated name in the sidecar.
        with zipfile.ZipFile(archive) as z:
            info, = z.infolist()
            self.assertEqual(info.orig_filename, "a\0b")
            self.assertEqual(info.filename, "a")
            self.assertEqual(z.read("a"), b"data")
        before = pair_bytes(archive)
        dest = self.root / "nul-dest"
        with mock.patch.object(os, "mkdir", wraps=os.mkdir) as mkdir:
            with self.assertRaises(preservation.PreservationError):
                preservation.restore_archive(archive, dest)
            mkdir.assert_not_called()
        self.assertFalse(os.path.lexists(dest))
        self.assertEqual(pair_bytes(archive), before)

    def test_directory_payload_is_refused_before_writes(self):
        """R3 CLI-6/CLI-9: directory members cannot discard nonempty payloads."""
        source = self.root / "directory-source"
        (source / "empty").mkdir(parents=True)
        archive = write_v1_pair(self.archives, source, tree_facts(source),
                                name="directory-data")
        with zipfile.ZipFile(archive, "a") as z:
            z.writestr("empty/", b"must not be discarded")
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(z.read("empty/"), b"must not be discarded")
        before = pair_bytes(archive)
        dest = self.root / "directory-dest"
        with mock.patch.object(os, "mkdir", wraps=os.mkdir) as mkdir:
            with self.assertRaises(preservation.PreservationError):
                preservation.restore_archive(archive, dest)
            mkdir.assert_not_called()
        self.assertFalse(os.path.lexists(dest))
        self.assertEqual(pair_bytes(archive), before)

    def test_incomplete_or_mistyped_metadata_is_refused_not_half_restored(self):
        """CLI-9/CLI-6: the FULL metadata record is validated before
        the first write - a missing or mistyped mode, a bad hash, a
        non-mapping xattr record, a non-hex attribute value, an
        unknown entry type, entry facts that are not a mapping at all:
        every one refused with nothing extracted, never a KeyError
        past a half-built destination and never a silently dropped
        entry."""
        payloads = {"f.txt": b"data"}
        base = good_entries(payloads)

        def mutated(label, change):
            entries = copy.deepcopy(base)
            change(entries)
            return label, entries

        variants = [
            mutated("file mode missing",
                    lambda e: e["f.txt"].pop("mode")),
            mutated("file mode not an integer",
                    lambda e: e["f.txt"].update(mode="0644")),
            mutated("file mode out of range",
                    lambda e: e["f.txt"].update(mode=0o10000)),
            mutated("file mode boolean",
                    lambda e: e["f.txt"].update(mode=True)),
            mutated("file hash missing",
                    lambda e: e["f.txt"].pop("sha256")),
            mutated("file hash not hex",
                    lambda e: e["f.txt"].update(sha256="z" * 64)),
            mutated("xattr record not a mapping",
                    lambda e: e["f.txt"].update(xattrs=[])),
            mutated("xattr value not hex",
                    lambda e: e["f.txt"].update(xattrs={"user.x": "nothex"})),
            mutated("xattr name contains NUL",
                    lambda e: e["f.txt"].update(xattrs={"user.x\0bad": "00"})),
            mutated("entry name contains NUL",
                    lambda e: e.update({"bad\0name": {
                        "type": "dir", "mode": 0o755, "xattrs": {}}})),
            mutated("entry name cannot be encoded by the filesystem",
                    lambda e: e.update({"bad\ud800name": {
                        "type": "dir", "mode": 0o755, "xattrs": {}}})),
            mutated("xattr name cannot be encoded by the filesystem",
                    lambda e: e["f.txt"].update(xattrs={"user.\ud800": "00"})),
            mutated("symlink target contains NUL",
                    lambda e: e.update({"link": {"type": "symlink",
                        "mode": 0o777, "xattrs": {}, "target": "f.txt\0bad"}})),
            mutated("symlink target cannot be encoded by the filesystem",
                    lambda e: e.update({"link": {"type": "symlink",
                        "mode": 0o777, "xattrs": {}, "target": "bad\ud800"}})),
            mutated("unknown entry type",
                    lambda e: e.update({"pipe": {"type": "fifo",
                                                 "mode": 0o644,
                                                 "xattrs": {}}})),
            mutated("entry facts not a mapping",
                    lambda e: e.__setitem__("f.txt", "file")),
            mutated("root entry facts not a mapping",
                    lambda e: e.__setitem__("", "root")),
        ]
        for i, (label, entries) in enumerate(variants):
            with self.subTest(label=label):
                zip_path = self.archives / f"meta-{i}.zip"
                hand_pair(zip_path, payloads, entries)
                dest = self.root / ("dest-meta-" + str(i))
                with mock.patch.object(os, "mkdir", side_effect=AssertionError(
                        "metadata must be refused before extraction writes")):
                    with self.assertRaises(preservation.PreservationError):
                        preservation.restore_archive(zip_path, dest)
                self.assertFalse(os.path.lexists(dest))

    def test_ambiguous_json_or_zip_directory_metadata_is_refused_before_writes(self):
        """CLI-6/CLI-9: contradictory records cannot be silently discarded."""
        entries = good_entries({"f": b"data"})
        archive = hand_pair(self.archives / "ambiguous.zip", {"f": b"data"}, entries)
        sidecar = archive.with_suffix(".meta.json")
        valid = sidecar.read_text()
        variants = [valid.replace('"version": 1', '"version": true'),
                    valid.replace('"mode": 420', '"mode": 384, "mode": 420')]
        before = archive.read_bytes()
        for text in variants:
            with self.subTest(text=text):
                sidecar.write_text(text)
                with mock.patch.object(os, "mkdir", side_effect=AssertionError(
                        "ambiguous metadata must be refused before writes")):
                    with self.assertRaises(preservation.PreservationError):
                        preservation.restore_archive(archive, self.root / "dest")
                self.assertEqual(archive.read_bytes(), before)
                self.assertEqual(sidecar.read_text(), text)
        sidecar.write_text(valid)
        with zipfile.ZipFile(archive, "a") as z:
            z.writestr("unrecorded-dir/", b"")
        with mock.patch.object(os, "mkdir", side_effect=AssertionError(
                "unrecorded zip directories must be refused before writes")):
            with self.assertRaises(preservation.PreservationError):
                preservation.restore_archive(archive, self.root / "dest")

    def test_payload_and_metadata_disagreement_is_refused(self):
        """CLI-9/CLI-6: the zip payload set must match the record
        exactly - an extra zip entry, a recorded file missing from the
        zip, or a payload that no longer matches its recorded hash is
        refused BEFORE the destination is created; a pair without its
        sidecar, a corrupt zip, or a non-zip file is refused too.
        Nothing is silently dropped from a restore."""
        dest = self.root / "never-created"
        before = pair_bytes(self.zip_path)
        # an extra zip entry the record does not know about
        extra = self.archives / "extra.zip"
        shutil.copy2(self.zip_path, extra)
        shutil.copy2(self.zip_path.with_suffix(".meta.json"),
                     extra.with_suffix(".meta.json"))
        with zipfile.ZipFile(extra, "a") as z:
            z.writestr("unrecorded.txt", b"where did I come from")
        with self.assertRaises(preservation.PreservationError):
            preservation.restore_archive(extra, dest)
        self.assertFalse(os.path.lexists(dest))
        # a recorded file missing from the zip
        missing = self.archives / "missing.zip"
        shutil.copy2(self.zip_path, missing)
        shutil.copy2(self.zip_path.with_suffix(".meta.json"),
                     missing.with_suffix(".meta.json"))
        sidecar = json.loads(
            missing.with_suffix(".meta.json").read_text())
        sidecar["entries"]["new-file.tf"] = {
            "type": "file", "mode": 0o644, "xattrs": {},
            "sha256": sha256_hex(b"x")}
        missing.with_suffix(".meta.json").write_text(json.dumps(sidecar))
        with self.assertRaises(preservation.PreservationError):
            preservation.restore_archive(missing, dest)
        self.assertFalse(os.path.lexists(dest))
        # a tampered payload: the recorded hash no longer matches
        tampered = self.archives / "tampered.zip"
        entries = json.loads(
            self.zip_path.with_suffix(".meta.json").read_text())["entries"]
        with zipfile.ZipFile(tampered, "w") as z:
            for rel, fact in sorted(entries.items()):
                if fact["type"] != "file":
                    continue
                data = (self.lab / rel).read_bytes()
                if rel == "main.tf":
                    data = b"tampered\n"
                z.writestr(rel, data)
        hand_sidecar(tampered, entries)
        with self.assertRaises(preservation.PreservationError):
            preservation.restore_archive(tampered, dest)
        self.assertFalse(os.path.lexists(dest))
        # a pair without its sidecar
        orphan = self.archives / "orphan.zip"
        shutil.copy2(self.zip_path, orphan)
        with self.assertRaises(preservation.PreservationError):
            preservation.restore_archive(orphan, dest)
        self.assertFalse(os.path.lexists(dest))
        # a corrupt zip and a non-zip payload
        broken = self.archives / "broken.zip"
        broken.write_bytes(self.zip_path.read_bytes()[:100])
        with self.assertRaises(preservation.PreservationError):
            preservation.restore_archive(broken, dest)
        fake = self.archives / "fake.zip"
        fake.write_bytes(b"this is not a zip")
        with self.assertRaises(preservation.PreservationError):
            preservation.restore_archive(fake, dest)
        self.assertFalse(os.path.lexists(dest))
        # the source pair was never modified by any refusal above
        self.assertEqual(pair_bytes(self.zip_path), before)


@unittest.skipUnless(XATTRS, "extended attributes required to build "
                             "and verify preservation pairs")
class RestoreCleanupTests(unittest.TestCase):
    """Failures and interrupts clean exactly the attempt's own partial
    destination (CLI-9)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        patch = mock.patch.dict(os.environ, stub_env(
            self.root / "home", self.root / "gcloud.log"), clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.lab = build_source_lab(self.root, slug="cleanup-lab")
        self.facts = tree_facts(self.lab)
        self.archives = self.root / "archives"
        self.archives.mkdir()
        self.zip_path = write_v1_pair(self.archives, self.lab, self.facts,
                                       name="cleanup-lab")

    def test_interrupt_immediately_after_destination_creation_cleans_or_reports(self):
        """R1 CLI-9: mkdir's post-create interrupt must not leak an unreported tree."""
        before = pair_bytes(self.zip_path)
        neighbor = self.root / "neighbor"
        neighbor.mkdir()
        (neighbor / "keep").write_bytes(b"keep")
        real_mkdir = os.mkdir
        for cleanup_fails in (False, True):
            with self.subTest(cleanup_fails=cleanup_fails):
                dest = self.root / f"mkdir-interrupted-{cleanup_fails}"
                interrupt = KeyboardInterrupt()

                def interrupted_mkdir(path, *args, **kwargs):
                    real_mkdir(path, *args, **kwargs)
                    if Path(path) == dest:
                        raise interrupt

                stderr = io.StringIO()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(os, "mkdir", interrupted_mkdir))
                    stack.enter_context(contextlib.redirect_stderr(stderr))
                    if cleanup_fails:
                        stack.enter_context(mock.patch.object(
                            shutil, "rmtree", side_effect=OSError("locked")))
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        preservation.restore_archive(self.zip_path, dest)
                self.assertIs(caught.exception, interrupt)
                if cleanup_fails:
                    self.assertTrue(dest.is_dir())
                    self.assertIn(str(dest), stderr.getvalue())
                else:
                    self.assertFalse(os.path.lexists(dest))
                    self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(pair_bytes(self.zip_path), before)
                self.assertEqual((neighbor / "keep").read_bytes(), b"keep")

    def test_destination_creation_collision_is_never_cleaned(self):
        """R1 CLI-9: EEXIST at mkdir never grants ownership of the destination."""
        before = pair_bytes(self.zip_path)
        dest = self.root / "collision"
        real_mkdir = os.mkdir

        def colliding_mkdir(path, *args, **kwargs):
            real_mkdir(path, *args, **kwargs)
            (Path(path) / "keep").write_bytes(b"not owned")
            raise FileExistsError("destination collision")

        with mock.patch.object(os, "mkdir", colliding_mkdir), \
                mock.patch.object(shutil, "rmtree", wraps=shutil.rmtree) as rmtree:
            with self.assertRaises(preservation.PreservationError):
                preservation.restore_archive(self.zip_path, dest)
            rmtree.assert_not_called()
        self.assertEqual((dest / "keep").read_bytes(), b"not owned")
        self.assertEqual(pair_bytes(self.zip_path), before)

    def test_interrupt_mid_extraction_removes_only_the_partial_destination(self):
        """CLI-9: a Ctrl-C mid-extraction still removes the partial
        destination this attempt created, leaves the input pair and
        every pre-existing neighbor untouched, and propagates the
        interrupt unchanged."""
        before = pair_bytes(self.zip_path)
        neighbor = self.root / "neighbor"
        neighbor.mkdir()
        (neighbor / "keep.txt").write_text("keep\n")
        dest = self.root / "partial"
        with mock.patch.object(os, "symlink", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                preservation.restore_archive(self.zip_path, dest)
        self.assertFalse(os.path.lexists(dest))
        self.assertEqual(pair_bytes(self.zip_path), before)
        self.assertEqual(os.listdir(neighbor), ["keep.txt"])

    def test_uncleanable_remnant_is_reported_and_original_error_wins(self):
        """CLI-9: when cleanup itself fails, the remnant destination
        is reported on stderr while the original refusal still
        propagates - a partial tree is never silently left behind,
        and the cleanup failure never masks the real error."""
        dest = self.root / "stuck"
        with mock.patch.object(os, "symlink",
                               side_effect=OSError("write error")):
            with mock.patch.object(shutil, "rmtree",
                                   side_effect=OSError("locked")):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(
                            preservation.PreservationError) as caught:
                        preservation.restore_archive(self.zip_path, dest)
        self.assertIn("restore failed", str(caught.exception))
        self.assertIn(str(dest), stderr.getvalue())
        self.assertTrue(dest.is_dir())  # the reported remnant is real

    def test_interrupt_during_cleanup_does_not_mask_original_failure(self):
        """CLI-9: cleanup interrupts still report remnants and preserve the cause."""
        dest = self.root / "cleanup-interrupted"
        error = OSError("original write failure")
        before = pair_bytes(self.zip_path)
        stderr = io.StringIO()
        with mock.patch.object(os, "symlink", side_effect=error), \
                mock.patch.object(shutil, "rmtree", side_effect=KeyboardInterrupt), \
                contextlib.redirect_stderr(stderr):
            with self.assertRaises(BaseException) as caught:
                preservation.restore_archive(self.zip_path, dest)
        self.assertIsInstance(caught.exception, preservation.PreservationError)
        self.assertIs(caught.exception.__cause__, error)
        self.assertIn(str(dest), stderr.getvalue())
        self.assertTrue(dest.is_dir())
        self.assertEqual(pair_bytes(self.zip_path), before)


@unittest.skipUnless(XATTRS, "extended attributes required to build "
                             "and verify preservation pairs")
class RestoreCommandTests(unittest.TestCase):
    """`labkit restore ZIP DEST` as a user runs it
    (RESTORE-1/2), on a disposable checkout, with a stub-only PATH and a
    disposable HOME (CLI-10)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.home = self.root / "home"
        self.env = stub_env(self.home, self.home / "gcloud.log")

    def build_pair(self, name):
        lab = build_source_lab(self.root, slug=name)
        facts = tree_facts(lab)
        zip_path = write_v1_pair(self.repo / "archives", lab, facts,
                                 name=name)
        return facts, zip_path

    def test_usage_refusal_and_success_exit_codes(self):
        """RESTORE-1/2: wrong argument counts exit 2 before touching
        anything, an existing destination is a refusal (exit 1, never
        merged), and the documented invocation restores an OLD
        unnumbered v1 pair with exit 0."""
        facts, zip_path = self.build_pair("legacy-lab")
        before = pair_bytes(zip_path)
        for argv in ([], [str(zip_path)],
                     [str(zip_path), "one", "two"]):
            with self.subTest(argv=argv):
                result = subprocess.run(
                    [str(self.repo / "labkit"), "restore", *argv],
                    cwd=self.repo, env=self.env, input="",
                    capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage", result.stderr)
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("precious\n")
        result = subprocess.run(
            [str(self.repo / "labkit"), "restore",
             str(zip_path), str(occupied)],
            cwd=self.repo, env=self.env, input="",
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused", result.stderr)
        self.assertEqual((occupied / "keep.txt").read_text(), "precious\n")
        dest = self.root / "restored-legacy"
        result = subprocess.run(
            [str(self.repo / "labkit"), "restore",
             str(zip_path), str(dest)],
            cwd=self.repo, env=self.env, input="",
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tree_facts(dest), facts)
        self.assertEqual(pair_bytes(zip_path), before)

    def test_smoke_from_outside_a_spaced_checkout_hermetic_env(self):
        """RESTORE-1/2 + CLI-10: the documented command restores a NUMBERED
        v1 pair when run from OUTSIDE the checkout, through a checkout
        path containing a space, under a stub-only PATH and a
        disposable HOME - and no cloud tool ever runs."""
        spaced = self.root / "repo with space"
        self.repo.rename(spaced)
        self.repo = spaced
        facts, zip_path = self.build_pair("smoke-lab--001")
        dest = self.root / "restore destination"
        result = subprocess.run(
            [str(self.repo / "labkit"), "restore",
             str(zip_path), str(dest)],
            cwd=self.root,  # outside the checkout
            env=self.env, input="",
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("restored", result.stdout)
        self.assertEqual(tree_facts(dest), facts)
        # hermeticity: nothing outside the stub PATH ever ran
        self.assertFalse((self.home / "gcloud.log").exists())

    def test_restore2_corrupt_pair_refused_before_destination_write(self):
        """RESTORE-2: command-level refusal keeps the pair and destination safe."""
        _, zip_path = self.build_pair("corrupt-lab--001")
        zip_path.with_suffix(".meta.json").write_text("{invalid json")
        before = pair_bytes(zip_path)
        dest = self.root / "must-not-exist"
        result = subprocess.run(
            [str(self.repo / "labkit"), "restore", str(zip_path), str(dest)],
            cwd=self.root, env=self.env, input="",
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("refused", result.stderr)
        self.assertFalse(os.path.lexists(dest))
        self.assertEqual(pair_bytes(zip_path), before)
        self.assertFalse((self.home / "gcloud.log").exists())

    def test_restore2_interrupt_cleans_partial_destination_and_exits_130(self):
        """RESTORE-2: Ctrl-C during extraction keeps the pair and cleans its work."""
        _, zip_path = self.build_pair("interrupted-lab--001")
        before = pair_bytes(zip_path)
        dest = self.root / "partial-destination"
        # Inject Ctrl-C at a real extraction write, then execute the normal CLI.
        bootstrap = """
import os, runpy, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(sys.argv[1]))
from labkit_cli import preservation
def interrupt(*args, **kwargs):
    raise KeyboardInterrupt
preservation.os.symlink = interrupt
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
        result = subprocess.run(
            ["python3", "-c", bootstrap, str(self.repo / "labkit"),
             "restore", str(zip_path), str(dest)],
            cwd=self.root, env=self.env, input="",
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertIn("fresh destination", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(os.path.lexists(dest))
        self.assertEqual(pair_bytes(zip_path), before)
        self.assertFalse((self.home / "gcloud.log").exists())


if __name__ == "__main__":
    unittest.main()
