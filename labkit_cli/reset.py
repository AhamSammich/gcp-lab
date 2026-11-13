"""labkit_cli.reset - tear a finished lab back down to a clean slate.

The inverse of the scaffold: remove the lab's terraform state and
tfvars, unset gcloud's project/region/zone config, and revoke the
lab credentials (ADC, then the account gcloud is using - the start
flow logged in with the student credentials, so that's the one to go).

One implementation for `labkit reset` and the reset half of a refresh: both
resolve their target through the shared boundary, so only managed
labs under labs/ are resettable:

  <command>                            # current dir is the lab (labs/<slug>)
  <command> my-lab                     # bare slug, from anywhere
  <command> labs/my-lab                # explicit managed path
  <command> --dry-run                  # show, don't touch
  <command> --credentials-only         # gcloud credentials only, no lab

--credentials-only is the finishing-workflow half: it needs no lab,
touches no files, and unsets gcloud's project/region/zone, revokes
ADC, and revokes the active account that gcloud's own lookup
confirms. Unlike the reset above it is strict - any failed or
unconfirmed step exits 1 - because "the credentials are gone" must
be a fact, not a best-effort shrug.

Anything else - the old flat-root labs, the repository root, labs/,
templates/, archives/, traversal, a symlinked target, or an alias
that merely resolves onto a managed lab - is refused before any file
or credential is touched.

Why the subprocess plumbing looks like it does: the old bash
lab-reset.sh hung forever on `gcloud auth application-default
revoke`. It sent the command's stdout/stderr to /dev/null, so
gcloud's Y/n prompt went to the void - while gcloud kept reading
stdin, the terminal, waiting for an answer no one could see. Two
fixes here: --quiet tells gcloud never to ask, and every subprocess
gets a closed stdin so ANY command that tries to prompt gets EOF
instead of a human.
"""

import argparse
import logging
import logging.handlers
import shutil
import subprocess
import sys
from pathlib import Path

from labkit_cli.common import (CYN, GRN, RED, RST, Spinner, YLW, banner,
                              cache_dir, resolve_lab_target)

LOG = logging.getLogger("lab-reset")


def _ensure_log() -> None:
    """Attach the debug log next to the start flow's, once per process.

    Same forensic deal as the start flow's log: the console keeps its
    curated output, the file records every command and its outcome.
    """
    if LOG.handlers:
        return
    cache_dir().mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        cache_dir() / "lab-reset.log", maxBytes=200_000, backupCount=1
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.DEBUG)


# --- subprocess plumbing ----------------------------------------------------
def run_quiet(*cmd: str, stderr_fallback: bool = True) -> tuple[int, str]:
    """Run a command with output captured and STDIN CLOSED.

    Returns (returncode, output) instead of raising: every step of a
    reset is best-effort - the script reports and keeps going.

    stdin=DEVNULL is the hang fix: a subprocess that asks a question
    gets instant EOF instead of a terminal to wait on. (capture_output
    alone doesn't do this - that's exactly the bug the bash version
    shipped: the prompt was hidden, but the wait stayed.)

    A launch failure (a gcloud that can't even be executed) comes
    back as rc=127 instead of an exception, so the remaining
    independent steps still run.

    The returned output is stdout - or, by default, stderr when
    stdout is empty, because the reporting steps want gcloud's
    explanation of a failure ("There was no ADC to revoke"). Data
    lookups pass stderr_fallback=False: a diagnostic on stderr must
    never be mistaken for data, least of all for the account to
    revoke.
    """
    try:
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True)
    except OSError as err:  # bad interpreter, exec refused, ...
        LOG.debug("failed to launch %s: %s", " ".join(cmd), err)
        return 127, str(err)
    output = ((result.stdout or result.stderr) if stderr_fallback
              else result.stdout).strip()
    LOG.debug("ran %s -> rc=%d %s", " ".join(cmd), result.returncode,
              output[-300:] if output else "(no output)")
    return result.returncode, output


def active_account() -> str | None:
    """The account gcloud is currently using.

    `gcloud auth list`'s status column renders empty in --format=value
    (the ACTIVE * is display-only), so the config property is the
    source of truth - `gcloud auth login` sets it, and the start flow
    signs in with the lab's student credentials.
    """
    rc, out = run_quiet("gcloud", "config", "get-value", "account")
    return out.strip() or None if rc == 0 else None


