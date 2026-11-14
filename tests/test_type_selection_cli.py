"""CLI tests for lab type selection and per-type generation (plan 005).

AC9: explicit --type for all three public types, and the interactive
prompt for new labs - no implicit default (an empty answer
re-prompts), invalid answers re-prompt, EOF and unattended --yes fail
before any lab write, reset, initialization, or authentication.
--yes is confirmation, never a type decision.

AC10: the selected type generates its own contents from its shipped
template - standard gets README.md and task<N>.md only, Terraform the
full Terraform workspace with generated tfvars, CLI README.md and
comment-only task<N>.sh - and records the type it generated.

AC11: terraform init runs only for the Terraform type and honors
--skip-init; standard and CLI never invoke a Terraform binary and,
with --skip-auth, ask for no project or zone at all.

AC12: a recorded lab refreshes as the same type, regenerating its
generated files while student work of any shape survives.

AC7: titles collected from a lab page are sanitized labels - a title
carrying newlines must never break a comment-only .tf/.sh stub into
executable source.

Every run targets the disposable fixture repo with a stub gcloud and
a tempdir HOME; the fake terraform binary (where used) logs and
fails, so an accidental invocation is both visible and harmless. No
real lab, cloud command, or credential is touched.
"""

import shlex
import tempfile
import unittest
from pathlib import Path

from util import (FULL_FLAGS, build_lab_dir, load_lab_start, make_repo,
                  noncomment_lines, run_cli, snapshot, stub_env)

METADATA = ".labkit-type"

# everything a standard or CLI run needs on the command line: no
# project, no zone - Terraform connection values must not be
# mandatory for the non-Terraform types
PLAIN_FLAGS = ("--title", "Typed Lab", "--tasks", "3",
               "--skip-init", "--skip-auth")


class TypeSelectionCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)
        self.repo = make_repo(self.workdir)  # never the real checkout
        self.home = self.workdir / "home"
        self.gcloud_log = self.workdir / "gcloud.log"
        self.env = stub_env(self.home, self.gcloud_log)

    def run_start(self, stdin_text, *argv):
        # invoked from outside the repository, like the other suites
        return run_cli(self.repo, self.workdir, self.env, stdin_text, *argv)

    def fake_terraform(self, status):
        """A terraform binary on the stub PATH that logs and exits
        `status`. Failing on purpose: for the never-invoked
        assertions an accidental run must be loud, and for the init
        tests the logged command line and working directory are the evidence."""
        script = self.home / "bin" / "terraform"
        script.write_text(
            "#!/bin/sh\n"
            f'echo "terraform $*" >> {shlex.quote(str(self.workdir / "terraform.log"))}\n'
            f'pwd -P >> {shlex.quote(str(self.workdir / "terraform-cwd.log"))}\n'
            f"exit {status}\n"
        )
        script.chmod(0o755)

    def terraform_invocations(self):
        log = self.workdir / "terraform.log"
        return log.read_text().strip() if log.exists() else ""

    def terraform_working_directories(self):
        log = self.workdir / "terraform-cwd.log"
        return [Path(line) for line in log.read_text().splitlines()]

    # --- AC10: explicit selection generates the type's contents ------
    def test_explicit_standard_generates_markdown_lab(self):
        # AC2/AC5/AC10: --type standard creates README.md and
        # task<N>.md files only - no Terraform support files, no
        # tfvars - and records standard
        result = self.run_start("", "std-lab", "--type", "standard",
                                *PLAIN_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "std-lab"
        self.assertEqual((lab / METADATA).read_text(), "standard\n")
        self.assertTrue((lab / "README.md").is_file())
        for n in range(1, 4):
            self.assertTrue((lab / f"task{n}.md").is_file(), f"task{n}.md")
            heading = (lab / f"task{n}.md").read_text().splitlines()[0]
            self.assertTrue(heading.startswith(f"# Task {n}"), heading)
        for name in ("backend.tf", "providers.tf", "variables.tf",
                     "terraform.tfvars", "task1.tf", "task1.sh"):
            self.assertFalse((lab / name).exists(),
                             f"{name} shipped into a standard lab")
        # the README is rendered, not a raw template
        readme = (lab / "README.md").read_text()
        self.assertIn("# Typed Lab", readme)
        self.assertNotIn("__LAB_", readme)
        # the preview and next steps tell the truth for the type
        type_lines = [line for line in result.stdout.splitlines()
                      if line.strip().startswith("type")]
        self.assertTrue(type_lines, "no type preview line was printed")
        self.assertIn("standard", type_lines[0])
        self.assertNotIn("terraform:", result.stdout)
        self.assertNotIn("terraform plan", result.stdout)
        self.assertNotIn("terraform init", result.stdout)

    def test_explicit_terraform_generates_terraform_workspace(self):
        # AC3/AC10: --type terraform creates the template's Terraform
        # skeleton, task<N>.tf stubs, and the generated tfvars, and
        # records terraform
        result = self.run_start("", "tf-lab", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "tf-lab"
        self.assertEqual((lab / METADATA).read_text(), "terraform\n")
        for name in ("README.md", "backend.tf", "providers.tf",
                     "variables.tf", "terraform.tfvars"):
            self.assertTrue((lab / name).is_file(), name)
        for n in range(1, 5):
            self.assertTrue((lab / f"task{n}.tf").is_file(), f"task{n}.tf")
        self.assertIn("qwiklabs-gcp-01-newlab",
                      (lab / "terraform.tfvars").read_text())

    def test_explicit_cli_generates_comment_only_shell_lab(self):
        # AC4/AC6/AC10: --type cli creates README.md and task<N>.sh
        # stubs that execute nothing, and records cli
        result = self.run_start("", "cli-lab", "--type", "cli",
                                *PLAIN_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "cli-lab"
        self.assertEqual((lab / METADATA).read_text(), "cli\n")
        self.assertTrue((lab / "README.md").is_file())
        for n in range(1, 4):
            stub = lab / f"task{n}.sh"
            self.assertTrue(stub.is_file(), f"task{n}.sh")
            self.assertEqual(noncomment_lines(stub), [],
                             f"task{n}.sh runs commands a student never wrote")
        for name in ("backend.tf", "providers.tf", "variables.tf",
                     "terraform.tfvars", "task1.tf"):
            self.assertFalse((lab / name).exists(),
                             f"{name} shipped into a CLI lab")

    # --- AC9: the interactive prompt, no default, failures closed ---
    def test_prompt_selects_the_answered_type(self):
        # AC9: a new lab without --type asks for one of the three
        # types; the answered type is scaffolded and recorded
        result = self.run_start("cli\n", "prompt-lab", *PLAIN_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "prompt-lab"
        self.assertEqual((lab / METADATA).read_text(), "cli\n")
        self.assertTrue((lab / "task1.sh").is_file())
        self.assertIn("Lab type", result.stdout)  # the prompt was offered

    def test_invalid_type_answer_reprompts(self):
        # AC9: an invalid answer re-prompts; the next valid one wins
        result = self.run_start("chef\nstandard\n", "invalid-lab",
                                *PLAIN_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "invalid-lab"
        self.assertEqual((lab / METADATA).read_text(), "standard\n")

    def test_empty_type_answer_is_not_a_silent_default(self):
        # AC9: no implicit default - an empty answer re-prompts (the
        # later explicit answer wins, so a default-accepting empty
        # would have picked its own type and left "cli" unread)
        result = self.run_start("\ncli\n", "empty-lab", *PLAIN_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lab = self.repo / "labs" / "empty-lab"
        self.assertEqual((lab / METADATA).read_text(), "cli\n")

    def test_eof_at_the_type_prompt_fails_before_anything(self):
        # AC9: exhausted stdin fails the run before any lab write,
        # reset, init, or auth - nothing is scaffolded and no type is
        # chosen silently
        result = self.run_start("", "eof-lab", *PLAIN_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Lab type", result.stdout)  # the prompt was offered
        self.assertFalse((self.repo / "labs" / "eof-lab").exists(),
                         "an EOF'd run created the lab anyway")
        self.assertFalse(self.gcloud_log.exists()
                         and self.gcloud_log.read_text().strip(),
                         "gcloud was invoked for an EOF'd run")

    def test_yes_without_type_fails_a_new_lab(self):
        # AC9: --yes answers confirmation, never the type decision -
        # unattended new-lab creation requires --type
        result = self.run_start("", "unattended-lab", "--yes", *PLAIN_FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--type", result.stderr)
        self.assertFalse((self.repo / "labs" / "unattended-lab").exists(),
                         "an unattended run created a lab without a type")
        self.assertFalse(self.gcloud_log.exists()
                         and self.gcloud_log.read_text().strip(),
                         "gcloud was invoked for an unattended run")

    # --- AC11: initialization is Terraform-only -------------------------
    def test_terraform_init_runs_when_a_binary_is_available(self):
        # AC11: initialize the selected lab, not the caller or repository root.
        self.fake_terraform(0)
        result = self.run_start("", "init-lab", "--yes",
                                 "--type", "terraform",
                                 "--title", "Init Lab", "--tasks", "1",
                                 "--project", "qwiklabs-gcp-01-newlab",
                                 "--zone", "us-central1-a", "--skip-auth")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.terraform_invocations(), "terraform init")
        self.assertEqual(self.terraform_working_directories(),
                         [self.repo / "labs" / "init-lab"])
        self.assertIn("terraform: initialized", result.stdout)

    def test_terraform_init_failure_is_reported(self):
        # AC11: a failing init in the selected lab is reported, not hidden.
        self.fake_terraform(1)
        result = self.run_start("", "failinit-lab", "--yes",
                                "--type", "terraform",
                                "--title", "Init Lab", "--tasks", "1",
                                "--project", "qwiklabs-gcp-01-newlab",
                                "--zone", "us-central1-a", "--skip-auth")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.terraform_invocations(), "terraform init")
        self.assertEqual(self.terraform_working_directories(),
                         [self.repo / "labs" / "failinit-lab"])
        self.assertIn("terraform: failed", result.stdout)
        # the lab itself was still scaffolded
        self.assertTrue((self.repo / "labs" / "failinit-lab" / "task1.tf").is_file())

    def test_skip_init_never_invokes_terraform(self):
        # AC11: --skip-init is honored for the Terraform type
        self.fake_terraform(1)
        result = self.run_start("", "skipinit-lab", "--yes", *FULL_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.terraform_invocations(), "")
        self.assertIn("terraform: skipped", result.stdout)

    def test_standard_never_invokes_terraform(self):
        # AC11: standard creation invokes no Terraform binary, even
        # without --skip-init and with one waiting (and failing) on
        # PATH
        self.fake_terraform(1)
        result = self.run_start("", "std-noinit-lab", "--yes",
                                 "--type", "standard",
                                 "--title", "Std Lab", "--tasks", "2",
                                 "--skip-auth")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.terraform_invocations(), "",
                         "standard creation invoked terraform")
        self.assertNotIn("terraform:", result.stdout)

    def test_cli_never_invokes_terraform(self):
        # AC11: same boundary for the CLI type
        self.fake_terraform(1)
        result = self.run_start("", "cli-noinit-lab", "--yes",
                                "--type", "cli",
                                "--title", "Cli Lab", "--tasks", "2",
                                "--skip-auth")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.terraform_invocations(), "",
                         "CLI creation invoked terraform")
        self.assertNotIn("terraform:", result.stdout)

    def test_standard_skip_auth_needs_no_project_or_zone(self):
        # AC11: Terraform connection values are not mandatory for the
        # standard type: with --skip-auth the run asks for neither
        # (an asked question would fail on the exhausted stdin)
        result = self.run_start("", "noconn-lab", "--type", "standard",
                                *PLAIN_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("GCP project ID", result.stdout)
        self.assertNotIn("Compute zone", result.stdout)

    # --- AC12: same-type refresh for standard and CLI -------------------
    # (the Terraform record is covered by the lab-type metadata suite)
    def test_standard_typed_lab_refreshes_as_standard(self):
        # AC12: a lab recording standard refreshes as standard: the
        # record keeps its type, its task<N>.md and foreign-type work
        # survive byte-identical, missing markdown stubs are created,
        # and the reset's tfvars deletion still happens for this type
        lab = build_lab_dir(self.repo / "labs", "std-refresh")
        (lab / METADATA).write_text("standard\n")
        (lab / "task1.md").write_text("# Task 1: my own notes\n")
        before = snapshot(lab)
        result = self.run_start("", "std-refresh", "--use-existing", "--yes",
                                 "--title", "Std Refresh", "--tasks", "3",
                                 "--skip-init", "--skip-auth")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LAB RESET COMPLETE", result.stdout)
        self.assertEqual((lab / METADATA).read_text(), "standard\n")
        # student work survives, across task formats
        self.assertEqual((lab / "task1.md").read_bytes(), before["task1.md"])
        for name in ("task1.tf", "task2.tf", "task3.tf", "task4.tf",
                     "shared-vpc/main.tf"):
            self.assertEqual((lab / name).read_bytes(), before[name], name)
        # the standard stubs for the missing numbers were created
        self.assertTrue((lab / "task2.md").is_file())
        self.assertTrue((lab / "task3.md").is_file())
        # the reset's deletions still apply to every type
        self.assertFalse((lab / "terraform.tfstate").exists())
        self.assertFalse((lab / "terraform.tfvars").exists())

    def test_cli_typed_lab_refreshes_as_cli(self):
        # AC12: a lab recording cli refreshes as cli, keeping its
        # recorded shell work and creating the missing stubs
        lab = self.repo / "labs" / "cli-refresh"
        lab.mkdir()
        (lab / METADATA).write_text("cli\n")
        (lab / "README.md").write_text("# stale readme\n")
        (lab / "task1.sh").write_text("#!/usr/bin/env bash\n# my work\n")
        (lab / "notes.txt").write_text("scratch\n")
        before = snapshot(lab)
        result = self.run_start("", "cli-refresh", "--use-existing", "--yes",
                                 "--title", "Cli Refresh", "--tasks", "2",
                                 "--skip-init", "--skip-auth")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((lab / METADATA).read_text(), "cli\n")
        self.assertEqual((lab / "task1.sh").read_bytes(), before["task1.sh"])
        self.assertEqual((lab / "notes.txt").read_bytes(), before["notes.txt"])
        stub2 = lab / "task2.sh"
        self.assertTrue(stub2.is_file())
        self.assertEqual(noncomment_lines(stub2), [])
        self.assertIn("# Cli Refresh", (lab / "README.md").read_text())


class TaskTitleSafetyTests(unittest.TestCase):
    """AC7, at the scaffold boundary: page-derived titles are labels.

    A task title with embedded newlines must land in the stub as ONE
    comment line - it can never break out of the comment-only .tf
    and .sh workspaces into executable source.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.lab_start = load_lab_start(self.repo)

    HOSTILE_TITLE = (
        "Set up a project\n"
        'resource "google_project" "p" {\n'
        '  name = "x"\n'
        "}\n"
        "gcloud projects create x\n"
    )

    def scaffold(self, lab_type, task_titles, tasks=2):
        lab = self.root / f"title-lab-{lab_type}"
        self.lab_start.scaffold(
            lab, lab_type, "Title Lab", "https://example.com/lab", None,
            tasks, task_titles,
            "qwiklabs-gcp-01-newlab", "us-central1", "us-central1-a",
        )
        return lab

    def test_hostile_title_stays_comment_only_in_tf_stub(self):
        lab = self.scaffold("terraform",
                            [{"n": 1, "title": self.HOSTILE_TITLE}])
        stub = lab / "task1.tf"
        self.assertEqual(noncomment_lines(stub), [],
                         "a page title broke out of the .tf comment stub")
        heading = stub.read_text().splitlines()[1]
        self.assertTrue(heading.startswith("# Task 1"), heading)
        # the title landed as ONE collapsed comment line: the whole
        # hostile text is on it with its newlines flattened
        collapsed = " ".join(self.HOSTILE_TITLE.split())
        self.assertIn(collapsed, heading)
        # the untitled task's markers were filled too
        self.assertNotIn("__TASK_", (lab / "task2.tf").read_text())

    def test_hostile_title_stays_comment_only_in_sh_stub(self):
        lab = self.scaffold("cli", [{"n": 1, "title": self.HOSTILE_TITLE}])
        stub = lab / "task1.sh"
        self.assertEqual(noncomment_lines(stub), [],
                         "a page title broke out of the .sh comment stub")
        heading = stub.read_text().splitlines()[1]
        self.assertTrue(heading.startswith("# Task 1"), heading)

    def test_stub_is_exactly_the_exemplar_with_markers_filled(self):
        # AC10: the stub is the shipped exemplar - nothing added,
        # nothing removed - with only its number and title markers
        # substituted, including the supplied task title.
        lab = self.scaffold("standard", [{"n": 1, "title": "Deploy a network"}])
        stub = (lab / "task1.md").read_text()
        self.assertIn("# Task 1: Deploy a network", stub)
        exemplar = (self.repo / "templates" / "new-standard-lab"
                    / "task1.md").read_text()
        expected = (exemplar.replace("__TASK_N__", "1")
                    .replace("__TASK_TITLE__", "Deploy a network"))
        self.assertEqual(stub, expected)


if __name__ == "__main__":
    unittest.main()
