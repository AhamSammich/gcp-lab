"""CLI-4..8,10: archive only disposable labs under hermetic checkouts."""

import contextlib
import io
import json
import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from util import (REPO_ROOT, load_labkit_cli, make_git_repo, run_labkit_cli,
                  run_git, set_xattr, stub_env)
from test_restore_lab import tree_facts, xattr_facts


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="archive spaced ")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = make_git_repo(self.base)
        self.lab = self.repo / "labs" / "my-lab"
        self.lab.mkdir()
        self.out = self.repo / "archives"
        self.home = self.base / "home"
        self.log = self.base / "cloud.log"
        self.env = stub_env(self.home, self.log)
        (self.home / "bin" / "git").symlink_to(shutil.which("git"))
        self.away = self.base / "outside"
        self.away.mkdir()

    def cli(self, *args, cwd=None, env=None):
        return run_labkit_cli(self.repo, cwd or self.away, env or self.env,
                             "", "archive", *args)

    def pair(self, number=1):
        z = self.out / f"my-lab--{number:03d}.zip"
        return z, z.with_suffix(".meta.json")

    def assert_pristine(self):
        self.assertTrue(self.lab.is_dir())
        self.assertEqual(list(self.out.iterdir()), [self.out / ".gitkeep"])
        self.assertFalse(self.log.exists())
        self.assertFalse((self.home / ".cache").exists())

    def inprocess(self, action=None, target=None):
        module = load_labkit_cli(self.repo, "archive")
        output = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(output):
            if target:
                with mock.patch.object(module, target, side_effect=action):
                    code = module.archive_lab("my-lab")
            else:
                code = module.archive_lab("my-lab")
        return code, output.getvalue(), module

    def test_cli8_preview_is_readonly_and_names_exact_pair_and_removal(self):
        (self.lab / "notes").write_text("work")
        before = tree_facts(self.repo)
        result = self.cli("--dry-run", cwd=self.lab)
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (self.lab, *self.pair()):
            self.assertIn(str(path), result.stdout)
        self.assertIn("remov", result.stdout.lower())
        self.assertEqual(tree_facts(self.repo), before)
        self.assert_pristine()

    def test_cli4_cli8_empty_and_notes_only_default_lab_root(self):
        for notes in (False, True):
            with self.subTest(notes=notes):
                if notes:
                    self.lab.mkdir()
                    (self.lab / "notes").write_text("finished")
                result = self.cli(cwd=self.lab)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(self.lab.exists())
                self.assertTrue(self.pair(2 if notes else 1)[0].exists())

    def test_cli5_generations_count_either_artifact_and_dangling_links(self):
        (self.out / "my-lab.zip").write_bytes(b"old")
        (self.out / "my-lab--002.zip").write_bytes(b"older")
        (self.out / "my-lab--1000.meta.json").symlink_to("missing")
        result = self.cli("my-lab")
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in self.pair(1001):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual((self.out / "my-lab.zip").read_bytes(), b"old")
        self.assertTrue((self.out / "my-lab--1000.meta.json").is_symlink())
        self.assertFalse(self.pair()[0].exists())

    def test_cli6_roundtrip_full_tree_independent_oracles_and_guidance(self):
        # CLI-6/9: retain ignored state/tfvars/cache and local task work,
        # not only visible files or nested repository metadata.
        cache = self.lab / ".terraform" / "modules"
        cache.mkdir(parents=True)
        (cache / "cache.json").write_bytes(b'{"synthetic": true}')
        (self.lab / "terraform.tfstate").write_bytes(b'{"disposable": true}')
        (self.lab / "terraform.tfvars").write_text('project_id = "synthetic"\n')
        (self.lab / "task9.tf").write_text("# local-only work\n")
        (self.lab / "empty").mkdir()
        (self.lab / ".hidden").write_bytes(bytes(range(256)) * 8193)
        (self.lab / ".hidden").chmod(0o6750)
        (self.lab / "empty").chmod(0o1750)
        (self.lab / "link").symlink_to(".hidden")
        (self.lab / "nested").mkdir()
        (self.lab / "nested" / "relative").symlink_to("../.hidden")
        run_git(self.lab / "nested", "init")
        attr = b"com.example.archive" if sys.platform == "darwin" else b"user.archive"
        set_xattr(self.lab, attr, b"root\x00data")
        set_xattr(self.lab / ".hidden", attr, b"payload")
        self.lab.chmod(0o2750)
        expected, attrs = tree_facts(self.lab), xattr_facts(self.lab)
        index = (self.repo / ".git" / "index").read_bytes()
        # RESTORE-3: guidance uses the unified CLI, with shell-safe paths.
        # CLI-11/8: final executable works from a soon-deleted lab cwd.
        result = subprocess.run([str(self.repo / "labkit"), "archive"],
                                cwd=self.lab, env=self.env, capture_output=True,
                                text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.lab.exists())
        self.assertEqual((self.repo / ".git" / "index").read_bytes(), index)
        commands = [shlex.split(line) for line in result.stdout.splitlines()
                    if line.startswith("cd ") or " restore " in line]
        restore = next(c for c in commands if c[1] == "restore")
        self.assertEqual(restore[:2], [str(self.repo / "labkit"), "restore"])
        self.assertTrue(all(Path(p).is_absolute() for p in (restore[0], *restore[2:])))
        restored = subprocess.run(restore, cwd=self.away, env=self.env,
                                  capture_output=True, text=True)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(tree_facts(Path(restore[-1])), expected)
        self.assertEqual(xattr_facts(Path(restore[-1])), attrs)
        self.assertIn(["cd", str(self.repo)], commands)
        self.assertFalse(self.log.exists())

    def test_cli4_refuses_unsafe_targets_collections_and_nonignored_output(self):
        for target in ("../my-lab", str(self.repo), str(self.repo / "labs"),
                       "missing", str(self.lab / "inside")):
            with self.subTest(target=target):
                self.assertEqual(self.cli(target).returncode, 1)
                self.assert_pristine()
        alias = self.repo / "labs" / "alias"
        alias.symlink_to(self.lab, target_is_directory=True)
        self.assertEqual(self.cli("alias").returncode, 1)
        alias.unlink()
        for collection in (self.repo / "labs", self.out):
            moved = collection.with_name(collection.name + "-saved")
            collection.rename(moved)
            collection.symlink_to(moved, target_is_directory=True)
            self.assertEqual(self.cli("my-lab").returncode, 1)
            collection.unlink()
            moved.rename(collection)
        (self.repo / ".gitignore").write_text("")
        self.assertEqual(self.cli("my-lab").returncode, 1)
        self.assert_pristine()

    def test_cli4_head_and_index_paths_and_gitlink_intersections(self):
        # CLI-4: HEAD protection survives staged deletion; index-only paths
        # and gitlinks above OR below the source/output remain protected.
        oid = run_git(self.repo, "rev-parse", "HEAD").stdout.strip()
        cases = [("labs/my-lab/note", False),
                 ("archives/my-lab--001.zip", False),
                 ("archives/my-lab--001.meta.json", True),
                 ("labs/my-lab/note", True)]
        for rel, head in cases:
            with self.subTest(rel=rel, head=head):
                path = self.repo / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("protected")
                run_git(self.repo, "add", "-f", "--", rel)
                if head:
                    run_git(self.repo, "commit", "-m", "protected", "--no-verify")
                    run_git(self.repo, "rm", "--cached", "--", rel)
                if path.parent == self.out:
                    path.unlink()  # output absent, but still tracked
                self.assertEqual(self.cli("my-lab", "--dry-run").returncode, 1)
                run_git(self.repo, "reset", "--hard", oid)
                self.lab.mkdir(exist_ok=True)
                if os.path.lexists(path):
                    path.unlink()
        for rel in ("labs", "labs/my-lab", "labs/my-lab/nested",
                    "archives", "archives/my-lab--001.zip/child"):
            with self.subTest(gitlink=rel):
                run_git(self.repo, "update-index", "--add", "--cacheinfo",
                        f"160000,{oid},{rel}")
                before = (self.repo / ".git" / "index").read_bytes()
                self.assertEqual(self.cli("my-lab").returncode, 1)
                self.assertEqual((self.repo / ".git" / "index").read_bytes(), before)
                run_git(self.repo, "reset", "--mixed", oid)
        self.assert_pristine()

    def test_cli4_git_environment_cannot_redirect_protection(self):
        (self.lab / "notes").write_text("tracked")
        run_git(self.repo, "add", "-f", "labs/my-lab/notes")
        otherbase = self.base / "other"
        otherbase.mkdir()
        other = make_git_repo(otherbase)
        env = dict(self.env, GIT_DIR=str(other / ".git"),
                   GIT_WORK_TREE=str(other), GIT_INDEX_FILE=str(other / "fake-index"),
                   GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.bare",
                   GIT_CONFIG_VALUE_0="true")
        result = self.cli("my-lab", env=env)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assert_pristine()

    def test_cli4_equivalent_case_tracked_source_cannot_bypass_guard(self):
        # CLI-4: on case-folding filesystems HEAD/index names can spell
        # the very same source differently. Conservative refusal is safe
        # on sensitive filesystems too; no tracked spelling is discarded.
        (self.lab / "notes").write_text("tracked work")
        blob = run_git(self.repo, "hash-object", "-w", "labs/my-lab/notes").stdout.strip()
        run_git(self.repo, "update-index", "--add", "--cacheinfo",
                f"100644,{blob},LABS/my-lab/notes")
        before = (self.repo / ".git" / "index").read_bytes()
        result = self.cli("my-lab")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual((self.repo / ".git" / "index").read_bytes(), before)
        self.assert_pristine()

    def test_cli6_unsupported_entries_and_escaping_links_refused_readonly(self):
        # CLI-6/9: retain absolute-link refusal as well as relative escapes.
        for kind in ("fifo", "escape", "absolute", "cycle", "badname"):
            with self.subTest(kind=kind):
                path = self.lab / ("bad:name" if kind == "badname" else "entry")
                if kind == "fifo":
                    os.mkfifo(path)
                elif kind == "escape":
                    path.symlink_to("../../outside")
                elif kind == "absolute":
                    path.symlink_to(self.base / "outside-file")
                elif kind == "cycle":
                    path.symlink_to("entry")
                else:
                    path.write_text("cannot preserve")
                for args in (("my-lab",), ("my-lab", "--dry-run")):
                    self.assertEqual(self.cli(*args).returncode, 1)
                    self.assert_pristine()
                path.unlink()

    def test_cli6_metadata_only_unsafe_names_refused_before_writes(self):
        # CLI-6/7/9: metadata-only dirs/links must remain restorable.
        for kind, name in (("dir", "empty:dir"), ("link", "li:nk.tf")):
            with self.subTest(kind=kind):
                path = self.lab / name
                if kind == "dir":
                    path.mkdir()
                else:
                    path.symlink_to("missing")
                before = tree_facts(self.lab)
                for flags in ((), ("--dry-run",)):
                    result = self.cli("my-lab", *flags)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(tree_facts(self.lab), before)
                    self.assert_pristine()
                if kind == "dir":
                    path.rmdir()
                else:
                    path.unlink()

    def test_cli6_unreadable_file_and_directory_refused_before_writes(self):
        # CLI-6/7: incomplete inventories cannot authorize removal.
        locked = self.lab / "locked"
        locked.mkdir()
        secret = locked / "secret"
        secret.write_bytes(b"keep")
        self.addCleanup(locked.chmod, 0o700)
        self.addCleanup(secret.chmod, 0o600)
        for path, mode in ((secret, 0o600), (locked, 0o700)):
            with self.subTest(path=path.name):
                path.chmod(0)
                try:
                    result = self.cli("my-lab")
                    self.assertEqual(result.returncode, 1)
                    self.assert_pristine()
                finally:
                    path.chmod(mode)
                self.assertEqual(secret.read_bytes(), b"keep")

    def test_cli6_case_unicode_link_chains_and_cycles_refuse_source(self):
        # CLI-6/7/9: case/normalization ambiguity matters at SOURCE too.
        for name, target in (("link", "link/.."), ("link", "LINK/.."),
                             ("\u00e9", "e\u0301/.."), ("loop", "loop")):
            with self.subTest(name=name, target=target):
                link = self.lab / name
                link.symlink_to("loop" if name == "loop" else ".")
                escape = self.lab / "escape"
                escape.symlink_to(target)
                before = tree_facts(self.lab)
                self.assertEqual(self.cli("my-lab").returncode, 1)
                self.assertEqual(tree_facts(self.lab), before)
                self.assert_pristine()
                escape.unlink()
                link.unlink()

    def test_cli7_readonly_directory_archives_and_removes_with_modes_preserved(self):
        # CLI-6/7: readonly source deletion must not lose its original mode.
        locked = self.lab / "readonly"
        locked.mkdir()
        (locked / "file").write_bytes(b"keep")
        locked.chmod(0o500)
        result = self.cli("my-lab")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.lab.exists())
        entries = json.loads(self.pair()[1].read_text())["entries"]
        self.assertEqual(entries["readonly"]["mode"], 0o500)
        self.assertEqual(entries["readonly/file"]["type"], "file")

    def test_cli7_write_verify_and_source_recheck_failures_keep_source(self):
        for step in ("write_pair", "verify_pair", "remove_source"):
            # CLI-7: failure before cleanup removes owned outputs; once
            # cleanup starts the verified pair is the recovery boundary.
            with self.subTest(step=step):
                code, text, _ = self.inprocess(OSError("injected cause"), step)
                self.assertEqual(code, 1)
                self.assertIn("injected cause", text)
                self.assertTrue(self.lab.exists())
                for path in self.pair():
                    self.assertEqual(path.exists(), step == "remove_source")
                    if path.exists():
                        path.unlink()

    def test_cli7_interrupts_preserve_recovery_after_cleanup_starts(self):
        for step in ("write_pair", "verify_pair", "remove_source"):
            with self.subTest(step=step):
                code, text, _ = self.inprocess(KeyboardInterrupt(), step)
                self.assertEqual(code, 130)
                self.assertTrue(self.lab.exists())
                for path in self.pair():
                    self.assertEqual(path.exists(), step == "remove_source")
                    if path.exists():
                        self.assertIn(str(path), text)
                        path.unlink()
                if step == "remove_source":
                    # RESTORE-3: interruption guidance uses labkit restore too.
                    self.assertIn(" restore ", text)
                    self.assertIn(str(self.lab), text)

    def test_cli7_changes_to_source_or_pair_before_deletion_refuse(self):
        # CLI-7/9: retain deleted, malformed and structurally corrupt sidecar
        # regressions at the second verification boundary, not just the first.
        for change in ("source", "metadata", "payload", "deleted-sidecar",
                       "invalid-json", "invalid-entry"):
            with self.subTest(change=change):
                module = load_labkit_cli(self.repo, "archive")
                real_verify = module.verify_pair
                calls = 0

                def verify(*args):
                    nonlocal calls
                    real_verify(*args)
                    calls += 1
                    if calls == 1:
                        if change == "source":
                            (self.lab / "new").write_text("changed")
                        elif change == "metadata":
                            self.pair()[1].write_text("{}")
                        elif change == "deleted-sidecar":
                            self.pair()[1].unlink()
                        elif change == "invalid-json":
                            self.pair()[1].write_text("{not json")
                        elif change == "invalid-entry":
                            data = json.loads(self.pair()[1].read_text())
                            data["entries"][""] = "not a facts mapping"
                            self.pair()[1].write_text(json.dumps(data))
                        else:
                            self.pair()[0].write_bytes(b"corrupt")

                with mock.patch.dict(os.environ, self.env, clear=True), \
                        mock.patch.object(module, "verify_pair", side_effect=verify), \
                        contextlib.redirect_stderr(io.StringIO()), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(module.archive_lab("my-lab"), 1)
                self.assertTrue(self.lab.exists())
                self.assertFalse(any(p.exists() for p in self.pair()))
                if (self.lab / "new").exists():
                    (self.lab / "new").unlink()

    def test_cli7_collision_and_replaced_artifact_never_unlinked(self):
        module = load_labkit_cli(self.repo, "archive")
        real_write = module.write_pair

        def collide(*args):
            self.pair()[0].write_bytes(b"collision")
            real_write(*args)

        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(module, "write_pair", side_effect=collide), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.archive_lab("my-lab"), 1)
        self.assertEqual(self.pair()[0].read_bytes(), b"collision")
        self.pair()[0].unlink()

        def replace(*args):
            replacement = self.out / "replacement"
            replacement.write_bytes(b"not ours")
            replacement.replace(self.pair()[0])
            raise OSError("verification refused")

        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(module, "verify_pair", side_effect=replace), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.archive_lab("my-lab"), 1)
        self.assertEqual(self.pair()[0].read_bytes(), b"not ours")
        self.assertFalse(self.pair()[1].exists())
        self.assertTrue(self.lab.exists())

    def test_cli7_cleanup_does_not_chmod_hardlinked_outside_file(self):
        outside = self.base / "outside-file"
        outside.write_text("outside")
        outside.chmod(0o440)
        os.link(outside, self.lab / "linked")
        (self.lab / "locked").mkdir()
        (self.lab / "locked" / "notes").write_text("inside")
        (self.lab / "locked").chmod(0o500)
        result = self.cli("my-lab")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outside.read_text(), "outside")
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o440)

    def test_cli5_outputs_are_0600_even_under_restrictive_umask(self):
        # CLI-5: explicit owner-only modes, not a caller umask accident.
        old = os.umask(0o777)
        try:
            code, text, _ = self.inprocess()
        finally:
            os.umask(old)
        self.assertEqual(code, 0, text)
        for path in self.pair():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_cli7_partial_cleanup_interrupt_keeps_pair_and_original_cause(self):
        # CLI-7/8: actual destructive progress, then Ctrl-C; remnant and
        # complete pair must remain and guidance must run outside the repo.
        (self.lab / "first").write_text("one")
        (self.lab / "second").write_text("two")
        module = load_labkit_cli(self.repo, "archive")
        unlink = os.unlink

        def interrupt_after_unlink(path, *args, **kwargs):
            unlink(path, *args, **kwargs)
            raise KeyboardInterrupt()

        output = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(module.os, "unlink", side_effect=interrupt_after_unlink), \
                contextlib.redirect_stderr(output):
            self.assertEqual(module.archive_lab("my-lab"), 130)
        self.assertEqual(len(list(self.lab.iterdir())), 1)
        self.assertTrue(all(p.exists() for p in self.pair()))
        text = output.getvalue()
        self.assertIn(str(self.lab), text)
        self.assertIn("Ctrl-C", text)
        # RESTORE-3: partial-failure recovery uses the same command as success.
        command = next(shlex.split(line) for line in text.splitlines()
                       if " restore " in line)
        self.assertEqual(command[:2], [str(self.repo / "labkit"), "restore"])
        result = subprocess.run(command, cwd=self.away, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = Path(command[-1])
        self.assertEqual((dest / "first").read_text(), "one")
        self.assertEqual((dest / "second").read_text(), "two")

    def test_type2_cleanup_and_guidance_failure_keep_pair_and_original_error(self):
        # TYPE-2: reporting cannot mask a destructive failure or remove recovery.
        module = load_labkit_cli(self.repo, "archive")
        output = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(module, "remove_source", side_effect=OSError("cleanup sentinel")), \
                mock.patch.object(module, "_guidance", side_effect=RuntimeError("report sentinel")), \
                contextlib.redirect_stderr(output):
            self.assertEqual(module.archive_lab("my-lab"), 1)
        self.assertTrue(self.lab.exists())
        self.assertTrue(all(path.exists() for path in self.pair()))
        self.assertIn("cleanup sentinel", output.getvalue())
        self.assertIn("report sentinel", output.getvalue())
        for path in self.pair():
            self.assertIn(str(path), output.getvalue())

    def test_cli7_interrupt_at_exclusive_creation_keeps_source_cleans_owned_only(self):
        # CLI-5/7: SIGINT in open/ownership-registration gap is deferred;
        # sidecar collision remains untouched if its creation is interrupted.
        module = load_labkit_cli(self.repo, "archive")
        real_open = os.open
        for selected in self.pair():
            with self.subTest(selected=selected):
                def open_and_interrupt(path, *args, **kwargs):
                    fd = real_open(path, *args, **kwargs)
                    if Path(path) == selected:
                        signal.raise_signal(signal.SIGINT)
                    return fd

                with mock.patch.dict(os.environ, self.env, clear=True), \
                        mock.patch.object(module.os, "open", side_effect=open_and_interrupt), \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(module.archive_lab("my-lab"), 130)
                self.assert_pristine()

    def test_cli6_streaming_reads_and_zip64_roundtrip(self):
        # CLI-6: both hashing and writing bound every source read; force
        # ZIP64 at a tiny limit so this is exercised without huge fixtures.
        (self.lab / "large").write_bytes(b"payload" * 10000)
        module = load_labkit_cli(self.repo, "archive")
        real_open = open
        sizes = []

        class BoundedReader:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def read(self, size=-1):
                self_outer.assertGreater(size, 0)
                self_outer.assertLessEqual(size, 1 << 20)
                sizes.append(size)
                return self.stream.read(size)

        self_outer = self

        def guarded_open(path, *args, **kwargs):
            stream = real_open(path, *args, **kwargs)
            if Path(path) == self.lab / "large":
                return BoundedReader(stream)
            return stream

        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch("builtins.open", side_effect=guarded_open), \
                mock.patch.object(module.zipfile, "ZIP64_LIMIT", 100), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.archive_lab("my-lab"), 0)
        self.assertGreater(len(sizes), 3)
        with zipfile.ZipFile(self.pair()[0]) as archive:
            self.assertGreaterEqual(archive.getinfo("large").extract_version, 45)
            self.assertEqual(archive.read("large"), b"payload" * 10000)

    def test_cli7_complete_inventory_mismatch_never_deletes_source(self):
        # CLI-6/7: not merely hashes; every sidecar fact and the full payload
        # entry set are compared to the ORIGINAL inventory after reopening.
        (self.lab / "notes").write_text("work")
        for change in ("root-mode", "root-xattrs", "entry-type", "entry-set",
                       "file-mode", "zip-mode", "extra-payload", "bad-hash"):
            with self.subTest(change=change):
                module = load_labkit_cli(self.repo, "archive")
                write = module.write_pair

                def changed_pair(*args):
                    write(*args)
                    z, meta = self.pair()
                    data = json.loads(meta.read_text())
                    if change == "root-mode":
                        data["entries"][""]["mode"] ^= 0o100
                    elif change == "root-xattrs":
                        data["entries"][""]["xattrs"]["user.added"] = "00"
                    elif change == "entry-type":
                        data["entries"]["notes"]["type"] = "dir"
                    elif change == "entry-set":
                        del data["entries"]["notes"]
                    elif change == "file-mode":
                        data["entries"]["notes"]["mode"] ^= 0o100
                    elif change == "extra-payload":
                        with zipfile.ZipFile(z, "a") as archive:
                            archive.writestr("extra", "unexpected")
                    elif change in ("zip-mode", "bad-hash"):
                        with zipfile.ZipFile(z, "w") as archive:
                            archive.writestr("notes", "changed" if change == "bad-hash" else "work")
                    meta.write_text(json.dumps(data))

                with mock.patch.dict(os.environ, self.env, clear=True), \
                        mock.patch.object(module, "write_pair", side_effect=changed_pair), \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(module.archive_lab("my-lab"), 1)
                self.assertEqual((self.lab / "notes").read_text(), "work")
                self.assertFalse(any(p.exists() for p in self.pair()))

    def test_cli7_midwrite_failures_clean_created_zip_and_sidecar(self):
        # CLI-7: exercise failure AFTER exclusive creation, not only a
        # failure before the writer starts. Source bytes always survive.
        (self.lab / "notes").write_text("precious work")
        for sidecar in (False, True):
            with self.subTest(sidecar=sidecar):
                module = load_labkit_cli(self.repo, "archive")
                patch = (mock.patch.object(module.json, "dumps",
                                          side_effect=OSError("sidecar write cause"))
                         if sidecar else
                         mock.patch.object(module.zipfile.ZipFile, "open",
                                           side_effect=OSError("payload write cause")))
                text = io.StringIO()
                with mock.patch.dict(os.environ, self.env, clear=True), patch, \
                        contextlib.redirect_stderr(text):
                    self.assertEqual(module.archive_lab("my-lab"), 1)
                self.assertIn("write cause", text.getvalue())
                self.assertEqual((self.lab / "notes").read_text(), "precious work")
                self.assert_pristine()

    def test_cli7_cleanup_failure_reports_exact_owned_remnants_without_masking_cause(self):
        module = load_labkit_cli(self.repo, "archive")
        text = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(module, "verify_pair", side_effect=OSError("original cause")), \
                mock.patch.object(module.os, "unlink", side_effect=OSError("cleanup cause")), \
                contextlib.redirect_stderr(text):
            self.assertEqual(module.archive_lab("my-lab"), 1)
        for path in (self.lab, *self.pair()):
            self.assertIn(str(path), text.getvalue())
            self.assertTrue(path.exists())
        self.assertIn("original cause", text.getvalue())
        self.assertIn("cleanup cause", text.getvalue())

    def test_cli7_sidecar_collision_preserves_collision_cleans_only_own_zip(self):
        module = load_labkit_cli(self.repo, "archive")
        real_write = module.write_pair

        def collide(*args):
            self.pair()[1].symlink_to("missing")
            real_write(*args)

        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(module, "write_pair", side_effect=collide), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.archive_lab("my-lab"), 1)
        self.assertTrue(self.pair()[1].is_symlink())
        self.assertEqual(os.readlink(self.pair()[1]), "missing")
        self.assertFalse(self.pair()[0].exists())
        self.assertTrue(self.lab.exists())

    def test_cli4_success_changes_no_siblings_outside_files_or_home(self):
        # CLI-4/8: archive never runs reset or cloud tools or writes logs.
        sibling = self.repo / "labs" / "other-lab"
        sibling.mkdir()
        (sibling / "terraform.tfstate").write_text("sibling state")
        (self.home / "credentials").write_text("disposable sentinel")
        (self.away / "outside-data").write_text("untouched")
        (self.lab / "terraform.tfstate").write_text("archived state")
        before = [tree_facts(p) for p in (sibling, self.home, self.away)]
        result = self.cli("my-lab")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([tree_facts(p) for p in (sibling, self.home, self.away)], before)
        self.assertFalse(self.log.exists())
        with zipfile.ZipFile(self.pair()[0]) as archive:
            self.assertEqual(archive.read("terraform.tfstate"), b"archived state")


if __name__ == "__main__":
    unittest.main()