def confirmed_active_account() -> tuple[str | None, bool]:
    """(account, confirmed): who gcloud is using, per its own lookup.

    `gcloud config get-value account` is the lookup that confirms who
    would be revoked. A successful run names the account on stdout,
    or says there is none (an empty value, or `unset` - gcloud's
    placeholder for a property with no value). Only stdout is read:
    a diagnostic on stderr is never data, so a successful lookup with
    empty stdout confirms NO account even when stderr carries
    warnings. A failed lookup confirms nothing: confirmed is False
    and the caller must revoke NOTHING - a guessed or default account
    is never a substitute for gcloud's own word.
    """
    rc, out = run_quiet("gcloud", "config", "get-value", "account",
                        stderr_fallback=False)
    if rc != 0:
        return None, False
    account = out.strip()
    if account in ("", "unset", "(unset)"):
        return None, True
    return account, True


# --- local file cleanup -------------------------------------------------------
def delete_matching(lab_dir: Path, pattern: str, label: str,
                    dry_run: bool = False) -> int:
    """Delete lab_dir's files matching pattern; return how many.

    The same-line counter from the bash version: \r rewrites one line
    in place with a fixed-width [i/n] so the line never grows, and
    \x1b[K wipes the last frame's tail before the final newline.
    """
    files = sorted(f for f in lab_dir.rglob(pattern) if f.is_file())
    if not files:
        print(f"{GRN}No {label} files found{RST}")
        return 0
    if dry_run:
        for f in files:
            print(f"  {RED}would remove{RST} {f.relative_to(lab_dir)}")
        return len(files)

    tty = sys.stdout.isatty()
    total = len(files)
    for i, f in enumerate(files, 1):
        if tty:
            print(f"\r{RED}Removing {label} [{i:2d}/{total}] "
                  f"{f.relative_to(lab_dir)}{RST}", end="", flush=True)
        f.unlink()
    if tty:
        print(f"\r{GRN}Removed {total} {label} file(s){RST}\x1b[K")
    else:
        print(f"{GRN}Removed {total} {label} file(s){RST}")
    LOG.debug("deleted %d %s file(s) in %s", total, label, lab_dir)
    return total


# --- CLI-3: credentials-only cleanup ---------------------------------------
def credentials_only_reset(dry_run: bool) -> int:
    """Clear gcloud's config and credentials - no lab, no files touched.

    The finishing workflow's strict half: unlike the ordinary
    best-effort reset, every step must succeed (or be confirmed
    unnecessary) or the run exits 1 - "the credentials are gone" has
    to be a fact, not a hope. Independent steps still run after a
    failure so one broken step cannot hide another, and a lookup
    that cannot confirm the active account revokes nothing.

    The dry run only queries: gcloud's availability and the active
    account, then a report of what would run. The exit-1 contract
    covers it too - a preview that finds missing gcloud or an
    unconfirmed lookup exits 1, exactly like the run it previews.
    """
    banner("CREDENTIALS RESET")
    _ensure_log()
    LOG.debug("credentials-only reset (dry_run=%s)", dry_run)

    results = {"config": "skipped", "adc": "skipped", "account": "skipped"}

    if shutil.which("gcloud") is None:
        print(f"{YLW}gcloud not found on PATH - cannot clear credentials{RST}")
        for key in results:
            results[key] = "missing gcloud"
    elif dry_run:
        account, confirmed = confirmed_active_account()
        if not confirmed:
            print(f"{YLW}active account unconfirmed (gcloud lookup failed) - "
                  f"nothing would be revoked{RST}")
            results["account"] = "unconfirmed"
        elif account is None:
            print(f"{GRN}no active account to revoke{RST}")
            results["account"] = "none active"
        else:
            print(f"  {CYN}would run{RST} gcloud auth revoke {account} --quiet")
            results["account"] = f"would revoke {account}"
        print(f"  {CYN}would run{RST} gcloud config unset project/region/zone")
        print(f"  {CYN}would run{RST} gcloud auth application-default "
              f"revoke --quiet")
        results["config"] = results["adc"] = "would run"
    else:
        # three independent steps; each runs even when the one before
        # it failed, and any failure is remembered for the exit code
        config_ok = True
        with Spinner(f"{CYN}unsetting gcloud config{RST}"):
            for prop in ("project", "compute/region", "compute/zone"):
                rc, out = run_quiet("gcloud", "config", "unset", prop)
                if rc != 0:
                    # the ordinary reset shrugs rc=1 "not set" off;
                    # here the strict cleanup wants every command green
                    LOG.debug("config unset %s -> rc=%d %s", prop, rc, out)
                    config_ok = False
        results["config"] = "unset" if config_ok else "failed"

        # --quiet: gcloud answers its own Y/n prompt with the default
        with Spinner(f"{CYN}revoking application-default credentials{RST}"):
            rc, out = run_quiet("gcloud", "auth", "application-default",
                                "revoke", "--quiet")
        if rc == 0:
            results["adc"] = "revoked"
        else:
            results["adc"] = "failed"
            print(f"{RED}  adc: {out or 'revoke failed'}{RST}")

        # WHO is revoked comes from gcloud's own successful lookup -
        # never a guess, never a default, and nothing at all when the
        # lookup cannot confirm an account
        account, confirmed = confirmed_active_account()
        if not confirmed:
            print(f"{RED}active account unconfirmed (gcloud lookup failed) - "
                  f"revoked nothing{RST}")
            results["account"] = "unconfirmed"
        elif account is None:
            print(f"{GRN}no active account to revoke{RST}")
            results["account"] = "none active"
        else:
            with Spinner(f"{CYN}revoking {account}{RST}"):
                rc, out = run_quiet("gcloud", "auth", "revoke",
                                    account, "--quiet")
            if rc == 0:
                results["account"] = f"revoked {account}"
            else:
                results["account"] = "failed"
                print(f"{RED}  account: {out or 'revoke failed'}{RST}")

    failed = any(v in ("failed", "unconfirmed", "missing gcloud")
                 for v in results.values())
    if dry_run:
        banner("DRY RUN - NOTHING CHANGED")
    elif failed:
        banner("CREDENTIALS RESET FAILED")
    else:
        banner("CREDENTIALS RESET COMPLETE")
    print(f"  {CYN}config{RST}    {results['config']}")
    print(f"  {CYN}adc{RST}       {results['adc']}")
    print(f"  {CYN}account{RST}   {results['account']}")
    print(f"  {CYN}log{RST}       {cache_dir() / 'lab-reset.log'}")
    LOG.debug("credentials-only reset: %s", results)
    # dry-run or not: a missing gcloud or a failed/unconfirmed step
    # fails the run the preview reports and the run itself
    return 1 if failed else 0


