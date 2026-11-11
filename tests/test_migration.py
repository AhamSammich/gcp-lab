"""Migration contract tests for scripts/migrate_002.py - RED until Phase 3.

These pin the one-time migration API defined in the helper's module
docstring (plan / run / verify_archive / restore_archive plus the
exception types) against disposable synthetic git repositories. No
real lab, archive, credential, or index is ever touched.

The fixtures reproduce the preservation hazards the real labs carry:
tracked source mixed with untracked task files and ignored state,
hidden files, executable/read-only/0600 modes, relative symlinks,
nested .git metadata, and terraform caches. Every preservation
assertion compares recoverable facts (type, permission bits, link
targets, content hashes), not just file presence.
"""

import contextlib
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

from util import (REPO_ROOT, XATTRS, list_xattrs, load_migrate_002,
                  make_git_repo, run_git, set_xattr)

migrate = load_migrate_002()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_facts(root: Path) -> dict:
    """relpath -> recoverable facts: type, mode, symlink target, hash.

    The lab root itself is recorded under "" so root directory
    metadata is compared too. Directory and symlink permission bits
    are facts like any other - including the setuid/setgid/sticky
    group (0o7000), which the migration records and restores - and
    entries that are neither regular files, directories, nor
    symlinks are recorded as "special" without being read (a FIFO
    would block a naive read). Hashes are skipped (None) for
    unreadable files so before/after comparisons work around
    fixtures that lock a file mid-test.
    """
    root = Path(root)
    facts = {"": {"type": "dir", "mode": stat.S_IMODE(root.stat().st_mode)}}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        st = os.lstat(p)
        if p.is_symlink():
            facts[rel] = {"type": "symlink", "target": os.readlink(p),
                          "mode": stat.S_IMODE(st.st_mode)}
        elif p.is_dir():
            facts[rel] = {"type": "dir", "mode": stat.S_IMODE(st.st_mode)}
        elif p.is_file():
            try:
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
            except PermissionError:
                digest = None
            facts[rel] = {"type": "file", "mode": stat.S_IMODE(st.st_mode),
                          "sha256": digest}
        else:
            facts[rel] = {"type": "special"}
    return facts


def file_hashes(facts: dict) -> dict:
    """The {relpath: sha256} map verify_archive expects."""
    return {rel: f["sha256"] for rel, f in facts.items()
            if f["type"] == "file" and f["sha256"] is not None}


def build_migration_repo(parent: Path) -> Path:
    """A synthetic repository shaped like the real one at Release time.

    Tracked labs carry tracked source plus local-only additions
    (untracked task file, ignored state, hidden files, an executable,
    a read-only file, a 0600 file, a relative symlink, nested .git
    metadata, a terraform cache). One lab is wholly untracked. Tool
    files, a template, tests, and a virtualenv must never be
    classified as labs. Two unrelated tool changes are pre-staged.
    """
    repo = parent / "repo"
    repo.mkdir()
    shutil.copy2(REPO_ROOT / ".gitignore", repo / ".gitignore")
    run_git(repo, "init")
    run_git(repo, "config", "user.email", "lab-tests@example.com")
    run_git(repo, "config", "user.name", "Lab Tests")

    # tool files that must survive the migration untouched
    (repo / "lab-start").write_text("#!/usr/bin/env python3\n# tool\n")
    (repo / "README.md").write_text("# gcp-lab scaffolding tool\n")
    template = repo / "templates" / "new-standard-lab"
    template.mkdir(parents=True)
    (template / "backend.tf").write_text("# template backend\n")
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_synthetic.py").write_text("# synthetic test\n")
    venv = repo / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "py.py").write_text("")  # excluded tooling

    # tracked lab A: tracked source + rich local-only additions
    lab_a = repo / "tracked-lab-a"
    lab_a.mkdir()
    (lab_a / "main.tf").write_text("# lab a\n")                      # tracked
    (lab_a / "task9.tf").write_text("# local work\n")                # untracked
    (lab_a / "terraform.tfstate").write_text('{"a": 1}')             # ignored
    (lab_a / ".hidden-note").write_text("hidden\n")                  # hidden
    module = lab_a / "modules" / "shared"
    module.mkdir(parents=True)
    (module / "main.tf").write_text("# user module\n")
    run_sh = lab_a / "run.sh"
    run_sh.write_text("#!/bin/sh\necho hi\n")
    run_sh.chmod(0o755)                                              # executable
    readonly = lab_a / "readonly.tf"
    readonly.write_text("# read-only\n")
    readonly.chmod(0o444)                                            # read-only
    secret = lab_a / "private.env"
    secret.write_text("token\n")
    secret.chmod(0o600)                                              # 0600
    (lab_a / "linked.tf").symlink_to("main.tf")                      # relative symlink
    nested_git = lab_a / ".git"                                      # nested repo metadata
    nested_git.mkdir()
    (nested_git / "config").write_text("[core]\n")
    cache = lab_a / ".terraform" / "modules"                        # ignored cache
    cache.mkdir(parents=True)
    (cache / "cache.json").write_text("{}")

    # tracked lab B: plain, so interruption ordering can be observed
    lab_b = repo / "tracked-lab-b"
    lab_b.mkdir()
    (lab_b / "main.tf").write_text("# lab b\n")

    # wholly untracked lab with local metadata
    lab_u = repo / "untracked-lab"
    lab_u.mkdir()
    (lab_u / "task1.tf").write_text("# work\n")
    (lab_u / ".env-local").write_text("secret\n")                    # hidden+ignored
    (lab_u / "terraform.tfvars").write_text('project_id = "p"\n')    # ignored
    (lab_u / "linked.tf").symlink_to("task1.tf")
    u_sh = lab_u / "run.sh"
    u_sh.write_text("#!/bin/sh\n")
    u_sh.chmod(0o755)
    u_key = lab_u / "private.key"
    u_key.write_text("k\n")
    u_key.chmod(0o600)

    # commit tools + tracked labs only; the untracked lab stays wholly
    # untracked, and untracked/ignored files inside lab A stay local
    run_git(repo, "add", ".gitignore", "lab-start", "README.md",
            "templates/new-standard-lab/backend.tf", "tests/test_synthetic.py",
            "tracked-lab-a/main.tf", "tracked-lab-b/main.tf")
    run_git(repo, "commit", "-m", "synthetic fixture", "--no-verify")

    # unrelated pre-staged tool changes the migration must not touch
    (repo / "README.md").write_text("# gcp-lab scaffolding tool\n\nstaged edit\n")
    run_git(repo, "add", "README.md")
    (repo / "notes.txt").write_text("staged notes\n")
    run_git(repo, "add", "notes.txt")
    return repo


class MigrationPlanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = build_migration_repo(Path(self._tmp.name))

    def test_classification_of_mixed_contents(self):
        """A lab with tracked source is tracked, however much local work it holds."""
        migration_plan = migrate.plan(self.repo)
        self.assertEqual(migration_plan.tracked,
                         ["tracked-lab-a", "tracked-lab-b"])
        self.assertEqual(migration_plan.untracked, ["untracked-lab"])
        self.assertEqual(migration_plan.collisions, [])
        # tooling, templates, tests, and virtualenvs are never labs:
        # pinned by the exact lists above


class MigrationRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = build_migration_repo(self.root)

    def index_state(self):
        return (run_git(self.repo, "ls-files").stdout,
                run_git(self.repo, "diff", "--cached", "--name-only").stdout)

    def test_destination_collisions_stop_migration_before_changes(self):
        (self.repo / "labs").mkdir()
        (self.repo / "labs" / "untracked-lab").mkdir()
        (self.repo / "archives").mkdir()
        (self.repo / "archives" / "tracked-lab-a.zip").write_bytes(b"stale zip")
        before_tree = tree_facts(self.repo)
        before_index = self.index_state()
        migration_plan = migrate.plan(self.repo)
        self.assertEqual(migration_plan.collisions,
                         ["tracked-lab-a", "untracked-lab"])
        with self.assertRaises(migrate.DestinationCollision):
            migrate.run(self.repo, migration_plan)
        self.assertEqual(tree_facts(self.repo), before_tree)
        self.assertEqual(self.index_state(), before_index)

    def test_valid_existing_archive_still_collides(self):
        """Even a valid, inventory-verified archive is a collision.

        "Destination already exists: stop" admits no validity
        exception. Accepting a pre-existing archive - even one that
        reopens and matches the preservation inventory - would need
        an explicit audited resume plan that does not exist yet.
        """
        facts_a = tree_facts(self.repo / "tracked-lab-a")
        (self.repo / "archives").mkdir()
        dest = self.repo / "archives" / "tracked-lab-a.zip"
        # hand-build an archive carrying exactly lab A's files with
        # their real contents: as good as a real one
        with zipfile.ZipFile(dest, "w") as z:
            for rel, entry in sorted(facts_a.items()):
                if entry["type"] == "file" and entry["sha256"] is not None:
                    z.writestr(rel, (self.repo / "tracked-lab-a" / rel).read_bytes())
        # it is genuinely valid: it reopens and every expected entry
        # hash matches the preservation inventory
        migrate.verify_archive(dest, file_hashes(facts_a))
        before_tree = tree_facts(self.repo)
        before_index = self.index_state()
        migration_plan = migrate.plan(self.repo)
        self.assertIn("tracked-lab-a", migration_plan.collisions)
        with self.assertRaises(migrate.DestinationCollision):
            migrate.run(self.repo, migration_plan)
        # nothing was overwritten, merged, or untracked
        self.assertEqual(tree_facts(self.repo), before_tree)
        self.assertEqual(self.index_state(), before_index)

    def test_run_archives_moves_and_untracks(self):
        facts_a = tree_facts(self.repo / "tracked-lab-a")
        facts_u = tree_facts(self.repo / "untracked-lab")
        # blob ids + modes of the unrelated pre-staged tool changes,
        # captured before the migration to prove they are untouched
        tool_stage_before = run_git(self.repo, "ls-files", "--stage", "--",
                                    "README.md", "notes.txt").stdout
        report = migrate.run(self.repo)
        self.assertEqual(report.archived, ["tracked-lab-a", "tracked-lab-b"])
        self.assertEqual(report.moved, ["untracked-lab"])
        # the moved untracked lab: whole tree, links and modes included
        self.assertEqual(tree_facts(self.repo / "labs" / "untracked-lab"), facts_u)
        self.assertFalse((self.repo / "untracked-lab").exists())
        # tracked labs: local verified archives, sources retired only after
        self.assertTrue((self.repo / "archives" / "tracked-lab-a.zip").is_file())
        self.assertTrue((self.repo / "archives" / "tracked-lab-b.zip").is_file())
        # each archive ships with its preservation sidecar next to it
        self.assertTrue((self.repo / "archives" / "tracked-lab-a.meta.json").is_file())
        self.assertTrue((self.repo / "archives" / "tracked-lab-b.meta.json").is_file())
        self.assertFalse((self.repo / "tracked-lab-a").exists())
        self.assertFalse((self.repo / "tracked-lab-b").exists())
        # index: no lab content; tools, template, and tests stay tracked
        files = run_git(self.repo, "ls-files").stdout.splitlines()
        self.assertFalse([f for f in files
                          if f.startswith(("tracked-lab-", "untracked-lab"))])
        for kept in ("lab-start", "README.md", "notes.txt",
                     "templates/new-standard-lab/backend.tf",
                     "tests/test_synthetic.py"):
            self.assertIn(kept, files)
        # the scoped `git rm --cached` correctly STAGES the inventoried
        # lab deletions; the exact staged set is pinned, not just "the
        # labs are gone from the index"
        self.assertEqual(
            run_git(self.repo, "diff", "--cached", "--name-status")
            .stdout.splitlines(),
            ["M\tREADME.md",
             "A\tnotes.txt",
             "D\ttracked-lab-a/main.tf",
             "D\ttracked-lab-b/main.tf"],
        )
        # the unrelated pre-staged tool changes keep their exact blobs
        # and modes next to those deletions, and their working files
        # stay intact - no automatic commit, no unstage, no re-add
        self.assertEqual(
            run_git(self.repo, "ls-files", "--stage", "--",
                    "README.md", "notes.txt").stdout,
            tool_stage_before,
        )
        self.assertIn("staged edit", (self.repo / "README.md").read_text())

    def test_archive_reopens_with_matching_hashes_and_entries(self):
        facts_a = tree_facts(self.repo / "tracked-lab-a")
        migrate.run(self.repo)
        # reopening and hash-verifying the produced archive must pass...
        migrate.verify_archive(self.repo / "archives" / "tracked-lab-a.zip",
                               file_hashes(facts_a))
        # ...and a wrong expectation must be caught
        with self.assertRaises(migrate.MigrationError):
            migrate.verify_archive(self.repo / "archives" / "tracked-lab-a.zip",
                                   {"main.tf": "0" * 64})

    def test_restore_rebuilds_tree_with_links_modes_and_hidden_data(self):
        facts_a = tree_facts(self.repo / "tracked-lab-a")
        migrate.run(self.repo)
        dest = self.root / "restored"
        migrate.restore_archive(self.repo / "archives" / "tracked-lab-a.zip", dest)
        # symlinks, permission bits (0755/0444/0600), hidden files,
        # nested .git metadata, and caches all come back
        self.assertEqual(tree_facts(dest), facts_a)

    def test_unsafe_archive_entries_refused(self):
        # entry names that escape the archive root, on both paths that
        # consume archives
        bad = self.root / "bad.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("ok.txt", b"fine")
            z.writestr("../evil.txt", b"escape")
        with self.assertRaises(migrate.UnsafeArchiveEntry):
            migrate.verify_archive(bad, {"ok.txt": sha256_hex(b"fine")})
        with self.assertRaises(migrate.UnsafeArchiveEntry):
            migrate.restore_archive(bad, self.root / "out")
        absolute = self.root / "abs.zip"
        with zipfile.ZipFile(absolute, "w") as z:
            z.writestr("/tmp/migrate-002-evil.txt", b"escape")
        with self.assertRaises(migrate.UnsafeArchiveEntry):
            migrate.verify_archive(absolute, {})
        # nothing was extracted anywhere
        self.assertFalse((self.root / "evil.txt").exists())
        self.assertFalse(Path("/tmp/migrate-002-evil.txt").exists())
        # a present-but-different or missing entry is a plain failure
        good = self.root / "good.zip"
        with zipfile.ZipFile(good, "w") as z:
            z.writestr("a.txt", b"actual")
        with self.assertRaises(migrate.MigrationError):
            migrate.verify_archive(good, {"a.txt": sha256_hex(b"expected")})
        with self.assertRaises(migrate.MigrationError):
            migrate.verify_archive(good, {"missing.txt": sha256_hex(b"x")})

    def test_staged_lab_conflict_stops_migration(self):
        # a staged change to lab source is a conflict, not something to
        # force through with git rm --cached
        (self.repo / "tracked-lab-a" / "main.tf").write_text("# lab a\nstaged\n")
        run_git(self.repo, "add", "tracked-lab-a/main.tf")
        before_tree = tree_facts(self.repo)
        before_index = self.index_state()
        with self.assertRaises(migrate.StagedLabConflict):
            migrate.run(self.repo)
        self.assertEqual(tree_facts(self.repo), before_tree)
        self.assertEqual(self.index_state(), before_index)

    def test_interrupted_run_preserves_all_sources_and_resumes(self):
        """A pre-retirement archive failure is fully recoverable.

        Retirement happens only after ALL archives verify, so no
        source is deleted; and the attempt removes every archive it
        newly created - not just the failed lab's partial - so the
        retry starts clean and no leftover destination can be
        mistaken for progress or accepted as "already done".
        """
        locked = self.repo / "tracked-lab-b" / "locked.bin"
        locked.write_bytes(b"\x00secret")
        facts = {slug: tree_facts(self.repo / slug)
                 for slug in ("tracked-lab-a", "tracked-lab-b", "untracked-lab")}
        locked.chmod(0o000)  # unreadable: archiving lab B must fail
        with self.assertRaises(migrate.MigrationError):
            migrate.run(self.repo)
        locked.chmod(0o644)
        # every source intact: retirement happens only after ALL
        # archives verify, so a partway failure deletes nothing
        for slug, before in facts.items():
            self.assertEqual(tree_facts(self.repo / slug), before, slug)
        self.assertFalse((self.repo / "labs" / "untracked-lab").exists())
        files = run_git(self.repo, "ls-files").stdout.splitlines()
        self.assertIn("tracked-lab-a/main.tf", files)  # index untouched
        self.assertIn("tracked-lab-b/main.tf", files)
        # artifacts newly created by the failed attempt are cleaned
        # up - BOTH archives (lab A's finished one too) AND their
        # sidecars - so the retry starts from a clean slate
        self.assertFalse((self.repo / "archives" / "tracked-lab-a.zip").exists())
        self.assertFalse((self.repo / "archives" / "tracked-lab-b.zip").exists())
        self.assertFalse((self.repo / "archives" / "tracked-lab-a.meta.json").exists())
        self.assertFalse((self.repo / "archives" / "tracked-lab-b.meta.json").exists())
        # retry against the inventory: with the obstacle removed and
        # no leftover destinations, the migration completes cleanly
        report = migrate.run(self.repo)
        self.assertEqual(report.archived, ["tracked-lab-a", "tracked-lab-b"])
        self.assertEqual(report.moved, ["untracked-lab"])
        self.assertEqual(tree_facts(self.repo / "labs" / "untracked-lab"),
                         facts["untracked-lab"])
        self.assertFalse((self.repo / "tracked-lab-a").exists())
        self.assertFalse((self.repo / "tracked-lab-b").exists())


