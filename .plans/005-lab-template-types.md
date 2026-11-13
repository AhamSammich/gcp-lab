# Lab template types

**Date**: 2026-11-11
**Status**: Implemented and reviewed; Release not performed
**Revised**: 2026-11-12; integration and interactive selection approved;
legacy labs without type metadata assume standard
**Author**: Andre + AI-assisted

## Background

Students need three independent ways to practice a lab: follow its original
instructions, write their own Terraform solution, or work through CLI commands.
The scaffold type determines the task-file format and guidance, not which tools
the student is allowed to use for every action.

At the start of this work, the templates had different meanings:

- `templates/new-standard-lab/` supplies a Terraform skeleton. The current
  scaffold generates Terraform task stubs and variable values separately.
- `templates/new-terraform-lab/` contains a README for following a Terraform
  repository supplied by the original lab. It is not selectable through the CLI.
- There is no CLI lab template.

The initial partial implementation added CLI content and revised the Terraform
template for students writing their own solutions. Integration subsequently
made all three types available through the public CLI.

Before integration, `labkit_cli/start.py` assumed Terraform throughout
scaffolding and refresh. Its page extraction still collects task titles, not
full task instructions; adding a template directory alone was insufficient.

## Goal

Provide standard, Terraform, and CLI scaffolds through `labkit start`, with
type-appropriate contents and learning guidance. Each should give students a
workspace and useful starting prompts without supplying the completed solution.
None requires completing another type first. New labs prompt for a type unless
one is supplied explicitly; refresh preserves the lab's recorded type.

## Acceptance Criteria

Checked items reflect reviewed template content and disposable integration
evidence below, not Release or real cloud execution. AC9–AC13 track the public
integration added after the initial template-content implementation.

### AC1: Shared lab requirements

- [x] All three types retain the original lab's objectives, required resource
  settings, security constraints, and validation requirements. Terraform and CLI
  approaches replace the prescribed procedure where useful, not the required
  outcomes. Necessary use of another tool is not a violation of the scaffold type.
  - [x] Standard: covered in the revised README and task exemplar.
  - [x] Terraform: covered in the own-solution README and task exemplar.
  - [x] CLI: covered in the step-by-step README and task exemplar.

### AC2: Standard scaffold

- [x] The standard scaffold provides `README.md` and `task<N>.md` files. Task
  files reference the original instructions, identify the prescribed approach
  and any permitted alternatives, and provide space for student notes. They do
  not assume Terraform. A lab that prescribes Terraform or an upstream
  repository still fits the standard type when the student follows that
  procedure.

### AC3: Terraform scaffold

- [x] The Terraform scaffold provides `README.md`, `task<N>.tf`, `backend.tf`,
  `providers.tf`, `variables.tf`, and generated `terraform.tfvars`. It supports
  students writing their own Terraform solution rather than treating an
  upstream repository as the defining feature of this type. Task files contain
  comments and space for student-written configuration, not completed resource
  blocks.
  - [x] Static files provided, with `task1.tf` as the exemplar to copy per task.
  - [x] Own-solution guidance and comment-only task workspace reviewed.
  - [x] Automatic `terraform.tfvars` generation through `labkit start`.
    Optional manual local creation remains documented in the appendix.

### AC4: CLI scaffold

- [x] The CLI scaffold provides `README.md` and `task<N>.sh` files. Task files
  contain comments and space for student-written commands, intended for
  step-by-step use. They do not supply or orchestrate a completed whole-lab
  script. The README and comment-only `task1.sh` exemplar supply the guidance
  used by CLI generation.

### AC5: Type-appropriate supporting files

- [x] Only the Terraform scaffold includes the Terraform supporting files
  listed in AC3 by default. Standard and CLI students can add other files when
  their lab requires them.
  - [x] Standard: remove default Terraform supporting files without breaking
    the start/refresh safeguards under the type-aware workflow below.
  - [x] Terraform: backend, provider, and variable files supplied; local tfvars
    generated, with optional manual creation documented.
  - [x] CLI: no default Terraform supporting files; additional files allowed.

