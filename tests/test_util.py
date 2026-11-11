"""CLI-10: fixture isolation and independent preservation-oracle regressions."""

import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import util


class GitFixtureSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Even the RED tests may only redirect Git into disposable sentinels.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(HOME=str(self.root), XDG_CONFIG_HOME=str(self.root / "xdg"),
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                   GIT_CONFIG_NOSYSTEM="1")
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.outside = self.root / "outside"
        self.outside.mkdir()
        util.run_git(self.outside, "init")
        util.run_git(self.outside, "config", "user.email", "sentinel@example.com")
        util.run_git(self.outside, "config", "user.name", "Sentinel")
        (self.outside / ".gitignore").write_text("# outside sentinel\n")
        util.run_git(self.outside, "add", ".gitignore")
        util.run_git(self.outside, "commit", "-m", "sentinel", "--no-verify")

    def test_cli10_ambient_index_cannot_be_written_by_fixture_creation(self):
        before = util.snapshot_nofollow(self.outside)
        with mock.patch.dict(os.environ, {
            "GIT_INDEX_FILE": str(self.outside / ".git" / "index"),
        }):
            repo = util.make_git_repo(self.root)
        self.assertEqual(util.snapshot_nofollow(self.outside), before)
        self.assertEqual(util.run_git(repo, "ls-files").stdout, ".gitignore\n")

    def test_cli10_ambient_worktree_cannot_supply_fixture_contents(self):
        repo = util.make_git_repo(self.root)
        (repo / "notes").write_text("fixture work\n")
        (self.outside / "notes").write_text("outside sentinel\n")
        before = util.snapshot_nofollow(self.outside)
        with mock.patch.dict(os.environ, {"GIT_WORK_TREE": str(self.outside)}):
            util.run_git(repo, "add", "notes")
        self.assertEqual(util.snapshot_nofollow(self.outside), before)
        self.assertEqual(util.run_git(repo, "show", ":notes").stdout,
                         (repo / "notes").read_text())

    def test_cli10_ambient_repository_overrides_cannot_change_other_repos(self):
        for key, path in (
            ("GIT_DIR", self.outside / ".git"),
            ("GIT_COMMON_DIR", self.outside / ".git"),
            ("GIT_OBJECT_DIRECTORY", self.outside / ".git" / "objects"),
        ):
            with self.subTest(override=key):
                parent = self.root / key
                parent.mkdir()
                before = util.snapshot_nofollow(self.outside)
                with mock.patch.dict(os.environ, {key: str(path)}):
                    repo = util.make_git_repo(parent)
                self.assertEqual(util.snapshot_nofollow(self.outside), before)
                self.assertEqual(util.run_git(repo, "ls-files").stdout,
                                 ".gitignore\n")

    def test_cli10_fixture_operations_ignore_ambient_git_configuration(self):
        config = "[fixture]\n\tmarker = ambient\n"
        for source in ("home", "xdg", "global", "system", "injected"):
            with self.subTest(source=source):
                parent = self.root / source
                parent.mkdir()
                home, xdg = parent / "home", parent / "xdg"
                home.mkdir()
                (xdg / "git").mkdir(parents=True)
                config_path = parent / "ambient.config"
                config_path.write_text(config)
                env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(xdg))
                if source in ("home", "xdg", "global"):
                    env.pop("GIT_CONFIG_GLOBAL")
                    if source == "home":
                        (home / ".gitconfig").write_text(config)
                    elif source == "xdg":
                        (xdg / "git" / "config").write_text(config)
                    else:
                        env["GIT_CONFIG_GLOBAL"] = str(config_path)
                elif source == "system":
                    env.pop("GIT_CONFIG_NOSYSTEM")
                    env["GIT_CONFIG_SYSTEM"] = str(config_path)
                else:
                    env.update(GIT_CONFIG_COUNT="1",
                               GIT_CONFIG_KEY_0="fixture.marker",
                               GIT_CONFIG_VALUE_0="ambient")
                with mock.patch.dict(os.environ, env, clear=True):
                    repo = util.make_git_repo(parent)
                    result = util.run_git(repo, "config", "--get", "fixture.marker",
                                          check=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(config_path.read_text(), config)


class XattrOracleTests(unittest.TestCase):
    def test_cli6_cli10_linux_oracle_reads_link_or_referent_as_requested(self):
        # Nonempty LINK attrs exercise lgetxattr even where Linux forbids
        # setting user.* attrs on real symlinks.
        libc = mock.Mock(spec=["listxattr", "getxattr", "llistxattr", "lgetxattr"])

        def listing(path, buf, size):
            payload = b"user.mark\0"
            if buf is not None:
                ctypes.memmove(buf, payload, len(payload))
            return len(payload)

        def reading(value):
            def read(path, name, buf, size):
                if buf is not None:
                    ctypes.memmove(buf, value, len(value))
                return len(value)
            return read

        libc.listxattr.side_effect = libc.llistxattr.side_effect = listing
        libc.getxattr.side_effect = reading(b"REFERENT")
        libc.lgetxattr.side_effect = reading(b"LINKOWN")
        with mock.patch.object(util.sys, "platform", "linux"), \
                mock.patch.object(util, "_XATTR_LIBC", libc):
            self.assertEqual(util.list_xattrs("link", follow_symlinks=False),
                             {b"user.mark": b"LINKOWN"})
            libc.listxattr.assert_not_called()
            libc.getxattr.assert_not_called()
            self.assertEqual(util.list_xattrs("link", follow_symlinks=True),
                             {b"user.mark": b"REFERENT"})

    def test_cli6_cli10_linux_oracle_refuses_missing_nofollow_support(self):
        libc = mock.Mock(spec=["listxattr", "getxattr", "setxattr"])
        libc.listxattr.return_value = 0
        with mock.patch.object(util.sys, "platform", "linux"), \
                mock.patch.object(util, "_XATTR_LIBC", libc):
            with self.assertRaises(RuntimeError):
                util.list_xattrs("link", follow_symlinks=False)
        libc.listxattr.assert_not_called()
        libc.getxattr.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux") and util.XATTRS,
                         "native Linux xattrs required")
    def test_cli6_cli10_native_linux_oracle_does_not_follow_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target.write_bytes(b"payload")
            os.setxattr(target, b"user.referent", b"REFERENT")
            link = root / "link"
            link.symlink_to("target")
            expected = {os.fsencode(name): os.getxattr(link, name, follow_symlinks=False)
                        for name in os.listxattr(link, follow_symlinks=False)}
            self.assertNotIn(b"user.referent", expected)
            self.assertEqual(util.list_xattrs(link, False), expected)
            self.assertEqual(util.list_xattrs(link, True)[b"user.referent"], b"REFERENT")
            target.unlink()
            self.assertEqual(util.list_xattrs(link, False), expected)
            with self.assertRaises(OSError):
                util.list_xattrs(link, True)