class MigrationMetadataTests(unittest.TestCase):
    """Preservation facts a zip cannot carry must not be dropped.

    Directory and root permission bits, extended attributes, and
    owner-only archive permissions are part of the migration's
    preservation contract, so they get their own pins instead of
    riding along in the entry-set comparisons.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = build_migration_repo(self.root)

    def test_archives_and_sidecars_are_owner_only(self):
        report = migrate.run(self.repo)
        for slug in report.archived:
            zip_path = self.repo / "archives" / f"{slug}.zip"
            sidecar = self.repo / "archives" / f"{slug}.meta.json"
            self.assertEqual(zip_path.stat().st_mode & 0o777, 0o600, slug)
            self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600, slug)

    def test_sidecar_records_the_full_preservation_inventory(self):
        lab = self.repo / "tracked-lab-a"
        facts_a = tree_facts(lab)
        migrate.run(self.repo)
        sidecar = self.repo / "archives" / "tracked-lab-a.meta.json"
        data = json.loads(sidecar.read_text())
        self.assertEqual(data["slug"], "tracked-lab-a")
        entries = data["entries"]
        # every entry of the source tree is recorded - the root (""),
        # directories, hidden files, nested .git/.terraform content,
        # and symlinks; nothing may be dropped as "just metadata"
        self.assertEqual(set(entries), set(facts_a))
        self.assertEqual(entries[""]["type"], "dir")
        self.assertEqual(entries[""]["mode"], facts_a[""]["mode"])
        self.assertEqual(entries["modules/shared"]["type"], "dir")
        self.assertEqual(entries["modules/shared"]["mode"],
                         facts_a["modules/shared"]["mode"])
        self.assertEqual(entries["main.tf"]["type"], "file")
        self.assertEqual(entries["main.tf"]["mode"], facts_a["main.tf"]["mode"])
        self.assertEqual(entries["main.tf"]["sha256"],
                         facts_a["main.tf"]["sha256"])
        self.assertIn("xattrs", entries["main.tf"])
        self.assertEqual(entries["linked.tf"]["type"], "symlink")
        self.assertEqual(entries["linked.tf"]["target"], "main.tf")

    def test_restore_rebuilds_distinctive_directory_and_root_modes(self):
        lab = self.repo / "tracked-lab-a"
        (lab / "modules").chmod(0o751)
        (lab / "modules" / "shared").chmod(0o700)
        lab.chmod(0o750)
        facts_a = tree_facts(lab)
        migrate.run(self.repo)
        dest = self.root / "restored-modes"
        migrate.restore_archive(self.repo / "archives" / "tracked-lab-a.zip",
                                dest)
        self.assertEqual(tree_facts(dest), facts_a)

    @unittest.skipUnless(XATTRS, "extended attributes unsupported here")
    def test_xattrs_are_recorded_and_restored(self):
        """Extended attributes survive archive + restore byte-for-byte.

        The real labs carry com.apple.provenance on every entry; the
        fixture sets a second, test-controlled attribute so the
        assertion cannot pass by accident through system-added
        attributes alone. The comparison covers the FULL attribute
        map, system attributes included: a restore that silently
        dropped or altered provenance must fail here.
        """
        lab = self.repo / "tracked-lab-a"
        set_xattr(lab / "main.tf", b"user.migration", b"\x01\x02\x03")
        set_xattr(lab / "modules", b"user.migration", b"dir-marker")
        set_xattr(lab, b"user.migration", b"root-marker")
        original = {
            "root": list_xattrs(lab),
            "file": list_xattrs(lab / "main.tf"),
            "dir": list_xattrs(lab / "modules"),
        }
        migrate.run(self.repo)
        dest = self.root / "restored-xattrs"
        migrate.restore_archive(self.repo / "archives" / "tracked-lab-a.zip",
                                dest)
        self.assertEqual(list_xattrs(dest), original["root"])
        self.assertEqual(list_xattrs(dest / "main.tf"), original["file"])
        self.assertEqual(list_xattrs(dest / "modules"), original["dir"])


class MigrationRefusalTests(unittest.TestCase):
    """Anything the helper cannot classify or preserve safely is refused.

    Every refusal here must fire before sources, index, or anything
    outside the repository changes, and must never leave an attempt's
    artifacts behind.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = build_migration_repo(self.root)

    def index_state(self):
        return (run_git(self.repo, "ls-files").stdout,
                run_git(self.repo, "diff", "--cached", "--name-only").stdout)

    def assert_no_attempt_artifacts(self):
        for slug in ("tracked-lab-a", "tracked-lab-b"):
            self.assertFalse(
                (self.repo / "archives" / f"{slug}.zip").exists(), slug)
            self.assertFalse(
                (self.repo / "archives" / f"{slug}.meta.json").exists(), slug)
        self.assertFalse((self.repo / "labs" / "untracked-lab").exists())

    def test_existing_sidecar_alone_collides(self):
        (self.repo / "archives").mkdir()
        (self.repo / "archives" / "tracked-lab-a.meta.json").write_text("{}")
        before_index = self.index_state()
        migration_plan = migrate.plan(self.repo)
        self.assertEqual(migration_plan.collisions, ["tracked-lab-a"])
        with self.assertRaises(migrate.DestinationCollision):
            migrate.run(self.repo, migration_plan)
        self.assertEqual(self.index_state(), before_index)
        self.assertTrue((self.repo / "tracked-lab-a" / "main.tf").exists())

    def test_run_refuses_stale_plan(self):
        migration_plan = migrate.plan(self.repo)
        # the inventory changed after planning: a wholly new lab
        # appeared at the root
        late = self.repo / "late-lab"
        late.mkdir()
        (late / "main.tf").write_text("# late arrival\n")
        before_index = self.index_state()
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.repo, migration_plan)
        self.assertNotIsInstance(caught.exception,
                                 (migrate.DestinationCollision,
                                  migrate.StagedLabConflict))
        self.assertEqual(self.index_state(), before_index)
        self.assertTrue((late / "main.tf").exists())
        self.assert_no_attempt_artifacts()

    def test_run_refuses_absolute_symlink_in_lab(self):
        (self.repo / "tracked-lab-b" / "elsewhere.tf").symlink_to("/etc/hosts")
        with self.assertRaises(migrate.MigrationError):
            migrate.run(self.repo)
        self.assertTrue((self.repo / "tracked-lab-b" / "main.tf").exists())
        self.assert_no_attempt_artifacts()

    def test_run_refuses_special_files(self):
        fifo = self.repo / "tracked-lab-b" / "pipe"
        os.mkfifo(fifo)
        with self.assertRaises(migrate.MigrationError):
            migrate.run(self.repo)
        # the unsupported entry is left exactly where it was
        self.assertTrue(os.path.lexists(fifo))
        self.assertTrue((self.repo / "tracked-lab-b" / "main.tf").exists())
        self.assert_no_attempt_artifacts()

    def test_run_refuses_symlinked_collection_dirs(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.repo / "labs").symlink_to(outside)
        with self.assertRaises(migrate.MigrationError):
            migrate.run(self.repo)
        self.assertEqual(list(outside.iterdir()), [])
        self.assert_no_attempt_artifacts()
        # same refusal when archives/ is the escape hatch
        (self.repo / "labs").unlink()
        (self.repo / "labs").mkdir()
        (self.repo / "archives").symlink_to(outside)
        with self.assertRaises(migrate.MigrationError):
            migrate.run(self.repo)
        self.assertEqual(list(outside.iterdir()), [])
        self.assert_no_attempt_artifacts()

    def test_source_change_during_archiving_stops_without_deletion(self):
        """A source that changes mid-archival stops the run cold.

        The helper inventories a lab, writes its archive, then
        re-inventories the source; the two snapshots must agree. The
        test tampers with the second snapshot to simulate a file
        changing while its archive was being written.
        """
        real_inventory = migrate._inventory_tree
        calls = {}

        def tracking(lab_root):
            inventory = real_inventory(lab_root)
            key = str(lab_root)
            calls[key] = calls.get(key, 0) + 1
            if Path(key).name == "tracked-lab-a" and calls[key] == 2:
                tampered = json.loads(json.dumps(inventory))
                tampered["main.tf"]["sha256"] = "f" * 64
                return tampered
            return inventory

        facts = {slug: tree_facts(self.repo / slug)
                 for slug in ("tracked-lab-a", "tracked-lab-b",
                              "untracked-lab")}
        before_index = self.index_state()
        with mock.patch.object(migrate, "_inventory_tree",
                               side_effect=tracking):
            with self.assertRaises(migrate.MigrationError):
                migrate.run(self.repo)
        for slug, before in facts.items():
            self.assertEqual(tree_facts(self.repo / slug), before, slug)
        self.assertEqual(self.index_state(), before_index)
        self.assert_no_attempt_artifacts()

    def test_restore_refuses_preexisting_destination(self):
        migrate.run(self.repo)
        zip_path = self.repo / "archives" / "tracked-lab-a.zip"
        # a directory destination keeps its contents
        as_dir = self.root / "as-dir"
        as_dir.mkdir()
        (as_dir / "keep.txt").write_text("keep")
        with self.assertRaises(migrate.MigrationError):
            migrate.restore_archive(zip_path, as_dir)
        self.assertEqual([p.name for p in as_dir.iterdir()], ["keep.txt"])
        self.assertEqual((as_dir / "keep.txt").read_text(), "keep")
        # a file destination is refused, not replaced
        as_file = self.root / "as-file"
        as_file.write_text("precious")
        with self.assertRaises(migrate.MigrationError):
            migrate.restore_archive(zip_path, as_file)
        self.assertEqual(as_file.read_text(), "precious")
        # a dangling symlink destination is detected, not written through
        as_link = self.root / "as-link"
        as_link.symlink_to(self.root / "no-such-target")
        with self.assertRaises(migrate.MigrationError):
            migrate.restore_archive(zip_path, as_link)
        self.assertTrue(as_link.is_symlink())
        self.assertFalse(as_link.exists())

    def test_restore_refuses_archive_without_sidecar(self):
        migrate.run(self.repo)
        orphan = self.root / "orphan.zip"
        shutil.copy2(self.repo / "archives" / "tracked-lab-a.zip", orphan)
        out = self.root / "out"
        with self.assertRaises(migrate.MigrationError):
            migrate.restore_archive(orphan, out)
        self.assertFalse(out.exists())

    def test_restore_refuses_symlink_escape_metadata(self):
        """Extraction-through-symlink is refused before anything is written.

        The sidecar is the only place symlink entries exist, so these
        archives pair a real zip payload with lying metadata: a
        symlink parent for real file entries, and an absolute-target
        link. Both must be refused with nothing extracted anywhere.
        """
        payload = b"payload"
        escape_root = self.root / "escape"
        escape_root.mkdir()
        zip_path = escape_root / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("sub/file.txt", payload)
        good = {
            "": {"type": "dir", "mode": 0o755, "xattrs": {}},
            "sub/file.txt": {"type": "file", "mode": 0o644,
                             "sha256": sha256_hex(payload), "xattrs": {}},
        }
        # variant 1: "sub" claims to be a symlink out of the tree
        # while real file entries live below it
        lying_parent = json.loads(json.dumps(good))
        lying_parent["sub"] = {"type": "symlink", "mode": 0o777,
                              "target": "../../escape", "xattrs": {}}
        (escape_root / "evil.meta.json").write_text(
            json.dumps({"version": 1, "kind": "gcp-lab-002-archive-metadata",
                        "slug": "evil", "entries": lying_parent}))
        out1 = self.root / "out1"
        with self.assertRaises(migrate.MigrationError):
            migrate.restore_archive(zip_path, out1)
        self.assertFalse(out1.exists())
        # variant 2: an absolute-target symlink entry
        absolute_link = json.loads(json.dumps(good))
        absolute_link["esc"] = {"type": "symlink", "mode": 0o777,
                               "target": "/etc/hosts", "xattrs": {}}
        (escape_root / "evil.meta.json").write_text(
            json.dumps({"version": 1, "kind": "gcp-lab-002-archive-metadata",
                        "slug": "evil", "entries": absolute_link}))
        out2 = self.root / "out2"
        with self.assertRaises(migrate.MigrationError):
            migrate.restore_archive(zip_path, out2)
        self.assertFalse(out2.exists())
        # nothing escaped the would-be destination anywhere above
        for made in self.root.rglob("sub"):
            self.assertNotEqual(made.parent, escape_root)
        self.assertFalse((self.root / "escape" / "escape").exists())

    def test_verify_archive_rejects_duplicates_and_type_conflicts(self):
        dup = self.root / "dup.zip"
        with warnings.catch_warnings():
            # zipfile itself warns about the duplicate we are writing
            # on purpose here
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(dup, "w") as z:
                z.writestr("a.txt", b"one")
                z.writestr("a.txt", b"two")
        with self.assertRaises(migrate.MigrationError):
            migrate.verify_archive(dup, {"a.txt": sha256_hex(b"one")})
        conflict = self.root / "conflict.zip"
        with zipfile.ZipFile(conflict, "w") as z:
            z.writestr("d/", b"")
            z.writestr("d", b"payload as a file")
        with self.assertRaises(migrate.MigrationError):
            migrate.verify_archive(conflict, {"d": sha256_hex(b"payload as a file")})
        # a file entry that is also a parent of another entry cannot
        # be extracted safely either
        prefix = self.root / "prefix.zip"
        with zipfile.ZipFile(prefix, "w") as z:
            z.writestr("a", b"not a directory")
            z.writestr("a/b.txt", b"below")
        with self.assertRaises(migrate.MigrationError):
            migrate.verify_archive(prefix, {"a": sha256_hex(b"not a directory")})