# --- the ordinary reset -------------------------------------------------------
def reset_lab(raw_target: str | None, dry_run: bool = False) -> int:
    """Tear one managed lab back down; best-effort, 0 on success.

    The whole ordinary-reset workflow behind `labkit reset`
    and a refresh's reset half: resolve the target,
    delete the tfstate/tfvars, then clear gcloud's config and
    credentials - printing the same banners and summary inline,
    whichever entry point called it.

    The target is resolved HERE, immediately before the first
    deletion. A refresh confirms its lab and may fetch a page for
    minutes afterwards, so the lab must still be the same managed
    directory when the cleanup starts: a lab deleted, renamed, or
    swapped for a symlink in that window is refused before anything
    is touched (the refusal exits the process, as ever).

    Local cleanup failure is the one non-best-effort exit: the files
    a reset exists to delete must actually be deletable, and a
    failure returns 1 so a refresh stops before rewriting anything.
    The gcloud steps stay best-effort either way.
    """
    banner("LAB RESET")
    _ensure_log()
    LOG.debug("reset target=%r dry_run=%s", raw_target, dry_run)
    # --- resolve the target before anything is touched -----------------
    # The same boundary the start flow uses: a reset only ever aims
    # at one managed lab under labs/. Collection roots, old flat-root
    # labs, traversal, and symlinks are refused here, before a single
    # file is deleted or any gcloud command runs - dry-run or not.
    if raw_target:
        lab_dir = resolve_lab_target(raw_target, cwd=Path.cwd(),
                                     must_exist=True)
    else:
        lab_dir = resolve_lab_target(str(Path.cwd()), cwd=Path.cwd(),
                                     must_exist=True)
    print(f"{CYN}lab{RST} {lab_dir}\n")

    # --- local state first: tfstate and tfvars ----------------------------
    try:
        n_state = delete_matching(lab_dir, "*.tfstate*", "tfstate", dry_run)
        n_tfvars = delete_matching(lab_dir, "*.tfvars*", "tfvars", dry_run)
    except OSError as err:
        print(f"{RED}local cleanup failed: {err}{RST}")
        LOG.debug("local cleanup failed in %s: %s", lab_dir, err)
        return 1

    # --- then gcloud: config, ADC, active account -------------------------
    results = {"config": "skipped", "adc": "skipped", "account": "skipped"}
    if shutil.which("gcloud") is None:
        print(f"{YLW}gcloud not found on PATH - skipping gcloud steps{RST}")
    elif dry_run:
        account = active_account()
        print(f"  {CYN}would run{RST} gcloud config unset project/region/zone")
        print(f"  {CYN}would run{RST} gcloud auth application-default revoke --quiet")
        print(f"  {CYN}would run{RST} gcloud auth revoke <active account>"
              + (f" ({account})" if account else " (none active)"))
        results["config"] = results["adc"] = results["account"] = "would run"
    else:
        with Spinner(f"{CYN}unsetting gcloud config{RST}"):
            for prop in ("project", "compute/region", "compute/zone"):
                rc, out = run_quiet("gcloud", "config", "unset", prop)
                # rc=1 "not set" is a clean second reset, not a failure
                if rc != 0:
                    LOG.debug("config unset %s -> rc=%d %s", prop, rc, out)
        results["config"] = "unset"

        # --quiet: gcloud answers its own Y/n prompt with the default
        with Spinner(f"{CYN}revoking application-default credentials{RST}"):
            rc, out = run_quiet("gcloud", "auth", "application-default",
                                "revoke", "--quiet")
        if rc == 0:
            results["adc"] = "revoked"
        else:  # e.g. "There was no ADC to revoke" - already clean
            results["adc"] = out.splitlines()[-1] if out else "failed"
            print(f"{YLW}  adc: {results['adc']}{RST}")

        # the account the start flow signed in with; printed up front
        # so there is never any mystery about WHO is being revoked
        account = active_account()
        if account:
            with Spinner(f"{CYN}revoking {account}{RST}"):
                rc, out = run_quiet("gcloud", "auth", "revoke",
                                    account, "--quiet")
            results["account"] = f"revoked {account}" if rc == 0 else "failed"
        else:
            print(f"{GRN}no active account to revoke{RST}")
            results["account"] = "none active"

    # --- summary -----------------------------------------------------------
    verb = "to remove" if dry_run else "removed"
    banner("DRY RUN - NOTHING CHANGED" if dry_run else "LAB RESET COMPLETE")
    print(f"  {CYN}tfstate{RST}   {n_state} {verb}")
    print(f"  {CYN}tfvars{RST}    {n_tfvars} {verb}")
    print(f"  {CYN}config{RST}    {results['config']}")
    print(f"  {CYN}adc{RST}       {results['adc']}")
    print(f"  {CYN}account{RST}   {results['account']}")
    print(f"  {CYN}log{RST}       {cache_dir() / 'lab-reset.log'}")
    LOG.debug("reset complete: %s", results)
    return 0


