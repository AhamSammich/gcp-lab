"""Component tests for the repo-boundary walk in resolve_lab_target.

The boundary is a fact about FILESYSTEM IDENTITY (st_dev+st_ino),
never about path strings: a case-insensitive filesystem (or a
Unicode-normalizing one) answers one inode for several spellings of
the same directory, while Path.resolve() keeps the caller's spelling
- so comparing strings can walk straight past the repository root
and mistake a nested alias's return to the canonical spelling for
the first entry. These tests pin the identity-based boundary on the
fixture module.

Where the host filesystem has no case equivalence, the case-spelling
tests skip AFTER verifying with a same-file stat that the variant
spelling is not the same file - and the mirror test runs instead:
a genuinely distinct directory (same letters, different inode) is
never equated with the repository root.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from util import build_lab_dir, load_lab_start, make_repo, same_file_probe


class BoundaryIdentityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.lab_start = load_lab_start(self.repo)

    def require_case_equivalent_fs(self):
        """Skip when a case-variant spelling is NOT the same file.

        The probe is a dev+ino stat comparison, so a case-sensitive
        filesystem (or a missing variant) honestly answers False -
        two genuinely distinct directories are never equated.
        """
        if not same_file_probe(self.repo, self.repo.name.upper()):
            self.skipTest(
                "filesystem treats case spellings as distinct files")

    def refuse(self, raw: str) -> str:
        """resolve_lab_target must exit on `raw`; returns its message."""
        with self.assertRaises(SystemExit) as caught:
            self.lab_start.resolve_lab_target(raw, cwd=self.root)
        self.assertIsInstance(caught.exception.code, str)
        return caught.exception.code

    def test_case_variant_root_detour_does_not_launder_nested_alias(self):
        # The review's repro: enter the repo through a case-equivalent
        # spelling of its root, detour through another lab, return to
        # the canonical spelling via repo-alias, then spell a clean
        # labs/<slug> suffix. The FIRST entry is the boundary, and it
        # is only visible to a walk that matches the root by
        # filesystem identity - the string walk finds the root at the
        # alias instead and accepts the suffix.
        self.require_case_equivalent_fs()
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = build_lab_dir(self.repo / "labs", "other-lab")
        (other / "repo-alias").symlink_to(self.repo)
        detour = (self.repo.parent / self.repo.name.upper() / "labs"
                  / "other-lab" / "repo-alias" / "labs" / "victim-lab")
        message = self.refuse(str(detour))
        self.assertIn("does not name the lab directly", message)

    def test_case_variant_root_literal_pair_conservatively_refused(self):
        # The identity boundary must not over-ACCEPT either: entering
        # through the case-variant root and then spelling the ENTIRE
        # remainder as the literal two components labs/<slug> yields
        # a candidate resolved through the case variant, which the
        # conservative managed-labs check refuses - the slug spelling
        # must match. Pin that refusal: never a silent acceptance.
        self.require_case_equivalent_fs()
        build_lab_dir(self.repo / "labs", "victim-lab")
        variant = (self.repo.parent / self.repo.name.upper() / "labs"
                   / "victim-lab")
        with self.assertRaises(SystemExit):
            self.lab_start.resolve_lab_target(str(variant), cwd=self.root)

    def test_case_variant_below_root_entry_refused(self):
        # An outside alias aimed at a case-variant spelling of a lab
        # interior resolves BELOW the repo root - but its string
        # spells the variant root, so a string ancestry check
        # (is_relative_to or parent membership) misses it exactly
        # like the root-equality check. The ancestor chain must be
        # identity-checked, or the repo-alias return to the canonical
        # root looks like a clean first entry.
        self.require_case_equivalent_fs()
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = build_lab_dir(self.repo / "labs", "other-lab")
        (other / "repo-alias").symlink_to(self.repo)
        inside = self.root / "inside-alias"
        inside.symlink_to(self.repo.parent / self.repo.name.upper()
                         / "labs" / "other-lab")
        message = self.refuse(str(inside / "repo-alias" / "labs"
                                  / "victim-lab"))
        self.assertIn("enters the repository below its root", message)

    def test_distinct_case_dirs_are_never_equated(self):
        # The mirror of the probe, on case-SENSITIVE filesystems: a
        # directory with the same letters but a different inode is a
        # foreign tree. The boundary walk must not mistake it for the
        # repository root (identity, not casefold). This is a valid
        # external checkout alias: its first physical repo entry is
        # repo-alias, followed by precisely labs/<slug>.
        variant = self.repo.parent / self.repo.name.upper()
        try:
            variant.mkdir()
        except FileExistsError:
            self.skipTest(
                "filesystem folds case spellings into one file")
        victim = build_lab_dir(self.repo / "labs", "victim-lab")
        other = variant / "labs" / "other-lab"
        other.mkdir(parents=True)
        (other / "repo-alias").symlink_to(self.repo)
        detour = (variant / "labs" / "other-lab" / "repo-alias"
                  / "labs" / "victim-lab")
        self.assertEqual(
            self.lab_start.resolve_lab_target(str(detour), cwd=self.root),
            victim)
        self.assertTrue((victim / "terraform.tfstate").exists())

    def test_identity_boundary_portably_refuses_root_and_interior_detours(self):
        # Model only the filesystem's identity answer, not resolution or
        # containment. The distinct on-disk tree supplies real prefixes;
        # its nested alias returns to the canonical repo, as in the repro.
        build_lab_dir(self.repo / "labs", "victim-lab")
        equivalent = self.root / "equivalent-root"
        interior = equivalent / "labs" / "other-lab"
        interior.mkdir(parents=True)
        (interior / "repo-alias").symlink_to(self.repo)
        inside_alias = self.root / "inside-alias"
        inside_alias.symlink_to(interior)
        equivalent = equivalent.resolve()
        original = Path.samefile

        def samefile(path, other):
            if path == equivalent and Path(other) == self.lab_start.REPO_ROOT:
                return True
            return original(path, other)

        for entry, message in (
            (interior, "does not name the lab directly"),
            (inside_alias, "enters the repository below its root"),
        ):
            with self.subTest(entry=entry), patch.object(Path, "samefile", samefile):
                raw = entry / "repo-alias" / "labs" / "victim-lab"
                self.assertIn(message, self.refuse(str(raw)))


if __name__ == "__main__":
    unittest.main()