class MigrationPreflightTests(unittest.TestCase):
    """The read-only preflight inventory: mapping, counts, problems.

    preflight() is the evidence step before anyone commits to
    run(): it classifies, counts, and reports problems without
    creating a single destination.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = build_migration_repo(self.root)

    def test_preflight_reports_the_inventory_without_writing(self):
        pre = migrate.preflight(self.repo)
        self.assertEqual(sorted(pre["tracked"]),
                         ["tracked-lab-a", "tracked-lab-b"])
        self.assertEqual(sorted(pre["untracked"]), ["untracked-lab"])
        self.assertEqual(pre["collisions"], [])
        self.assertEqual(pre["problems"], [])
        self.assertIn(pre["xattr_support"], (True, False))
        lab_a = pre["tracked"]["tracked-lab-a"]
        # the explicit disk inventory: every entry counted, root
        # directory included, hidden files and caches included
        self.assertEqual(lab_a["files"], 10)
        self.assertEqual(lab_a["dirs"], 6)
        self.assertEqual(lab_a["symlinks"], 1)
        self.assertEqual(lab_a["entries"], 17)
        self.assertGreater(lab_a["bytes"], 0)
        lab_u = pre["untracked"]["untracked-lab"]
        self.assertEqual(lab_u["files"], 5)
        self.assertEqual(lab_u["dirs"], 1)
        self.assertEqual(lab_u["symlinks"], 1)
        # strictly read-only: no destination was created
        self.assertFalse((self.repo / "archives").exists())
        self.assertFalse((self.repo / "labs").exists())

    def test_preflight_reports_collisions_and_problems(self):
        (self.repo / "labs").mkdir()
        (self.repo / "labs" / "untracked-lab").mkdir()
        (self.repo / "tracked-lab-b" / "elsewhere.tf").symlink_to("/etc/hosts")
        pre = migrate.preflight(self.repo)
        self.assertEqual(pre["collisions"], ["untracked-lab"])
        self.assertEqual(len(pre["problems"]), 1)
        self.assertIn("tracked-lab-b", pre["problems"][0])
        # the problematic lab is reported, not silently classified
        self.assertNotIn("tracked-lab-b", pre["tracked"])
        self.assertEqual(sorted(pre["tracked"]), ["tracked-lab-a"])


# --- independent extended-attribute oracle (phase-3 P1 remediation) --------
# The wrong no-follow constant (0x0008, which is Darwin's
# XATTR_REPLACE) lived in BOTH scripts/migrate_002.py and
# tests/util.py, so the old "independent" oracle agreed with the
# helper's bug and masked it. The helpers below share no code with
# either implementation: they call libc with the literal ABI constant
# (XATTR_NOFOLLOW = 0x0001, verified against the platform with a
# direct probe) and cross-check with the native /usr/bin/xattr tool,
# which reads a symlink's OWN attributes via -s.

DARWIN = sys.platform == "darwin"
_ABI_XATTR_NOFOLLOW = 0x0001  # Darwin XATTR_NOFOLLOW (options word)


def _abi_libc():
    if not DARWIN:
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.listxattr
        libc.getxattr
        libc.setxattr
    except (OSError, AttributeError):
        return None
    return libc


ABI_XATTRS = _abi_libc() is not None
XATTR_TOOL = DARWIN and os.path.exists("/usr/bin/xattr")


def abi_list_xattrs(path, nofollow):
    """{name bytes: value bytes} via literal-constant libc calls.

    Raises OSError on any failed call - this oracle never swallows
    errors, unlike the masked original.
    """
    libc = _abi_libc()
    if libc is None:
        raise RuntimeError("the ABI oracle is darwin-only")
    opts = _ABI_XATTR_NOFOLLOW if nofollow else 0
    path_b = os.fsencode(path)
    listxattr = libc.listxattr
    listxattr.restype = ctypes.c_ssize_t
    listxattr.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                          ctypes.c_size_t, ctypes.c_int]
    ctypes.set_errno(0)
    size = listxattr(path_b, None, 0, opts)
    if size < 0:
        raise OSError(ctypes.get_errno(), "abi listxattr probe failed")
    if size == 0:
        return {}
    buf = ctypes.create_string_buffer(size + 1)
    ctypes.set_errno(0)
    size = listxattr(path_b, buf, size, opts)
    if size < 0:
        raise OSError(ctypes.get_errno(), "abi listxattr read failed")
    getxattr = libc.getxattr
    getxattr.restype = ctypes.c_ssize_t
    getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                         ctypes.c_void_p, ctypes.c_size_t,
                         ctypes.c_uint32, ctypes.c_int]
    out = {}
    for name in buf.raw[:size].split(b"\0"):
        if not name:
            continue
        ctypes.set_errno(0)
        vsize = getxattr(path_b, name, None, 0, 0, opts)
        if vsize < 0:
            raise OSError(ctypes.get_errno(), "abi getxattr probe failed")
        vbuf = ctypes.create_string_buffer(vsize + 1)
        ctypes.set_errno(0)
        got = getxattr(path_b, name, vbuf, vsize, 0, opts)
        if got < 0:
            raise OSError(ctypes.get_errno(), "abi getxattr read failed")
        out[bytes(name)] = bytes(vbuf.raw[:got])
    return out


def abi_set_xattr(path, name, value, nofollow):
    """Set one attribute through the literal-constant ABI path."""
    libc = _abi_libc()
    setxattr = libc.setxattr
    setxattr.restype = ctypes.c_ssize_t
    setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                         ctypes.c_void_p, ctypes.c_size_t,
                         ctypes.c_uint32, ctypes.c_int]
    opts = _ABI_XATTR_NOFOLLOW if nofollow else 0
    ctypes.set_errno(0)
    rc = setxattr(os.fsencode(path), name, value, len(value), 0, opts)
    if rc != 0:
        raise OSError(ctypes.get_errno(), "abi setxattr failed")


def native_link_xattrs(link):
    """/usr/bin/xattr -s -l: {name: value} of the LINK ITSELF, or None
    when the native tool is unavailable."""
    result = subprocess.run(["/usr/bin/xattr", "-s", "-l", str(link)],
                            capture_output=True, timeout=60)
    if result.returncode != 0:
        return None
    out = {}
    for line in result.stdout.decode(errors="replace").splitlines():
        name, sep, value = line.partition(": ")
        if sep and not name.startswith("0000"):
            out[name] = value
    return out


class MigrationRemediationFixture(unittest.TestCase):
    """Shared fixture for the phase-3 P1 remediation regressions."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = build_migration_repo(self.root)

    def index_state(self):
        return (run_git(self.repo, "ls-files").stdout,
                run_git(self.repo, "diff", "--cached", "--name-only").stdout)

    def assert_no_attempt_artifacts(self):
        for slug in ("tracked-lab-a", "tracked-lab-b"):
            self.assertFalse(
                (self.repo / "archives" / f"{slug}.zip").exists(), slug)
            self.assertFalse(
                (self.repo / "archives" / f"{slug}.meta.json").exists(), slug)
        self.assertFalse((self.repo / "labs" / "untracked-lab").exists())