### AC6: Hints without solutions

- [x] Initial templates provide generic, approach-specific prompts. Standard
  prompts direct students to the prescribed procedure and permitted
  alternatives; Terraform prompts help students identify resources and data
  sources; CLI prompts encourage command discovery and help usage. Specific
  resource names or command suggestions can be added manually when preparing a
  particular lab. The scaffold does not infer those details from task titles or
  supply completed provisioning commands.
  - [x] Standard: procedure and permitted-alternative prompts supplied.
  - [x] Terraform: generic resource/data-source prompts, without solutions.
  - [x] CLI: command-discovery/help prompts, without provisioning commands.

### AC7: Honest use of source material

- [x] Task stubs can use collected task titles and reference the original lab
  page. They must not imply that full task instructions have been extracted
  when only titles are available. Missing task-specific guidance remains for
  manual preparation rather than being invented.
  - [x] Standard: titles identified as labels; original instructions required.
  - [x] Terraform: titles identified as labels; original instructions required.
  - [x] CLI: titles identified as labels; original instructions required.

### AC8: README owns the common workflow

- [x] Each README covers prerequisites, the original lab link, setup, use of
  its task files, validation, and cleanup using guidance appropriate to that
  type. Task stubs hold task-local guidance and workspace rather than repeating
  shared setup. Terraform installation instructions are not universal
  prerequisites.

  Cleanup guidance preserves the distinction between destroying cloud resources
  and removing local state or credentials. It must not imply that local reset
  destroys cloud resources or direct students to discard state or credentials
  before resource cleanup that needs them.
  - [x] Standard: common workflow revised for the new standard meaning.
  - [x] Terraform: common workflow, safe setup, and cleanup ordering reviewed.
  - [x] CLI: common workflow, safe setup, and cleanup ordering reviewed;
    Terraform is not a prerequisite.

### AC9: Explicit or interactive type selection

- [x] `labkit start --type standard|terraform|cli` selects the requested type.
  - [x] For a new lab without `--type`, prompt for one of the three types with
    no implicit default. Explain their learning approaches briefly.
  - [x] Invalid answers re-prompt interactively. EOF or unattended use without
    an explicit type fails before lab writes, reset, initialization, or
    authentication; it must not hang or choose a type silently.
  - [x] `--yes` skips confirmation, not the type decision. Unattended new-lab
    creation requires `--type` as well as the other required inputs.
  - [x] Do not infer the type from task titles, lab pages, or existing files.
    The explicit exception for missing legacy metadata is the fixed standard
    fallback in AC12, not file-based type detection.

### AC10: Type-aware scaffold generation

- [x] The selected type generates the contents specified in AC1–AC8 through
  the public CLI, rather than requiring manual template copying.
  - [x] Render the README and one appropriately numbered task file per task,
    using available titles and the original lab reference without inventing
    task instructions. Task guidance comes from the selected template.
  - [x] Validate the selected template before creating partial output or
    resetting an existing lab, including readability and UTF-8 decoding of
    required files. A broken unrelated template does not block it.
  - [x] Record the selected type for subsequent refresh. Protect that metadata
    with the same containment and unsafe-output safeguards as other managed
    outputs; malformed or unsupported recorded types fail before mutation.

### AC11: Type-appropriate initialization

- [x] Terraform initialization runs only for the Terraform type and honors
  `--skip-init`. Standard and CLI creation do not require a Terraform binary or
  invoke Terraform initialization.
  - [x] Terraform-specific variable collection and tfvars generation are not
    mandatory for standard or CLI scaffolds. Collect connection settings only
    where the selected workflow needs them.
  - [x] Preserve existing authentication and `--skip-auth` safeguards. This
    scope does not authorize a credential-management redesign.

### AC12: Safe, same-type refresh

