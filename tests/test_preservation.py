"""CLI-6/7/9/10: preservation regressions retained after migration retirement."""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from util import load_cloud_lab, make_repo, stub_env


DARWIN = sys.platform == "darwin"
XATTR_TOOL = DARWIN and os.path.exists("/usr/bin/xattr")


def abi_libc():
    if not DARWIN:
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        for name in ("listxattr", "getxattr", "setxattr"):
            getattr(libc, name)
        return libc
    except (OSError, AttributeError):
        return None


ABI_XATTRS = abi_libc() is not None


def abi_list_xattrs(path, nofollow):
    """Independent Darwin oracle: literal NOFOLLOW ABI, never suppress errors.

    The historical bug shared a wrong constant between writer and oracle.
    Keep this separate from production and util, with a native cross-check.
    """
    libc = abi_libc()
    opts = 0x0001 if nofollow else 0
    path_b = os.fsencode(path)
    fn = libc.listxattr
    fn.restype = ctypes.c_ssize_t
    fn.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                   ctypes.c_size_t, ctypes.c_int]
    size = fn(path_b, None, 0, opts)
    if size < 0:
        raise OSError(ctypes.get_errno(), "ABI list probe failed")
    if size == 0:
        return {}
    buf = ctypes.create_string_buffer(size + 1)
    size = fn(path_b, buf, size, opts)
    if size < 0:
        raise OSError(ctypes.get_errno(), "ABI list read failed")
    get = libc.getxattr
    get.restype = ctypes.c_ssize_t
    get.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                    ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    out = {}
    for name in buf.raw[:size].split(b"\0"):
        if not name:
            continue
        length = get(path_b, name, None, 0, 0, opts)
        if length < 0:
            raise OSError(ctypes.get_errno(), "ABI get probe failed")
        value = ctypes.create_string_buffer(length + 1)
        got = get(path_b, name, value, length, 0, opts)
        if got < 0:
            raise OSError(ctypes.get_errno(), "ABI get read failed")
        out[bytes(name)] = bytes(value.raw[:got])
    return out


def abi_set_xattr(path, name, value, nofollow):
    fn = abi_libc().setxattr
    fn.restype = ctypes.c_ssize_t
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                   ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    if fn(os.fsencode(path), name, value, len(value), 0,
          0x0001 if nofollow else 0) != 0:
        raise OSError(ctypes.get_errno(), "ABI set failed")