class MigrationSymlinkXattrTests(MigrationRemediationFixture):
    """P1: a symlink's own extended attributes are real data.

    The helper read them with a wrong no-follow constant and quietly
    recorded an empty attribute map for every symlink - silent
    metadata loss for the 143 real links. Reads must fail closed on
    errors; only a genuine zero-length value may record as empty.
    """

    @unittest.skipUnless(ABI_XATTRS, "darwin libc ABI required")
    def test_symlink_own_xattrs_are_read_no_follow(self):
        lab = self.repo / "tracked-lab-a"
        link = lab / "linked.tf"                       # -> main.tf
        abi_set_xattr(link, b"user.linkmark", b"LINKOWN", nofollow=True)
        abi_set_xattr(lab / "main.tf", b"user.referent", b"REFERENT",
                      nofollow=False)
        # the helper must read the LINK's own attributes - not the
        # referent's, and not a silent empty map
        recorded = migrate._xattr_list(link, follow_symlinks=False)
        self.assertEqual(recorded.get("user.linkmark"), b"LINKOWN".hex())
        self.assertNotIn("user.referent", recorded)
        # and through the link: the referent's attributes
        followed = migrate._xattr_list(link, follow_symlinks=True)
        self.assertEqual(followed.get("user.referent"), b"REFERENT".hex())
        self.assertNotIn("user.linkmark", followed)
        # cross-check against the literal ABI and the native tool
        self.assertEqual(
            abi_list_xattrs(link, nofollow=True).get(b"user.linkmark"),
            b"LINKOWN")
        if XATTR_TOOL:
            self.assertEqual(native_link_xattrs(link).get("user.linkmark"),
                             "LINKOWN")

    @unittest.skipUnless(ABI_XATTRS, "darwin libc ABI required")
    def test_dangling_symlink_own_xattrs_read_without_touching_target(self):
        link = self.repo / "tracked-lab-b" / "dangling.tf"
        link.symlink_to("no-such-target")
        abi_set_xattr(link, b"user.linkmark", b"DANGLING", nofollow=True)
        # a dangling link still has its own attributes to preserve
        recorded = migrate._xattr_list(link, follow_symlinks=False)
        self.assertEqual(recorded.get("user.linkmark"), b"DANGLING".hex())
        # following a dangling link is a genuine read error: it must
        # raise, never silently return an empty map
        with self.assertRaises(migrate.MigrationError):
            migrate._xattr_list(link, follow_symlinks=True)

    @unittest.skipUnless(DARWIN, "darwin options-word ABI required")
    def test_xattr_read_errors_raise_instead_of_mapping_to_empty(self):
        # a failed getxattr size probe previously recorded the
        # attribute as an empty value - inventing "zero-length"
        # metadata that the source never had
        fake = mock.Mock()
        fake.listxattr.side_effect = (
            lambda path, buf, size, opts:
            11 if buf is None
            else (ctypes.memmove(buf, b"user.probe\x00", 11), 11)[1])
        fake.getxattr.return_value = -1
        with mock.patch.object(migrate, "_XATTR", fake):
            with self.assertRaises(migrate.MigrationError):
                migrate._xattr_list("whatever", follow_symlinks=False)
        # same discipline when the size probe works but the read fails
        fake2 = mock.Mock()
        fake2.listxattr.side_effect = fake.listxattr.side_effect
        fake2.getxattr.side_effect = [6, -1]
        with mock.patch.object(migrate, "_XATTR", fake2):
            with self.assertRaises(migrate.MigrationError):
                migrate._xattr_list("whatever", follow_symlinks=False)

    @unittest.skipUnless(ABI_XATTRS, "darwin libc ABI required")
    def test_zero_length_xattr_values_stay_valid(self):
        # a genuinely empty attribute value is real data, not an
        # error: it must survive as "" - never be dropped, and never
        # make a failed read pass as one
        target = self.repo / "tracked-lab-a" / "main.tf"
        abi_set_xattr(target, b"user.empty", b"", nofollow=False)
        recorded = migrate._xattr_list(target, follow_symlinks=True)
        self.assertEqual(recorded.get("user.empty"), "")

    @unittest.skipUnless(DARWIN, "darwin libc required")
    def test_linux_nofollow_listing_refuses_without_l_symbols(self):
        # glibc spells no-follow with l-prefixed symbols; a platform
        # without them must refuse, never silently follow the link
        link = self.repo / "tracked-lab-a" / "linked.tf"
        with mock.patch.object(migrate.sys, "platform", "linux"):
            with self.assertRaises(migrate.MigrationError):
                migrate._xattr_list(link, follow_symlinks=False)

    @unittest.skipUnless(ABI_XATTRS, "darwin libc ABI required")
    def test_symlink_own_xattrs_survive_archive_and_restore(self):
        lab = self.repo / "tracked-lab-a"
        abi_set_xattr(lab / "linked.tf", b"user.linkmark", b"LINKOWN",
                      nofollow=True)
        migrate.run(self.repo)
        entries = json.loads(
            (self.repo / "archives" / "tracked-lab-a.meta.json")
            .read_text())["entries"]
        self.assertEqual(entries["linked.tf"]["xattrs"].get("user.linkmark"),
                         b"LINKOWN".hex())
        dest = self.root / "restored-link-xattrs"
        migrate.restore_archive(self.repo / "archives" / "tracked-lab-a.zip",
                                dest)
        restored = dest / "linked.tf"
        self.assertTrue(restored.is_symlink())
        self.assertEqual(
            abi_list_xattrs(restored, nofollow=True).get(b"user.linkmark"),
            b"LINKOWN")
        if XATTR_TOOL:
            self.assertEqual(native_link_xattrs(restored).get("user.linkmark"),
                             "LINKOWN")


class MigrationPreinventoryTests(MigrationRemediationFixture):
    """P1: every lab - tracked AND untracked - is inventoried before
    the run writes an artifact, untracks, or moves anything.

    An unsupported entry inside a wholly untracked lab used to be
    discovered only AFTER the index had already changed.
    """

    def test_unsupported_entry_in_untracked_lab_refused_before_changes(self):
        fifo = self.repo / "untracked-lab" / "pipe"
        os.mkfifo(fifo)
        before = {slug: tree_facts(self.repo / slug)
                  for slug in ("tracked-lab-a", "tracked-lab-b",
                               "untracked-lab")}
        before_index = self.index_state()
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.repo)
        self.assertIn("pipe", str(caught.exception))
        self.assertEqual(self.index_state(), before_index)
        for slug, facts in before.items():
            self.assertEqual(tree_facts(self.repo / slug), facts, slug)
        self.assert_no_attempt_artifacts()

    def test_unreadable_dir_in_untracked_lab_refused_before_changes(self):
        locked = self.repo / "untracked-lab" / "locked"
        locked.mkdir()
        (locked / "s.txt").write_text("secret\n")
        locked.chmod(0o000)
        self.addCleanup(locked.chmod, 0o755)
        before_index = self.index_state()
        with self.assertRaises(migrate.MigrationError):
            migrate.run(self.repo)
        self.assertEqual(self.index_state(), before_index)
        self.assertTrue((self.repo / "tracked-lab-a" / "main.tf").exists())
        self.assertTrue((self.repo / "untracked-lab" / "task1.tf").exists())
        self.assert_no_attempt_artifacts()

    def test_missing_tracked_source_refused_before_any_archive_written(self):
        (self.repo / "tracked-lab-b" / "main.tf").unlink()
        writes = []
        real_write = migrate._write_archive

        def recording(zip_path, *rest):
            writes.append(zip_path.name)
            return real_write(zip_path, *rest)

        before_index = self.index_state()
        with mock.patch.object(migrate, "_write_archive",
                               side_effect=recording):
            with self.assertRaises(migrate.MigrationError) as caught:
                migrate.run(self.repo)
        self.assertIn("tracked-lab-b", str(caught.exception))
        self.assertEqual(writes, [])
        self.assertEqual(self.index_state(), before_index)
        self.assert_no_attempt_artifacts()


class MigrationRetirementRevalidationTests(MigrationRemediationFixture):
    """P1: retirement revalidates the complete zip AND sidecar.

    The sidecar is the ONLY copy of symlink targets, directory modes,
    and extended attributes. Verifying just the zip's file hashes let
    a tampered or deleted sidecar through - and the source was then
    deleted on the strength of an archive that cannot rebuild the
    tree.
    """

    def _run_with_untrack_tamper(self, tamper):
        real_untrack = migrate._untrack

        def hooked(repo_root, paths):
            real_untrack(repo_root, paths)
            tamper()

        facts = tree_facts(self.repo / "tracked-lab-a")
        with mock.patch.object(migrate, "_untrack", side_effect=hooked):
            with self.assertRaises(migrate.MigrationError) as caught:
                migrate.run(self.repo)
        return caught.exception, facts

    def test_tampered_sidecar_blocks_retirement(self):
        sidecar = self.repo / "archives" / "tracked-lab-a.meta.json"
        exc, facts = self._run_with_untrack_tamper(
            lambda: sidecar.write_text("{}"))
        self.assertIn("tracked-lab-a", str(exc))
        self.assertIn("progress retained", str(exc))
        # the source survives: its retirement was refused
        self.assertEqual(tree_facts(self.repo / "tracked-lab-a"), facts)
        # the verified zip is retained (the failure came after the
        # retirement phase began, alongside the index changes)
        self.assertTrue((self.repo / "archives" / "tracked-lab-a.zip").is_file())

    def test_deleted_sidecar_blocks_retirement(self):
        sidecar = self.repo / "archives" / "tracked-lab-a.meta.json"
        exc, facts = self._run_with_untrack_tamper(sidecar.unlink)
        self.assertIn("sidecar", str(exc).lower())
        self.assertEqual(tree_facts(self.repo / "tracked-lab-a"), facts)
        self.assertTrue((self.repo / "archives" / "tracked-lab-a.zip").is_file())

    def test_invalid_sidecar_json_is_wrapped_not_uncaught(self):
        sidecar = self.repo / "archives" / "tracked-lab-a.meta.json"
        exc, facts = self._run_with_untrack_tamper(
            lambda: sidecar.write_text("{not json"))
        self.assertIsInstance(exc, migrate.MigrationError)
        self.assertNotIsInstance(exc, json.JSONDecodeError)
        self.assertIn("progress retained", str(exc))
        self.assertEqual(tree_facts(self.repo / "tracked-lab-a"), facts)


class MigrationIgnoreBoundaryTests(MigrationRemediationFixture):
    """P1: the run verifies the managed ignore rules before writing.

    Without the /labs/* and /archives/* rules active, a successful
    migration would leave archive payloads, sidecars, and moved labs
    as untracked noise one `git add -A` away from being committed -
    and a whole-directory rule would silently untrack the .gitkeep
    placeholders. Absent or wrong rules are a refusal; the helper
    never edits .gitignore itself.
    """

    def test_run_refuses_when_payload_rules_are_missing(self):
        gitignore = self.repo / ".gitignore"
        kept = [line for line in gitignore.read_text().splitlines(True)
                if "labs" not in line and "archives" not in line]
        gitignore.write_text("".join(kept))
        before_index = self.index_state()
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.repo)
        self.assertIn("ignore", str(caught.exception).lower())
        self.assertEqual(self.index_state(), before_index)
        self.assertTrue((self.repo / "tracked-lab-a" / "main.tf").exists())
        self.assertTrue((self.repo / "untracked-lab" / "task1.tf").exists())
        self.assert_no_attempt_artifacts()

    def test_run_refuses_when_placeholders_would_be_ignored(self):
        # whole-directory rules ignore the .gitkeep placeholders too
        (self.repo / ".gitignore").write_text("/labs/\n/archives/\n.DS_Store\n")
        before_index = self.index_state()
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.repo)
        self.assertIn("gitkeep", str(caught.exception))
        self.assertEqual(self.index_state(), before_index)
        self.assert_no_attempt_artifacts()