- [x] Refresh reuses the recorded type without prompting for another one. An
  explicitly conflicting `--type` fails before reset or writes; switching an
  existing lab between types is not supported. For eligible legacy labs without
  type metadata, use standard as specified below.
  - [x] Preserve student task files and other non-generated work across all
    three types, including files students added for another tool. Do not
    delete them merely because they are not defaults for the selected type.
  - [x] Preview the actual generated files that will be rewritten and the
    existing reset effects. Retain containment, output-symlink, credential,
    and student-data safeguards; changing types must not bypass them.
  - [x] Assume standard for eligible legacy labs without type metadata, without
    a type prompt or file-based type inference. Show the fallback in the
    preview and record standard only after the owner approves refresh through
    the existing confirmation/`--use-existing` safeguards. An explicit
    conflicting type must not override this fallback.
  - [x] Retain the existing safeguard against resetting arbitrary directories;
    missing metadata alone is not evidence that a directory is a lab. Preserve
    legacy Terraform source/supporting files as non-generated work when
    refreshing as standard. The existing reset's state/tfvars deletion and
    credential effects must remain explicit in the preview.

### AC13: Public workflow verification and documentation

- [x] Disposable CLI tests cover explicit selection and interactive selection,
  generated contents for each type, Terraform-only initialization, same-type
  refresh, student-work preservation, and meaningful refusal paths above.
  - [x] Revise standard-only behavior assertions only where superseded by
    these criteria. Preserve existing meaningful safety, authentication, and
    refresh regressions rather than removing them to accommodate the change.
  - [x] Verify the legacy standard fallback, preservation of legacy source
    files, and the recorded type after approved refresh. Refusal or cancellation
    must preserve the entire lab tree, including state, tfvars, tasks, and
    absent metadata, and invoke neither reset nor initialization/gcloud
    authentication. Optional Skills website sign-in for page extraction is
    separate from gcloud authentication and retains its existing workflow.
  - [x] Update help, previews, next steps, root documentation, and template
    READMEs for actual generated workflows. Do not leave manual-only or
    standard-only claims as the primary instructions after integration.
  - [x] Run the full applicable suite and offline integration checks before
    marking integration complete. Tests do not authorize real cloud operations
    or establish Release completion.

## Exclusions

- Automatic type detection or a silent default when a new lab needs a type.
- Switching an existing lab between types.
- Migration, conversion, or bulk rewriting of existing student labs. Same-type
  refresh and the owner-approved legacy standard fallback are limited to AC12;
  there is no bulk migration or rewriting of student source files.
- Automated task-specific hint generation or full task-body extraction.
- Completed solutions, whole-lab automation scripts, or strict tool enforcement.
- Changes to authentication, reset, archive, restore, or credential safeguards.

## Implementation boundary and verification

This plan now includes template contents and the public integration described
in AC9–AC13. The owner chose an interactive prompt for new labs without an
explicit type; there is no default type for new labs. Integration is implemented
and reviewed against disposable fixtures, not released through real execution.
The owner superseded the earlier legacy-refusal decision: eligible legacy labs
without type metadata assume standard when refreshed, with existing reset and
confirmation safeguards retained.

For implementation, follow red-green-refactor for executable behavior and
identify the relevant criterion in each new or changed test. Review prose for
learning intent rather than asserting incidental wording. Preserve meaningful
regressions; if an existing standard-only test conflicts with the approved new
meaning, discuss that conflict rather than silently weakening the test.

## Decision: legacy labs assume standard

The original CLI generated Terraform scaffolds under the name `standard`;
manually copied templates also lack recorded types. The owner now chose a fixed
standard fallback for eligible legacy labs instead of refusing all untyped
directories. This does not infer Terraform from old `.tf` files, convert source
files, or authorize a bulk migration. The new standard task format applies to
newly generated stubs; existing student files remain user work. Approved refresh
records standard and retains the normal, previewed reset effects. Invalid,
unsupported, or unsafe metadata is still refused rather than treated as absent.
This decision does not change the separate reset, archive, or restore commands
or authorize executing real operations on the owner's labs.

## Initial template-content evidence