def native_link_xattrs(link):
    result = subprocess.run(["/usr/bin/xattr", "-s", "-l", str(link)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr)
    out = {}
    for line in result.stdout.splitlines():
        name, sep, value = line.partition(": ")
        if sep and not name.startswith("0000"):
            out[name] = value
    return out


class PreservationTests(unittest.TestCase):
    """CLI-6/9: no-follow metadata and recoverable cleanup boundaries."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = make_repo(self.root)
        env = stub_env(self.root / "home", self.root / "cloud.log")
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.p = load_cloud_lab(self.repo, "preservation")
        self.lab = self.repo / "labs" / "synthetic"
        self.lab.mkdir()
        (self.lab / "main.tf").write_bytes(b"work")
        self.link = self.lab / "linked.tf"
        self.link.symlink_to("main.tf")

    @unittest.skipUnless(ABI_XATTRS, "Darwin libc ABI required")
    def test_cli6_symlink_own_xattrs_are_read_no_follow(self):
        abi_set_xattr(self.link, b"user.linkmark", b"LINKOWN", True)
        abi_set_xattr(self.lab / "main.tf", b"user.referent", b"REFERENT", False)
        own = self.p._xattr_list(self.link, follow_symlinks=False)
        self.assertEqual(own.get("user.linkmark"), b"LINKOWN".hex())
        self.assertNotIn("user.referent", own)
        followed = self.p._xattr_list(self.link, follow_symlinks=True)
        self.assertEqual(followed.get("user.referent"), b"REFERENT".hex())
        self.assertNotIn("user.linkmark", followed)
        self.assertEqual(abi_list_xattrs(self.link, True).get(b"user.linkmark"),
                         b"LINKOWN")
        if XATTR_TOOL:
            self.assertEqual(native_link_xattrs(self.link).get("user.linkmark"),
                             "LINKOWN")

    @unittest.skipUnless(ABI_XATTRS, "Darwin libc ABI required")
    def test_cli6_dangling_link_own_attrs_and_follow_failure(self):
        link = self.lab / "dangling"
        link.symlink_to("no-such-target")
        abi_set_xattr(link, b"user.linkmark", b"DANGLING", True)
        self.assertEqual(self.p._xattr_list(link, False).get("user.linkmark"),
                         b"DANGLING".hex())
        with self.assertRaises(self.p.PreservationError):
            self.p._xattr_list(link, True)
        self.assertFalse((self.lab / "no-such-target").exists())

    @unittest.skipUnless(DARWIN, "Darwin options-word ABI required")
    def test_cli6_xattr_probe_and_read_errors_never_become_empty_values(self):
        for results in ([-1], [6, -1]):
            with self.subTest(results=results):
                fake = mock.Mock()
                fake.listxattr.side_effect = lambda path, buf, size, opts: (
                    11 if buf is None else
                    (ctypes.memmove(buf, b"user.probe\0", 11), 11)[1])
                fake.getxattr.side_effect = results
                with mock.patch.object(self.p, "_XATTR", fake):
                    with self.assertRaises(self.p.PreservationError):
                        self.p._xattr_list(self.link, False)

    @unittest.skipUnless(ABI_XATTRS, "Darwin libc ABI required")
    def test_cli6_genuinely_zero_length_value_is_preserved(self):
        abi_set_xattr(self.lab / "main.tf", b"user.empty", b"", False)
        self.assertEqual(self.p._xattr_list(self.lab / "main.tf")["user.empty"], "")

    def test_cli6_missing_glibc_nofollow_symbols_refuse_instead_of_following(self):
        fake = mock.Mock(spec=["listxattr", "getxattr", "setxattr", "removexattr"])
        with mock.patch.object(self.p.sys, "platform", "linux"), \
                mock.patch.object(self.p, "_XATTR", fake):
            with self.assertRaises(self.p.PreservationError):
                self.p._xattr_list(self.link, False)
        fake.listxattr.assert_not_called()

    @unittest.skipUnless(ABI_XATTRS, "Darwin libc ABI required")
    def test_cli6_cli9_symlink_own_attrs_survive_archive_restore(self):
        abi_set_xattr(self.link, b"user.linkmark", b"LINKOWN", True)
        # Use the actual archive pair writer, not a migration compatibility API.
        archive = load_cloud_lab(self.repo, "archive")
        inventory = archive.p._inventory_tree(self.lab)
        self.assertEqual(inventory["linked.tf"]["xattrs"]["user.linkmark"],
                         b"LINKOWN".hex())
        paths = (self.root / "synthetic--001.zip", self.root / "synthetic--001.meta.json")
        archive.write_pair(self.lab, paths, inventory, {})
        self.assertEqual(json.loads(paths[1].read_text())["entries"]
                         ["linked.tf"]["xattrs"]["user.linkmark"], b"LINKOWN".hex())
        dest = self.root / "restored"
        archive.p.restore_archive(paths[0], dest)
        restored = dest / "linked.tf"
        self.assertTrue(restored.is_symlink())
        self.assertEqual(abi_list_xattrs(restored, True).get(b"user.linkmark"), b"LINKOWN")
        if XATTR_TOOL:
            self.assertEqual(native_link_xattrs(restored).get("user.linkmark"), "LINKOWN")

    def locked_tree(self):
        tree = self.root / "doomed"
        (tree / "sub").mkdir(parents=True)
        (tree / "plain.txt").write_text("y")
        (tree / "sub/f.txt").write_text("x")
        (tree / "sub").chmod(0o500)
        return tree

    def test_cli9_cleanup_removes_readonly_parent_trees(self):
        tree = self.locked_tree()
        self.p._force_rmtree(tree)
        self.assertFalse(tree.exists())

    def test_cli9_cli10_cleanup_accepts_python310_onerror_signature(self):
        tree = self.locked_tree()

        def py310_rmtree(path, ignore_errors=False, onerror=None):
            def call(func, target):
                try:
                    func(target)
                except OSError:
                    if onerror is None:
                        raise
                    onerror(func, target, sys.exc_info())
            try:
                entries = list(os.scandir(path))
            except OSError:
                if onerror is None:
                    raise
                onerror(os.scandir, path, sys.exc_info())
                return
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    py310_rmtree(entry.path, ignore_errors, onerror)
                else:
                    call(os.unlink, entry.path)
            call(os.rmdir, path)

        with mock.patch.object(self.p, "_PY312", False), \
                mock.patch.object(shutil, "rmtree", side_effect=py310_rmtree):
            self.p._force_rmtree(tree)
        self.assertFalse(tree.exists())

    def test_cli9_cleanup_retry_preserves_original_error(self):
        target = self.locked_tree() / "sub/f.txt"
        original = OSError("original unlink failure")

        def hopeless(path):
            raise OSError("retry failed too")

        with self.assertRaises(OSError) as caught:
            self.p._rmtree_step(hopeless, target, original, str(self.root))
        self.assertIs(caught.exception, original)