class MigrationClassificationEvidenceTests(MigrationRemediationFixture):
    """P1: an untracked root directory needs demonstrable lab evidence.

    The classifier used to call EVERY untracked root directory a lab
    - docs/, a scratch cache, a virtualenv lookalike. The spec says
    stop on unexpected classification, not guess.
    """

    def test_plan_refuses_unknown_root_directories(self):
        docs = self.repo / "docs"
        docs.mkdir()
        (docs / "guide.md").write_text("# documentation, not a lab\n")
        cache = self.repo / "scratch-cache"
        cache.mkdir()
        (cache / "dump.bin").write_text("cache\n")
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.plan(self.repo)
        self.assertIn("docs", str(caught.exception))
        self.assertIn("scratch-cache", str(caught.exception))

    def test_run_refuses_unknown_root_directories_before_changes(self):
        docs = self.repo / "docs"
        docs.mkdir()
        (docs / "guide.md").write_text("# documentation\n")
        before_index = self.index_state()
        with self.assertRaises(migrate.MigrationError):
            migrate.run(self.repo)
        self.assertEqual(self.index_state(), before_index)
        self.assertTrue((docs / "guide.md").exists())
        self.assertTrue((self.repo / "tracked-lab-a" / "main.tf").exists())
        self.assert_no_attempt_artifacts()

    def test_untracked_dir_with_terraform_evidence_is_a_lab(self):
        late = self.repo / "late-lab"
        late.mkdir()
        (late / "main.tf").write_text("# a real lab\n")
        current = migrate.plan(self.repo)
        self.assertIn("late-lab", current.untracked)
        self.assertNotIn("late-lab", current.tracked)


class MigrationRuntimeCompatTests(MigrationRemediationFixture):
    """P1: the helper must run on the Python it documents (>= 3.10).

    shutil.rmtree grew `onexc` in 3.12; on 3.10/3.11 the previous
    code failed at deletion time - after index changes and moves.
    """

    def _locked_tree(self):
        tree = self.root / "doomed"
        tree.mkdir()
        (tree / "plain.txt").write_text("y")
        sub = tree / "sub"
        sub.mkdir()
        (sub / "f.txt").write_text("x")
        sub.chmod(0o500)
        return tree

    def test_force_rmtree_removes_readonly_parent_trees(self):
        tree = self._locked_tree()
        migrate._force_rmtree(tree)
        self.assertFalse(tree.exists())

    def test_force_rmtree_uses_python310_onerror_signature(self):
        """A Python 3.10 rmtree (onerror + exc_info tuple) must work."""
        tree = self._locked_tree()

        def py310_rmtree(path, ignore_errors=False, onerror=None, **kwargs):
            if kwargs:
                raise TypeError(
                    "rmtree() got an unexpected keyword argument "
                    + next(iter(kwargs)))
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

        with mock.patch.object(migrate, "_PY312", False), \
                mock.patch.object(migrate.shutil, "rmtree",
                                  side_effect=py310_rmtree):
            migrate._force_rmtree(tree)
        self.assertFalse(tree.exists())

    def test_rmtree_step_reraises_the_original_failure(self):
        """When the retry also fails, the original error wins."""
        tree = self._locked_tree()
        target = tree / "sub" / "f.txt"
        original = OSError("original unlink failure")

        def hopeless(path):
            raise OSError("retry failed too")

        with self.assertRaises(OSError) as caught:
            migrate._rmtree_step(hopeless, target, original,
                                 str(self.root))
        self.assertIs(caught.exception, original)

    def test_run_retires_labs_with_readonly_directories(self):
        # a 0500 directory directly containing a file used to break
        # the deletion step - late, after the index changed
        (self.repo / "tracked-lab-a" / "modules" / "shared").chmod(0o500)
        report = migrate.run(self.repo)
        self.assertEqual(report.archived, ["tracked-lab-a", "tracked-lab-b"])
        self.assertFalse((self.repo / "tracked-lab-a").exists())


class MigrationSymlinkChainTests(MigrationRemediationFixture):
    """P1: lexical normpath alone misses chained symlink escapes.

    link -> '.' plus escape -> 'link/..' normalizes to '.' lexically
    but resolves OUTSIDE the root. Chains and cycles must be resolved
    virtually - against the full link map - before any source is
    retired or extracted.
    """

    def test_run_refuses_chained_symlink_escape(self):
        lab = self.repo / "tracked-lab-b"
        (lab / "link").symlink_to(".")
        (lab / "escape").symlink_to("link/..")
        before_index = self.index_state()
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.repo)
        self.assertIn("escape", str(caught.exception))
        self.assertEqual(self.index_state(), before_index)
        self.assertTrue((lab / "main.tf").exists())
        self.assert_no_attempt_artifacts()

    def test_run_refuses_symlink_cycle(self):
        lab = self.repo / "tracked-lab-b"
        (lab / "loop").symlink_to("loop")
        before_index = self.index_state()
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.repo)
        self.assertIn("loop", str(caught.exception))
        self.assertEqual(self.index_state(), before_index)
        self.assert_no_attempt_artifacts()

    def test_restore_refuses_chained_symlink_escape_metadata(self):
        """Chained/cyclic escapes in sidecar metadata are refused.

        The sidecar is tampered onto an otherwise REAL archive: every
        recorded entry keeps its genuine xattr values, so the only
        thing that can fail is containment. The evil links are copies
        of the lab's real symlink entry with changed targets.
        """
        migrate.run(self.repo)
        zip_path = self.repo / "archives" / "tracked-lab-a.zip"
        sidecar = self.repo / "archives" / "tracked-lab-a.meta.json"
        original = sidecar.read_text()
        real_link = json.loads(original)["entries"]["linked.tf"]

        def evil(target):
            fact = json.loads(json.dumps(real_link))
            fact["target"] = target
            return fact

        for rel, target, named in (("escape", "link/..", "escape"),
                                   ("loop", "loop", "loop")):
            data = json.loads(original)
            data["entries"]["link"] = evil(".")
            data["entries"][rel] = evil(target)
            sidecar.write_text(json.dumps(data))
            out = self.root / f"out-{rel}"
            with self.assertRaises(migrate.MigrationError) as caught:
                migrate.restore_archive(zip_path, out)
            self.assertIn(named, str(caught.exception))
            # refused before anything was written anywhere
            self.assertFalse(out.exists())


class MigrationLiteralPathspecTests(MigrationRemediationFixture):
    """P1: `git rm --cached` pathspecs are wildcards by default.

    A tracked lab slug carrying glob characters (a directory named
    `t*`) made the scoped untrack swallow every tracked path starting
    with 't' - staging deletions for unrelated tool files (tests/,
    tools/) while their working files survived, silently dropping
    them from the index. Untracking must treat the inventoried paths
    literally, not ban their names.
    """

    def _add_glob_named_lab(self):
        star = self.repo / "t*"
        star.mkdir()
        (star / "main.tf").write_text("# glob-named lab\n")
        run_git(self.repo, "--literal-pathspecs", "add", "--", "t*/main.tf")
        # a tracked tool file a wildcard pathspec would swallow,
        # carrying an unrelated staged modification
        (self.repo / "tests" / "main.tf").write_text("# tests main\n")
        run_git(self.repo, "add", "tests/main.tf")
        # commit ONLY the new paths, keeping the fixture's unrelated
        # pre-staged tool changes (README.md, notes.txt) staged
        run_git(self.repo, "--literal-pathspecs", "commit", "-m",
                "glob lab and tool file", "--no-verify", "--only", "--",
                "t*/main.tf", "tests/main.tf")
        (self.repo / "tests" / "main.tf").write_text("# tests main staged\n")
        run_git(self.repo, "add", "tests/main.tf")

    def test_glob_named_lab_untracks_exactly_itself(self):
        self._add_glob_named_lab()
        tool_paths = ("tests/main.tf", "tests/test_synthetic.py",
                      "templates/new-standard-lab/backend.tf")
        tools_stage_before = run_git(self.repo, "ls-files", "--stage", "--",
                                     *tool_paths).stdout
        before_tree = tree_facts(self.repo / "t*")
        report = migrate.run(self.repo)
        self.assertEqual(report.archived,
                         ["t*", "tracked-lab-a", "tracked-lab-b"])
        # exactly the inventoried lab paths are staged for deletion -
        # no tool file joined them (byte order: 't*' sorts before
        # 'tests' because '*' is a lower byte than 'e')
        self.assertEqual(
            run_git(self.repo, "diff", "--cached", "--name-status")
            .stdout.splitlines(),
            ["M\tREADME.md",
             "A\tnotes.txt",
             "D\tt*/main.tf",
             "M\ttests/main.tf",
             "D\ttracked-lab-a/main.tf",
             "D\ttracked-lab-b/main.tf"],
        )
        # the unrelated staged blobs and modes keep their exact values
        self.assertEqual(
            run_git(self.repo, "ls-files", "--stage", "--", *tool_paths).stdout,
            tools_stage_before)
        # the tool files stay tracked, work files intact, and the
        # glob-named lab's source retired with its archive verified
        files = set(run_git(self.repo, "ls-files").stdout.splitlines())
        for kept in tool_paths:
            self.assertIn(kept, files, kept)
        self.assertIn("# tests main staged\n",
                      (self.repo / "tests" / "main.tf").read_text())
        self.assertFalse((self.repo / "t*").exists())
        self.assertTrue((self.repo / "archives" / "t*.zip").is_file())
        self.assertEqual(
            json.loads((self.repo / "archives" / "t*.meta.json")
                       .read_text())["entries"]["main.tf"]["sha256"],
            before_tree["main.tf"]["sha256"])


