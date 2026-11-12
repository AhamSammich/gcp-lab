"""Recover a lab into a fresh destination from a verified v1 archive pair.

The preservation core validates both artifacts before writing and removes only
the attempt-owned partial destination on failure or interruption. The input
pair is never modified. Exit codes: 0 success, 1 refusal, 2 invalid arguments
(handled by labkit's parser), and 130 on Ctrl-C.
"""

import sys
from pathlib import Path

from . import preservation


def add_arguments(parser):
    parser.add_argument("zip_path", type=Path, metavar="ZIP",
                        help="version-1 archive; matching .meta.json must be beside it")
    parser.add_argument("destination", type=Path, metavar="DEST",
                        help="fresh destination path; must not already exist in any form")


def run(args):
    try:
        preservation.restore_archive(args.zip_path, args.destination)
    except preservation.PreservationError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # The core already cleaned its partial destination or reported a remnant.
        print("interrupted - retry into a fresh destination", file=sys.stderr)
        return 130
    print(f"restored {args.zip_path} into {args.destination}")
    return 0
