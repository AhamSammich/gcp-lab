"""Rebuild a lab tree from a verified archive + sidecar pair.

    python3 scripts/restore_lab.py ZIP DEST

ZIP is an archives/<slug>.zip or archives/<slug>--NNN.zip written by
the lab tooling; its preservation sidecar - the matching .meta.json
next to it, derived from the zip's name - must be readable. DEST must
not exist in any form: nothing is ever extracted into, merged with,
or written through a pre-existing destination.

The whole pair is validated (entry safety, metadata types, payload
hashes) before the first write. On a failure or an interrupt exactly
the partial destination this attempt created is removed, any remnant
that could not be removed is reported, and the input pair is never
modified. Exits 0 after a verified restore, 1 on a refusal, 2 on a
wrong command line, and 130 on Ctrl-C.
"""

import sys
from pathlib import Path

# resolve the implementation relative to THIS file, so the command
# works from any cwd and through checkout paths containing spaces -
# and keep the checkout clean: no __pycache__ from the import
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cloud_lab import preservation

USAGE = "usage: python3 scripts/restore_lab.py ZIP DEST"


def main(argv=None) -> int:
    """Parse the command line and run the restore."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    zip_path, dest = (Path(arg) for arg in args)
    try:
        preservation.restore_archive(zip_path, dest)
    except preservation.PreservationError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # the module already removed the partial destination (or
        # reported the remnant it could not remove)
        print("interrupted - retry into a fresh destination",
              file=sys.stderr)
        return 130
    print(f"restored {zip_path} into {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