class MigrationRestoreCompatibilityTests(MigrationRemediationFixture):
    """P1: every archive the migration retires a source for must be
    restorable by this helper's own rules.

    The zip only carries regular files, so the zip-name validator
    never saw metadata-only entries (empty directories, symlinks):
    an unsafe name there passed the whole run, the source was
    deleted, and restore_archive then refused the sidecar - leaving
    an archive that cannot rebuild the tree it replaced. Inventories
    must be validated with the same structural and name rules
    restore applies, before any artifact, index change, or
    retirement; the reloaded sidecar is validated again immediately
    before each retirement.
    """

    def _run_with_untrack_tamper(self, tamper):
        real_untrack = migrate._untrack

        def hooked(repo_root, paths):
            real_untrack(repo_root, paths)
            tamper()

        facts = tree_facts(self.repo / "tracked-lab-a")
        with mock.patch.object(migrate, "_untrack", side_effect=hooked):
            with self.assertRaises(migrate.MigrationError) as caught:
                migrate.run(self.repo)
        return caught.exception, facts

    def test_run_refuses_unrestorable_entry_names_before_changes(self):
        # an empty directory whose name restore refuses to recreate:
        # it is metadata-only (no zip entry ever carries it), so only
        # inventory/sidecar validation can catch it - while every
        # source and the index are still intact
        unsafe = self.repo / "tracked-lab-b" / "empty:dir"
        unsafe.mkdir()
        before = {slug: tree_facts(self.repo / slug)
                  for slug in ("tracked-lab-a", "tracked-lab-b",
                               "untracked-lab")}
        before_index = self.index_state()
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.repo)
        self.assertIn("empty:dir", str(caught.exception))
        self.assertEqual(self.index_state(), before_index)
        for slug, facts in before.items():
            self.assertEqual(tree_facts(self.repo / slug), facts, slug)
        self.assert_no_attempt_artifacts()

    def test_run_refuses_unrestorable_symlink_names(self):
        # a symlink with an unsafe name is metadata-only too (the zip
        # stores only file payloads), and equally non-restorable
        (self.repo / "tracked-lab-b" / "li:nk.tf").symlink_to("main.tf")
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.repo)
        self.assertIn("li:nk.tf", str(caught.exception))
        self.assert_no_attempt_artifacts()
        self.assertTrue((self.repo / "tracked-lab-b" / "main.tf").exists())

    def test_preflight_reports_unrestorable_names_as_problems(self):
        # the read-only evidence step reports the problem instead of
        # presenting a clean bill of health
        (self.repo / "tracked-lab-b" / "empty:dir").mkdir()
        pre = migrate.preflight(self.repo)
        self.assertTrue(any("empty:dir" in problem
                            for problem in pre["problems"]),
                        pre["problems"])
        self.assertNotIn("tracked-lab-b", pre["tracked"])

    def test_safe_empty_directories_survive_archive_and_restore(self):
        # the refusal targets unrestorable NAMES, not empty
        # directories: a safely named one is preserved and restored
        (self.repo / "tracked-lab-b" / "empty-dir").mkdir()
        migrate.run(self.repo)
        dest = self.root / "restored-empty"
        migrate.restore_archive(
            self.repo / "archives" / "tracked-lab-b.zip", dest)
        self.assertTrue((dest / "empty-dir").is_dir())
        self.assertEqual(list((dest / "empty-dir").iterdir()), [])

    def test_retirement_revalidation_refuses_structurally_corrupt_sidecar(self):
        # regression: immediately before retirement the RELOADED
        # sidecar is validated with restore's rules, not merely
        # compared - corruption that keeps the JSON well formed is
        # still a refusal with the source intact and the verified zip
        # retained
        sidecar = self.repo / "archives" / "tracked-lab-a.meta.json"

        def tamper():
            data = json.loads(sidecar.read_text())
            data["entries"]["main.tf"] = "not a facts dict"
            sidecar.write_text(json.dumps(data))

        exc, facts = self._run_with_untrack_tamper(tamper)
        self.assertIn("tracked-lab-a", str(exc))
        self.assertIn("progress retained", str(exc))
        self.assertEqual(tree_facts(self.repo / "tracked-lab-a"), facts)
        self.assertTrue((self.repo / "archives" / "tracked-lab-a.zip").is_file())


class MigrationInterruptTests(MigrationRemediationFixture):
    """P2: Ctrl-C is the operator's decision, not a migration failure.

    KeyboardInterrupt is a BaseException: it slipped past every
    handler in run(), so a mid-attempt Ctrl-C killed the process with
    half-written archives on disk and nothing said about them - and a
    retry then tripped the destination collision. The interrupt must
    still propagate, never wrapped as a MigrationError. Before the
    index or any source is touched the attempt cleans up after
    itself, so a retry starts from a pristine tree; once anything is
    mutated, the verified archives are retained and the operator gets
    the progress state on stderr.
    """

    def test_interrupt_before_mutation_cleans_up_and_retry_completes(self):
        # Ctrl-C right after the FIRST archive is written: no index
        # change, move, or deletion has happened, so the half-written
        # attempt must be removed and a plain retry must complete the
        # migration
        real_write = migrate._write_archive
        seen = []

        def interrupting(zip_path, *rest):
            seen.append(zip_path.name)
            real_write(zip_path, *rest)
            raise KeyboardInterrupt

        before = {slug: tree_facts(self.repo / slug)
                  for slug in ("tracked-lab-a", "tracked-lab-b",
                               "untracked-lab")}
        before_index = self.index_state()
        with mock.patch.object(migrate, "_write_archive",
                               side_effect=interrupting):
            with self.assertRaises(KeyboardInterrupt):
                migrate.run(self.repo)
        self.assertEqual(seen, ["tracked-lab-a.zip"])
        self.assertEqual(self.index_state(), before_index)
        for slug, facts in before.items():
            self.assertEqual(tree_facts(self.repo / slug), facts, slug)
        self.assert_no_attempt_artifacts()
        # the retry starts from the pristine tree and completes
        report = migrate.run(self.repo)
        self.assertEqual(report.archived,
                         ["tracked-lab-a", "tracked-lab-b"])
        self.assertEqual(report.moved, ["untracked-lab"])
        self.assertTrue(
            (self.repo / "labs" / "untracked-lab" / "task1.tf").is_file())

    def test_interrupt_after_untrack_keeps_verified_archives(self):
        # Ctrl-C AFTER the index is mutated: the verified archives are
        # the only record of what was untracked, so they are retained
        # - with the progress state on stderr - no source is deleted,
        # and the interrupt still propagates unwrapped
        real_untrack = migrate._untrack

        def interrupting(repo_root, paths):
            real_untrack(repo_root, paths)
            raise KeyboardInterrupt

        before = {slug: tree_facts(self.repo / slug)
                  for slug in ("tracked-lab-a", "tracked-lab-b",
                               "untracked-lab")}
        before_index = self.index_state()
        err = io.StringIO()
        with mock.patch.object(migrate, "_untrack", side_effect=interrupting):
            with contextlib.redirect_stderr(err):
                with self.assertRaises(KeyboardInterrupt):
                    migrate.run(self.repo)
        self.assertIn("interrupted", err.getvalue())
        self.assertIn("verified archives retained", err.getvalue())
        self.assertNotEqual(self.index_state(), before_index)
        for slug, facts in before.items():
            self.assertEqual(tree_facts(self.repo / slug), facts, slug)
        for slug in ("tracked-lab-a", "tracked-lab-b"):
            self.assertTrue(
                (self.repo / "archives" / f"{slug}.zip").is_file(), slug)
        self.assertFalse((self.repo / "labs" / "untracked-lab").exists())


class MigrationCasefoldAmbiguityTests(MigrationRemediationFixture):
    """P2: case-insensitive filesystems spell links differently.

    _resolve_within compares names exactly, so a chain spelled with a
    case variant of a known link ('LINK/..' where the link is 'link')
    looked like a plain directory traversal and passed - yet on a
    case-insensitive filesystem (the default on macOS and Windows)
    that spelling reaches through the REAL link, and '.' plus
    'LINK/..' leaves the lab root. A component that case-insensitively
    matches a known link without matching it exactly is refused;
    so is a sidecar whose entry names collide case-insensitively,
    because such a tree cannot be recreated faithfully there.
    """

    def test_case_variant_link_chain_refused_from_real_source(self):
        # a real lab source carrying 'link' -> '.' and
        # 'escape' -> 'LINK/..': the lexical resolver currently calls
        # that '.', but a case-insensitive filesystem follows the real
        # 'link' and the '..' then leaves the lab root - refused at
        # inventory time with every source and the index intact
        lab = self.repo / "tracked-lab-b"
        (lab / "link").symlink_to(".")
        (lab / "escape").symlink_to("LINK/..")
        before = {slug: tree_facts(self.repo / slug)
                  for slug in ("tracked-lab-a", "tracked-lab-b",
                               "untracked-lab")}
        before_index = self.index_state()
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.repo)
        self.assertIn("escape", str(caught.exception))
        self.assertEqual(self.index_state(), before_index)
        for slug, facts in before.items():
            self.assertEqual(tree_facts(self.repo / slug), facts, slug)
        self.assert_no_attempt_artifacts()

    def test_restore_refuses_case_variant_link_chain(self):
        # the same shape arriving as sidecar metadata: refused
        # before the destination is touched, nothing extracted
        case_root = self.root / "casefold"
        case_root.mkdir()
        zip_path = case_root / "case.zip"
        with zipfile.ZipFile(zip_path, "w"):
            pass  # every entry is metadata-only: no file payload
        entries = {
            "": {"type": "dir", "mode": 0o755, "xattrs": {}},
            "link": {"type": "symlink", "mode": 0o777,
                     "target": ".", "xattrs": {}},
            "escape": {"type": "symlink", "mode": 0o777,
                       "target": "LINK/..", "xattrs": {}},
        }
        (case_root / "case.meta.json").write_text(json.dumps(
            {"version": 1, "kind": "gcp-lab-002-archive-metadata",
             "slug": "case", "entries": entries}))
        out = self.root / "out"
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.restore_archive(zip_path, out)
        self.assertIn("escape", str(caught.exception))
        self.assertFalse(out.exists())

    def test_restore_refuses_case_fold_duplicate_names(self):
        # 'README' and 'readme' cannot both be recreated faithfully:
        # on a case-insensitive filesystem one clobbers the other.
        # Refused in validation, before the destination is touched.
        dup_root = self.root / "dupcase"
        dup_root.mkdir()
        zip_path = dup_root / "dup.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("README", b"upper")
            z.writestr("readme", b"lower")
        entries = {
            "": {"type": "dir", "mode": 0o755, "xattrs": {}},
            "README": {"type": "file", "mode": 0o644,
                       "sha256": sha256_hex(b"upper"), "xattrs": {}},
            "readme": {"type": "file", "mode": 0o644,
                       "sha256": sha256_hex(b"lower"), "xattrs": {}},
        }
        (dup_root / "dup.meta.json").write_text(json.dumps(
            {"version": 1, "kind": "gcp-lab-002-archive-metadata",
             "slug": "dup", "entries": entries}))
        out = self.root / "out"
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.restore_archive(zip_path, out)
        self.assertIn("differ only by case", str(caught.exception))
        self.assertFalse(out.exists())