# --- entry plumbing -----------------------------------------------------------
# The argument contract for `labkit reset`.
SUMMARY = ("Tear a finished lab back down: delete tfstate and tfvars, "
           "unset gcloud config, revoke the lab credentials. Only managed "
           "labs under labs/ are resettable.")


def examples(command: str) -> str:
    """The help examples, named for the entry point that shows them."""
    return (
        "examples:\n"
        f"  {command}                            # current dir is the lab\n"
        f"  {command} my-lab                     # bare slug, from anywhere\n"
        f"  {command} labs/my-lab                # explicit managed path\n"
        f"  {command} --dry-run                  # show, don't touch\n"
        f"  {command} --credentials-only         # clear gcloud credentials\n"
        f"                                         # only - no lab needed\n"
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The reset command's options, shared by both entry points."""
    parser.add_argument("directory", nargs="?",
                        help="lab to reset: a slug (labs/<slug>), an explicit "
                             "managed path, or nothing for the current directory")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="show what would happen without doing it")
    parser.add_argument("--credentials-only", action="store_true",
                        help="clear only gcloud credentials (unset "
                             "project/region/zone, revoke ADC and the "
                             "confirmed active account) - works without a "
                             "lab and touches no files")


def run(args, parser: argparse.ArgumentParser) -> int:
    """The reset command, after argument parsing.

    `parser` is the parser the entry point built - the one whose
    usage a bad invocation must print on its way to exit 2.
    """
    # --credentials-only aims at gcloud, never at a lab: a positional
    # directory is argparse's own refusal (usage + exit 2) BEFORE the
    # banner, the log, or any subprocess - there is nothing to resolve
    # and nothing to clean up file-wise in this mode. `is not None`,
    # not truthiness: an empty string is still a directory argument
    # the user supplied, not the absence of one
    if args.credentials_only and args.directory is not None:
        parser.error("--credentials-only takes no lab directory")

    if args.credentials_only:
        return credentials_only_reset(args.dry_run)
    return reset_lab(args.directory, dry_run=args.dry_run)


def main(argv=None, prog="labkit reset") -> int:
    """Parse and run the reset workflow."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=SUMMARY,
        epilog=examples(prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_arguments(parser)
    return run(parser.parse_args(argv), parser)
