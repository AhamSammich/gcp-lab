"""Shared toolkit for the lab CLI: repository paths, terminal paint,
and the one managed-lab target boundary.

labkit_cli.start and labkit_cli.reset are the implementations behind
./labkit; this module holds what they genuinely share. Importing it
is side-effect free: no prompt, no subprocess, no log - the workflows
start only when their entry points run.
"""

import os
import re
import shutil
import sys
import threading
from pathlib import Path
from typing import Literal

# --- where things live ------------------------------------------------------
# Path(__file__) is this package; .resolve() makes every derived path
# correct no matter which directory an entry point was invoked from.
REPO_ROOT = Path(__file__).resolve().parent.parent
LABS_DIR = REPO_ROOT / "labs"


def cache_dir() -> Path:
    """The tools' cache directory: ~/.cache/labkit.

    Read at CALL time, not import time: the debug logs, the browser
    profile, and the auth marker must follow the HOME in force when
    the process actually runs - never the HOME at import time.
    """
    return Path.home() / ".cache" / "labkit"


# --- terminal paint ------------------------------------------------------
# The same ANSI escapes lab-reset.sh used: \x1b[31m switches the
# terminal to red, \x1b[0m switches back. When stdout is not a TTY
# (piped into a file) or NO_COLOR is set, every constant is "" so piped
# output stays clean - a courtesy to future-you-scripting-this.
if sys.stdout.isatty() and "NO_COLOR" not in os.environ:
    RED, GRN, YLW, BLU, CYN, RST = (
        "\x1b[31m",
        "\x1b[32m",
        "\x1b[33m",
        "\x1b[34m",
        "\x1b[36m",
        "\x1b[0m",
    )
    HIDE_CURSOR, SHOW_CURSOR = "\x1b[?25l", "\x1b[?25h"
else:
    RED = GRN = YLW = BLU = CYN = RST = ""
    HIDE_CURSOR = SHOW_CURSOR = ""


def banner(message: str) -> None:
    """A `>---  message  ---<` rule, the lab-reset.sh house style."""
    width = min(shutil.get_terminal_size((80, 24)).columns, 80)
    padded = f"  {message}  "
    # dashes to fill whatever width the terminal allows (min 2)
    fill = max(width - len(padded) - 4, 2)
    print(f"\n{CYN}>--{padded}{'-' * fill}<{RST}\n")


class Spinner:
    """Animate `message... +` on ONE line while a with-block runs.

    The Python version of the background-subshell spinner in
    lab-reset.sh: a daemon thread redraws the line with \\r every
    100ms; on exit the frame is wiped (\\x1b[K) and replaced by the
    final status. Daemon threads die with the process, so a Ctrl+C can
    never orphan one.
    """

    FRAMES = "+x"

    def __enter__(self) -> "Spinner":
        self._tty = sys.stdout.isatty()
        if self._tty:
            print(HIDE_CURSOR, end="", flush=True)
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        else:
            # piped into a file: draw one plain line, no \r animation
            # - clean logs beat animation nobody can see
            print(f"{self.message}...", flush=True)
        return self

    def _spin(self) -> None:
        i = 0
        while not self._stop.wait(0.1):  # wait() doubles as the sleep
            frame = self.FRAMES[i % len(self.FRAMES)]
            print(f"\r{self.message}... {frame}", end="", flush=True)
            i += 1

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        # the with statement hands us the block's outcome for free:
        # exc_type is None iff the block ran to the end
        status = f"{GRN}done{RST}" if exc_type is None else f"{RED}failed{RST}"
        if self._tty:
            self._stop.set()
            self._thread.join()
            # \r rewrites the spinner line, \x1b[K wipes its leftover tail
            print(f"\r\x1b[K{self.message}... {status}")
            print(SHOW_CURSOR, end="", flush=True)
        else:
            print(f"{self.message}... {status}")
        return False  # don't swallow exceptions - let them propagate

    def __init__(self, message: str) -> None:
        self.message = message


# --- validators shared with the boundary --------------------------------
def check_slug(value: str):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        return "kebab-case only, e.g. develop-your-google-network"


# --- managed lab targets -----------------------------------------------------
# The one resolution boundary shared by start and reset (and by both
# their entry points). A lab is a direct child of LABS_DIR with a
# kebab-case slug, and every target - positional argument, prompted
# name, page-derived default, reset argument, or current directory -
# resolves through here before any fetch, reset, or write can happen:
#
#   * a bare slug maps to labs/<slug> from ANY working directory
#   * an explicit path (caller-relative or absolute) must NAME
#     labs/<slug> directly: the same place, nothing deeper, nowhere
#     else - and no alias may stand in for the lab, even one that
#     resolves onto labs/<slug> (a root-level legacy-alias, a link
#     nested in another lab). Ancestor aliases (a platform's /var vs
#     /private/var, an outside checkout alias pointing AT the repo
#     root) stay valid; once the path enters the repo, the ENTIRE
#     remainder must be the literal labs/<slug> pair, and an alias
#     that first lands inside the repo below its root is refused
#   * the old flat root, other labs' interiors, templates/, archives/,
#     collection roots, `..` traversal, symlinked targets, symlinked
#     labs/ roots, and plain files are all refused
def _refuse(message: str, hint: str | None = None) -> None:
    if hint:
        sys.exit(f"{RED}{message}{RST}\n{YLW}hint: {hint}{RST}")
    sys.exit(f"{RED}{message}{RST}")


def _same_directory(path: Path, other: Path) -> bool:
    # Path.resolve() can retain equivalent case/Unicode spellings. Only
    # filesystem identity locates the physical repository boundary.
    try:
        return path.samefile(other)
    except (FileNotFoundError, NotADirectoryError):
        # Missing prefixes are normal when selecting a new lab.
        return False