class MigrationUnicodeEquivalenceTests(MigrationRemediationFixture):
    """P2: canonically equivalent Unicode spellings are ONE name on macOS.

    casefold() folds case but not Unicode normalization, so a link
    named with precomposed 'e\u0301' (U+00E9) and a chain spelled with
    the decomposed form looked like different names to the lexical
    resolver - yet a normalization-insensitive filesystem (the macOS
    default) treats them as the SAME name: 'e\u0301/..' reaches
    through the real link, and the '..' then leaves the lab root.
    The ambiguous-link lookup and the entry-name collision check
    must use a shared equivalence key that folds case AND Unicode
    normalization; recorded names are preserved byte-exactly, with
    no rewriting of payload names.
    """

    def test_run_refuses_unicode_variant_link_chain(self):
        # a real lab source carrying precomposed 'é' -> '.' and
        # 'escape' -> 'e\u0301/..' (decomposed): the lexical resolver
        # calls that chain safe because casefold does not normalize,
        # but on a normalization-insensitive filesystem the
        # decomposed spelling reaches through the real link and the
        # '..' escapes the lab root. Refused at inventory time with
        # every source and the index intact.
        lab = self.repo / "tracked-lab-b"
        (lab / "\u00e9").symlink_to(".")
        (lab / "escape").symlink_to("e\u0301/..")
        before = {slug: tree_facts(self.repo / slug)
                  for slug in ("tracked-lab-a", "tracked-lab-b",
                               "untracked-lab")}
        before_index = self.index_state()
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.repo)
        self.assertIn("escape", str(caught.exception))
        self.assertEqual(self.index_state(), before_index)
        for slug, facts in before.items():
            self.assertEqual(tree_facts(self.repo / slug), facts, slug)
        self.assert_no_attempt_artifacts()

    def test_restore_refuses_unicode_variant_link_chain(self):
        # the same shape arriving as sidecar metadata (metadata-only
        # entries, no file payload): refused before the destination
        # is touched, nothing extracted
        case_root = self.root / "unicode"
        case_root.mkdir()
        zip_path = case_root / "unicode.zip"
        with zipfile.ZipFile(zip_path, "w"):
            pass  # every entry is metadata-only: no file payload
        entries = {
            "": {"type": "dir", "mode": 0o755, "xattrs": {}},
            "\u00e9": {"type": "symlink", "mode": 0o777,
                       "target": ".", "xattrs": {}},
            "escape": {"type": "symlink", "mode": 0o777,
                       "target": "e\u0301/..", "xattrs": {}},
        }
        (case_root / "unicode.meta.json").write_text(json.dumps(
            {"version": 1, "kind": "gcp-lab-002-archive-metadata",
             "slug": "unicode", "entries": entries}))
        out = self.root / "out"
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.restore_archive(zip_path, out)
        self.assertIn("escape", str(caught.exception))
        self.assertFalse(out.exists())

    def test_restore_refuses_unicode_equivalent_entry_names(self):
        # 'café' (precomposed) and 'cafe\u0301' (decomposed) are one
        # name on a normalization-insensitive filesystem: one would
        # clobber the other on restore. The collision check must use
        # the shared equivalence key, and the refusal must name the
        # RECORDED spellings exactly, not rewritten ones.
        dup_root = self.root / "dupunicode"
        dup_root.mkdir()
        zip_path = dup_root / "dup.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("caf\u00e9", b"precomposed")
            z.writestr("cafe\u0301", b"decomposed")
        entries = {
            "": {"type": "dir", "mode": 0o755, "xattrs": {}},
            "caf\u00e9": {"type": "file", "mode": 0o644,
                          "sha256": sha256_hex(b"precomposed"), "xattrs": {}},
            "cafe\u0301": {"type": "file", "mode": 0o644,
                           "sha256": sha256_hex(b"decomposed"),
                           "xattrs": {}},
        }
        (dup_root / "dup.meta.json").write_text(json.dumps(
            {"version": 1, "kind": "gcp-lab-002-archive-metadata",
             "slug": "dup", "entries": entries}))
        out = self.root / "out"
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.restore_archive(zip_path, out)
        self.assertIn("caf\u00e9", str(caught.exception))
        self.assertIn("cafe\u0301", str(caught.exception))
        self.assertFalse(out.exists())


class MigrationSpecialModeBitsTests(MigrationRemediationFixture):
    """P2: setuid/setgid/sticky bits are permission facts.

    The inventory recorded `st_mode & 0o777`, silently dropping the
    setuid, setgid, and sticky bits (0o7000) - so a setuid helper or
    a sticky directory archived, "verified", retired, and restored
    without its special bits, and no comparison could ever notice:
    both sides masked identically. Permission bits are part of the
    preservation contract, so all four bit groups must survive run
    and restore, and the test oracle must see them too.
    """

    def test_setuid_setgid_sticky_bits_survive_run_and_restore(self):
        lab = self.repo / "tracked-lab-b"
        sticky_dir = lab / "sticky-dir"
        sticky_dir.mkdir()
        sticky_dir.chmod(0o1777)                       # sticky dir
        setuid_sh = lab / "setuid.sh"
        setuid_sh.write_text("#!/bin/sh\nid\n")
        setuid_sh.chmod(0o4755)                        # setuid file
        setgid_sh = lab / "setgid.sh"
        setgid_sh.write_text("#!/bin/sh\nid\n")
        setgid_sh.chmod(0o2755)                        # setgid file
        sticky_tf = lab / "sticky.tf"
        sticky_tf.write_text("# sticky\n")
        sticky_tf.chmod(0o1644)                        # sticky file
        lab.chmod(0o2755)                              # setgid lab root
        migrate.run(self.repo)
        # the sidecar is the only record: it must carry the FULL mode
        entries = json.loads(
            (self.repo / "archives" / "tracked-lab-b.meta.json")
            .read_text())["entries"]
        self.assertEqual(entries["sticky-dir"]["mode"], 0o1777)
        self.assertEqual(entries["setuid.sh"]["mode"], 0o4755)
        self.assertEqual(entries["setgid.sh"]["mode"], 0o2755)
        self.assertEqual(entries["sticky.tf"]["mode"], 0o1644)
        self.assertEqual(entries[""]["mode"], 0o2755)
        # and restore must rebuild every bit of them
        dest = self.root / "restored-special-modes"
        migrate.restore_archive(self.repo / "archives" / "tracked-lab-b.zip",
                                dest)
        for rel, mode in (("sticky-dir", 0o1777), ("setuid.sh", 0o4755),
                          ("setgid.sh", 0o2755), ("sticky.tf", 0o1644)):
            self.assertEqual(
                stat.S_IMODE(os.lstat(dest / rel).st_mode), mode, rel)
        self.assertEqual(stat.S_IMODE(os.lstat(dest).st_mode), 0o2755)


class MigrationReleasedLayoutPreflightTests(unittest.TestCase):
    """The conservative case rules must not misfire on the released layout.

    A fresh clone of the 002 target layout is the steady state this
    repository actually lives in: zero root labs, managed labs/ and
    archives/ already present, only tool files, templates, and ignore
    rules. The fixture is disposable (tests.util.make_git_repo), so
    the assertion is about the LAYOUT, never about whatever untracked
    directories happen to sit in a developer's checkout - preflight
    on it must stay exactly the read-only evidence step: an empty
    plan, zero problems, and a byte-identical tree before and after.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_git_repo(self.root)

    def test_released_layout_preflight_is_clean_and_read_only(self):
        before = tree_facts(self.repo)
        current = migrate.plan(self.repo)
        self.assertEqual(current.tracked, [])
        self.assertEqual(current.untracked, [])
        self.assertEqual(current.collisions, [])
        pre = migrate.preflight(self.repo)
        self.assertEqual(pre["tracked"], {})
        self.assertEqual(pre["untracked"], {})
        self.assertEqual(pre["collisions"], [])
        self.assertEqual(pre["problems"], [])
        # the released layout keeps its managed destinations: they
        # were already there, preflight must not create anything
        self.assertTrue((self.repo / "labs").is_dir())
        self.assertTrue((self.repo / "archives").is_dir())
        # strictly read-only evidence: nothing changed anywhere
        self.assertEqual(tree_facts(self.repo), before)


if __name__ == "__main__":
    unittest.main()