The initial implementation approval covered independent Terraform and CLI
template content only. At that point the standard template remained unchanged:
removing its Terraform files before integration would have broken the original
start/refresh workflow. The bullets below record that earlier snapshot, not the
current integration status.

- AC4 and the CLI portions of AC1 and AC5–AC8: `new-cli-lab/` provided
  a manual-use README and comment-only `task1.sh` exemplar to copy per task.
- AC3 static content and the Terraform portions of AC1 and AC5–AC8:
  `new-terraform-lab/` provided the own-solution README, comment-only
  `task1.tf` exemplar, backend, provider, and variable declarations. Automatic
  `terraform.tfvars` generation was not yet implemented; manual local creation
  was documented instead. Neither template was wired to `labkit start` then.
- Both slices passed independent adversarial review after fixes. New tests
  demonstrated failures before implementation and cover template contents,
  documented setup, preservation of existing student work, and disposable
  subprocess isolation. Learning guidance and cleanup ordering were reviewed
  manually rather than pinned to incidental wording.
- Initial verification: `python3 -m unittest discover -s tests` ran 221 tests
  successfully, with two pre-existing platform skips;
  `terraform fmt -check templates/new-terraform-lab/`,
  `bash -n templates/new-cli-lab/task1.sh`, and `git diff --check` passed.
  Existing standard and refresh regressions were unchanged at that point.

## Integration implementation evidence

- AC1–AC8: all three template contracts are now generated through the CLI;
  standard uses Markdown task notes, Terraform uses configuration stubs plus
  generated tfvars, and CLI uses comment-only shell stubs. Required outcomes
  and safe cleanup guidance were reviewed manually for learning intent.
- AC9–AC11: `--type` or an explicit interactive choice selects new labs;
  `.labkit-type` records the choice. Only Terraform generates supporting files
  and runs initialization. Standard/CLI with `--skip-auth` need no Terraform
  connection inputs. Existing single-login authentication remains unchanged.
- AC12–AC13: recorded types are reused; conflicting types and unsafe metadata
  refuse before reset or writes. Eligible untyped legacy labs use standard,
  preserve existing Terraform source/support files, and record the fallback
  only on approved refresh. Existing `.tf` lab eligibility remains a safety
  guard, not type inference; arbitrary untyped directories still refuse.
- Independent adversarial review approved the final behavior after a
  test-first fix for unreadable/undecodable selected templates. Required source
  files are read during preflight so failures precede reset and partial output.
  Meaningful path, task-preservation, reset-failure, and credential regressions
  remain covered under the new selection contract.
- Coordinator verification: `python3 -m unittest discover -s tests` ran 264
  tests, with no failures and two pre-existing platform skips. An additional
  offline smoke passed 31 checks across all three create/refresh workflows,
  interactive selection, conflicting-type refusal, and legacy fallback.
  `terraform fmt -check templates/new-terraform-lab/`,
  `bash -n templates/new-cli-lab/task1.sh`, and `git diff --check` passed.

These checks used disposable fixtures, isolated HOME, and stub tools. No real
lab, infrastructure, deployment, or credential operations were performed, and
Release is not claimed. Live browser/cloud workflows and concurrent template
edits were not verified. No roadmap file exists in this checkout.

## References

- `templates/new-standard-lab/`: original-procedure README and Markdown task
  exemplar, without default Terraform supporting files.
- `templates/new-terraform-lab/README.md`: own-solution guidance; replaced the
  earlier upstream-repository meaning during partial implementation.
- `labkit_cli/start.py`: current scaffold generation and page extraction.
- `tests/test_template_plumbing.py`: selected-template preflight, rendered
  output parity, and unreadable-template refusal regressions.
- `tests/test_type_selection_cli.py` and `tests/test_lab_type_metadata.py`:
  selection, generation, initialization, metadata, refresh, and legacy fallback.
- `tests/test_refresh_cli.py` and `tests/test_refresh_component.py`: existing
  preservation and refresh regressions to retain during type-aware integration.
- `.plans/002-directory-restructure.md`: historical template plumbing and
  deferred selection work.