def resolve_lab_target(
    raw: str, *, cwd: Path | None = None, must_exist: bool = False
) -> Path:
    """Resolve `raw` to a managed lab directory; refuse anything else.

    Returns the absolute labs/<slug> path, or exits with a refusal:
    nothing is fetched, reset, or written for a refused target. An
    explicit path must NAME labs/<slug> directly: the path may enter
    the repository only once, at its root, and everything below that
    first entry must be exactly the two components `labs` and the
    slug - an alias resolving onto labs/<slug> (a root-level
    legacy-alias, a link nested in another lab) is not a way in, and
    neither is an outside alias that lands inside the repo below its
    root. Ancestor aliases (a platform's /var vs /private/var) stay
    valid; only the lab target itself may not be reached through a
    symlink. With must_exist=True (reset), a missing target is a
    refusal too - there is no "fresh clone" interpretation of a reset.
    """
    cwd = (cwd or Path.cwd()).resolve()
    labs = LABS_DIR

    # labs/ itself must be a real directory: a symlinked collection
    # root would aim every managed path at whatever it points at
    if labs.is_symlink():
        _refuse(f"{labs} is a symlink - refusing to work through it")
    if labs.exists() and not labs.is_dir():
        _refuse(f"{labs} exists and is not a directory")

    if "/" in raw:
        # a literal '..' component never names a managed lab, whatever
        # it normalizes to after resolution - reject it BEFORE
        # resolving, so `labs/detour/../victim-lab` cannot walk in as
        # labs/victim-lab. ('.' components are dropped by Path and
        # stay valid; a bare slug cannot contain '..' - check_slug
        # already refuses it.)
        if ".." in Path(raw).parts:
            _refuse(
                f"{raw!r}: '..' path components are not a lab target",
                "name the lab directly: labs/<slug>",
            )
        # explicit path: caller-relative or absolute. The SUPPLIED
        # path decides, not its canonicalization: its final component
        # may not be a symlink - an alias that happens to resolve
        # onto labs/<slug> (a root-level legacy-alias, a link nested
        # in another lab) is not a way in. Canonicalization only
        # sorts out ancestors, which a platform may alias (macOS
        # /var vs /private/var); ancestor aliases stay valid.
        supplied = Path(raw) if Path(raw).is_absolute() else cwd / raw
        if supplied.is_symlink():
            _refuse(
                f"{supplied} is a symlink - refusing to work through it",
                "name the lab directly: labs/<slug>",
            )
        candidate = supplied.resolve()
        if candidate.parent != labs:
            if candidate.parent == REPO_ROOT and candidate != labs:
                _refuse(
                    f"{candidate} is not a managed lab under {labs}/",
                    "old flat-root labs are not targets; the one-time "
                    "002 migration moves them into labs/ at Release",
                )
            _refuse(f"{candidate} is not a managed lab under {labs}/")
        # boundary walk on the SUPPLIED spelling, not the resolved
        # one: walk the supplied path's components from the root
        # down, resolving ancestors ONLY to find where the path
        # FIRST enters this repository. An ancestor alias may point
        # AT the repository root - a platform's /var vs /private/var,
        # an outside checkout alias - but everything BELOW that
        # first entry must then be exactly labs/<slug>: two literal
        # non-symlink components, with no alias in between, so
        # `labs/other-lab/labs/victim-lab` and
        # `labs/other-lab/repo-alias/labs/victim-lab` cannot name a
        # lab they merely resolve onto no matter how clean their
        # suffix is. A path whose first entry into the repo lands
        # BELOW the root - an outside alias aimed at a lab interior
        # that reaches back up and around - is refused outright.
        # Case must match exactly too: a conservative refusal on
        # case-insensitive platforms.
        parts = Path(os.path.normpath(str(supplied))).parts
        remainder = None
        for i in range(1, len(parts) + 1):
            resolved = Path(*parts[:i]).resolve()
            if _same_directory(resolved, REPO_ROOT):
                # first entry into the repo is the boundary: the
                # ENTIRE remainder, not just the final components,
                # must be the literal labs directory and the slug
                remainder = parts[i:]
                break
            if any(_same_directory(parent, REPO_ROOT) for parent in resolved.parents):
                # below the root before any root boundary: an alias
                # pointing at a sublab may not reach back up to a
                # managed lab, whatever it resolves onto next
                _refuse(
                    f"{raw!r} enters the repository below its root "
                    f"(first at {resolved}) - only labs/<slug> is "
                    "a lab target",
                    "name the lab directly: labs/<slug>",
                )
        if (
            remainder is None
            or len(remainder) != 2
            or remainder[0] != labs.name
            or remainder[1] != candidate.name
            or Path(*parts[: len(parts) - 1]).is_symlink()
        ):
            _refuse(
                f"{raw!r} does not name the lab directly - it only "
                f"resolves onto {candidate}",
                "name the lab directly: labs/<slug>",
            )
        name = candidate.name
    else:
        # bare slug: validated and anchored to labs/, never the caller's
        # directory and never the old flat root
        problem = check_slug(raw)
        if problem:
            _refuse(f"{raw!r}: {problem}", "a new lab lands at labs/<slug>")
        name = raw

    problem = check_slug(name)
    if problem:
        _refuse(f"{name!r}: {problem}")

    target = labs / name
    if target.is_symlink():
        _refuse(f"{target} is a symlink - refusing to work through it")
    if target.exists():
        if not target.is_dir():
            _refuse(f"{target} exists and is not a directory")
    elif must_exist:
        _refuse(
            f"{target} is not a directory",
            "managed labs live under labs/, e.g. labs/my-lab",
        )
    return target
